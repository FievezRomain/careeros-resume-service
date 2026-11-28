import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Path, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, SecretStr
from starlette.middleware.base import RequestResponseEndpoint

from careeros_resume.application.drafts import (
    DraftInputInvalid,
    GenerateDraft,
    SourceInvalid,
    SourceUnavailable,
)
from careeros_resume.application.exports import MEDIA_TYPES, ResumeExports
from careeros_resume.application.versions import (
    Actor,
    Denied,
    NotFound,
    RegistrationConflict,
    StartResume,
    Versions,
)
from careeros_resume.domain.draft import DraftRequest, ResumeDraft
from careeros_resume.domain.version import (
    ArtifactFormat,
    Contract,
    Registration,
    Version,
    VersionPage,
)

log = logging.getLogger("careeros.resume")
bearer = HTTPBearer(auto_error=False)
Number = Annotated[int, Path(ge=1, le=2**31 - 1)]
Cursor = Annotated[int, Query(ge=0, le=2**31 - 1)]
Limit = Annotated[int, Query(ge=1, le=100)]


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Actor: ...


class Health(BaseModel):
    status: Literal["alive", "ready"]


class Problem(Contract):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    correlation_id: UUID


details = {
    401: "A valid bearer token is required.",
    403: "The required resume scope is missing.",
    404: "The requested resource is unavailable to the caller.",
    409: "The registration key is already associated with different metadata.",
    422: "The resume request is invalid or its selected sources are ineligible.",
    502: "A resume dependency returned invalid data or an artifact failed its integrity check.",
    503: "A required resume dependency is temporarily unavailable.",
}


def error_docs(*statuses: int) -> dict[int | str, dict[str, Any]]:
    return {
        status: {
            "description": details[status],
            "content": {"application/problem+json": {"schema": Problem.model_json_schema()}},
        }
        for status in statuses
    }


def problem(request: Request, status: int, headers: dict[str, str] | None = None) -> Response:
    body = Problem(
        title=HTTPStatus(status).phrase,
        status=status,
        detail=details.get(status, "The request could not be completed."),
        correlation_id=request.state.correlation_id,
    )
    return JSONResponse(
        body.model_dump(mode="json", by_alias=True),
        status_code=status,
        headers=headers,
        media_type="application/problem+json",
    )


def create_app(
    versions: Versions,
    startup: StartResume,
    verifier: TokenVerifier,
    close: Callable[[], None] | None = None,
    *,
    drafts: GenerateDraft | None = None,
    exports: ResumeExports | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            startup.execute()
            yield
        finally:
            if close is not None:
                close()

    app = FastAPI(title="CareerOS Resume Service", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def correlate(request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            identifier = UUID(request.headers.get("X-Correlation-ID", ""))
        except ValueError:
            identifier = uuid4()
        request.state.correlation_id = identifier
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = str(identifier)
        response.headers["Cache-Control"] = "no-store"
        log.info(json.dumps({"correlationId": str(identifier), "status": response.status_code}))
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        return problem(request, exc.status_code, dict(exc.headers) if exc.headers else None)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, _: RequestValidationError) -> Response:
        return problem(request, 422)

    @app.exception_handler(Denied)
    async def denied(request: Request, _: Denied) -> Response:
        return problem(request, 403)

    @app.exception_handler(NotFound)
    async def not_found(request: Request, _: NotFound) -> Response:
        return problem(request, 404)

    @app.exception_handler(RegistrationConflict)
    async def conflict(request: Request, _: RegistrationConflict) -> Response:
        return problem(request, 409)

    @app.exception_handler(DraftInputInvalid)
    async def invalid_draft(request: Request, _: DraftInputInvalid) -> Response:
        return problem(request, 422)

    @app.exception_handler(SourceInvalid)
    async def invalid_source(request: Request, _: SourceInvalid) -> Response:
        return problem(request, 502)

    @app.exception_handler(SourceUnavailable)
    async def unavailable_source(request: Request, _: SourceUnavailable) -> Response:
        return problem(request, 503)

    def authenticate_session(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    ) -> tuple[Actor, SecretStr]:
        try:
            if credentials is not None:
                return verifier.verify(credentials.credentials), SecretStr(credentials.credentials)
        except ValueError:
            pass
        raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})

    def authenticate(
        session: Annotated[tuple[Actor, SecretStr], Security(authenticate_session)],
    ) -> Actor:
        return session[0]

    @app.get("/health/live")
    def live() -> Health:
        return Health(status="alive")

    @app.get("/health/ready")
    def ready() -> Health:
        return Health(status="ready")

    @app.post("/v1/resume-drafts", responses=error_docs(401, 403, 404, 422, 502, 503))
    def generate_draft(
        body: DraftRequest,
        request: Request,
        session: Annotated[tuple[Actor, SecretStr], Security(authenticate_session)],
    ) -> ResumeDraft:
        if drafts is None:
            raise HTTPException(503)
        actor, credential = session
        return drafts.execute(actor, credential, body, request.state.correlation_id)

    @app.post(
        "/v1/resumes/{resume_id}/exports",
        status_code=201,
        responses={
            200: {"model": Version, "description": "Idempotent replay"},
            **error_docs(401, 403, 404, 409, 422, 502, 503),
        },
    )
    def export_resume(
        resume_id: UUID,
        body: DraftRequest,
        request: Request,
        response: Response,
        key: Annotated[UUID, Header(alias="Idempotency-Key")],
        session: Annotated[tuple[Actor, SecretStr], Security(authenticate_session)],
    ) -> Version:
        if exports is None:
            raise HTTPException(503)
        actor, credential = session
        version, created = exports.create(
            actor, credential, resume_id, key, body, request.state.correlation_id
        )
        response.status_code = 201 if created else 200
        response.headers["Location"] = f"/v1/resumes/{resume_id}/versions/{version.version}"
        return version

    @app.get(
        "/v1/resumes/{resume_id}/versions/{number}/artifacts/{format}",
        response_class=Response,
        responses={
            200: {
                "content": {
                    media_type: {"schema": {"type": "string", "format": "binary"}}
                    for media_type in MEDIA_TYPES.values()
                }
            },
            **error_docs(401, 403, 404, 422, 502, 503),
        },
    )
    def download_artifact(
        resume_id: UUID,
        number: Number,
        format: ArtifactFormat,
        actor: Annotated[Actor, Security(authenticate)],
    ) -> Response:
        if exports is None:
            raise HTTPException(503)
        data = exports.download(actor, resume_id, number, format)
        return Response(
            data,
            media_type=MEDIA_TYPES[format],
            headers={"Content-Disposition": f'attachment; filename="resume.{format}"'},
        )

    @app.post(
        "/v1/resumes/{resume_id}/versions",
        status_code=201,
        responses={
            200: {"model": Version, "description": "Idempotent replay"},
            **error_docs(401, 403, 409, 422),
        },
    )
    def register(
        resume_id: UUID,
        body: Registration,
        response: Response,
        key: Annotated[UUID, Header(alias="Idempotency-Key")],
        actor: Annotated[Actor, Security(authenticate)],
    ) -> Version:
        version, created = versions.register(actor, resume_id, key, body)
        response.status_code = 201 if created else 200
        response.headers["Location"] = f"/v1/resumes/{resume_id}/versions/{version.version}"
        return version

    @app.get("/v1/resumes/{resume_id}/versions/{number}", responses=error_docs(401, 403, 404, 422))
    def get_version(
        resume_id: UUID, number: Number, actor: Annotated[Actor, Security(authenticate)]
    ) -> Version:
        return versions.get(actor, resume_id, number)

    @app.get("/v1/resumes/{resume_id}/versions", responses=error_docs(401, 403, 404, 422))
    def list_versions(
        resume_id: UUID,
        actor: Annotated[Actor, Security(authenticate)],
        after: Cursor = 0,
        limit: Limit = 20,
    ) -> VersionPage:
        return versions.list(actor, resume_id, after, limit)

    return app
