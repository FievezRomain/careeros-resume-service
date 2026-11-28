from hashlib import sha256
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import SecretStr

from careeros_resume.application.drafts import DraftInputInvalid, GenerateDraft, SourceInvalid
from careeros_resume.application.versions import Actor, Denied, NotFound, Versions
from careeros_resume.domain.draft import DraftRequest, ResumeDraft
from careeros_resume.domain.version import Artifact, ArtifactFormat, Contract, Registration, Version

EXPORT_NAMESPACE = uuid5(NAMESPACE_URL, "https://careeros.dev/resume-exports/v1")
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MEDIA_TYPES: dict[ArtifactFormat, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class RenderedDocument(Contract):
    format: ArtifactFormat
    data: bytes


class Renderer(Protocol):
    def render(self, draft: ResumeDraft) -> tuple[RenderedDocument, ...]: ...


class ArtifactStore(Protocol):
    def write(self, scope: UUID, artifact: Artifact, data: bytes, fingerprint: str) -> None: ...

    def read(self, scope: UUID, artifact: Artifact) -> bytes: ...


def _scope(actor: Actor, resume_id: UUID) -> UUID:
    return uuid5(EXPORT_NAMESPACE, f"{actor.tenant_id}/{actor.account_id}/{resume_id}")


class ResumeExports:
    def __init__(
        self,
        drafts: GenerateDraft,
        versions: Versions,
        renderer: Renderer,
        artifacts: ArtifactStore,
    ) -> None:
        self._drafts = drafts
        self._versions = versions
        self._renderer = renderer
        self._artifacts = artifacts

    def create(
        self,
        actor: Actor,
        credential: SecretStr,
        resume_id: UUID,
        key: UUID,
        request: DraftRequest,
        correlation_id: UUID,
    ) -> tuple[Version, bool]:
        if "resume:versions:write" not in actor.scopes:
            raise Denied
        draft = self._drafts.execute(actor, credential, request, correlation_id)
        documents = self._renderer.render(draft)
        if tuple(document.format for document in documents) != ("pdf", "docx"):
            raise SourceInvalid
        if any(
            len(document.data) > MAX_ARTIFACT_BYTES or not document.data for document in documents
        ):
            raise DraftInputInvalid
        scope = _scope(actor, resume_id)
        descriptors = tuple(
            Artifact(
                format=document.format,
                object_key=uuid5(scope, f"{key}/{document.format}"),
                sha256=sha256(document.data).hexdigest(),
                byte_size=len(document.data),
            )
            for document in documents
        )
        registration = Registration(
            profile_snapshot_id=draft.profile_snapshot_id,
            opportunity_version_id=draft.opportunity_version_id,
            artifacts=descriptors,
        )
        fingerprint = sha256(registration.model_dump_json().encode()).hexdigest()
        for artifact, document in zip(descriptors, documents, strict=True):
            self._artifacts.write(scope, artifact, document.data, fingerprint)
        return self._versions.register(
            actor, resume_id, uuid5(scope, f"export/{key}"), registration
        )

    def download(self, actor: Actor, resume_id: UUID, number: int, format: ArtifactFormat) -> bytes:
        version = self._versions.get(actor, resume_id, number)
        artifact = next((item for item in version.artifacts if item.format == format), None)
        if artifact is None:
            raise NotFound
        if artifact.byte_size > MAX_ARTIFACT_BYTES:
            raise SourceInvalid
        data = self._artifacts.read(_scope(actor, resume_id), artifact)
        if len(data) != artifact.byte_size or sha256(data).hexdigest() != artifact.sha256:
            raise SourceInvalid
        return data
