import logging
from typing import Literal

import httpx2
import jwt
from fastapi import FastAPI
from pydantic import AnyHttpUrl, Field, field_validator
from sqlalchemy import create_engine

from careeros_resume.adapters.draft_sources import HttpDraftSources
from careeros_resume.adapters.http import create_app
from careeros_resume.adapters.oidc import OidcVerifier
from careeros_resume.adapters.postgresql.migrations.runner import DatabaseSettings, SqlMigrations
from careeros_resume.adapters.postgresql.store import SqlVersions
from careeros_resume.application.drafts import GenerateDraft
from careeros_resume.application.versions import StartResume, Versions


class Settings(DatabaseSettings):
    oidc_issuer: AnyHttpUrl
    oidc_audience: str = Field(min_length=1)
    oidc_jwks_url: AnyHttpUrl
    profile_api_base_url: AnyHttpUrl | None = None
    job_api_base_url: AnyHttpUrl | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("oidc_issuer", "oidc_jwks_url", "profile_api_base_url", "job_api_base_url")
    @classmethod
    def trusted_address(cls, url: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if url is None:
            return None
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("service URLs cannot contain credentials, queries or fragments")
        if url.scheme != "https" and url.host not in {"localhost", "127.0.0.1", "[::1]"}:
            raise ValueError("service URLs require HTTPS outside local development")
        return url


def create() -> FastAPI:
    config = Settings()
    logger = logging.getLogger("careeros.resume")
    logger.setLevel(config.log_level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
    engine = create_engine(config.resume_database_url.get_secret_value(), pool_pre_ping=True)
    verifier = OidcVerifier(
        jwt.PyJWKClient(str(config.oidc_jwks_url), timeout=5),
        str(config.oidc_issuer).rstrip("/"),
        config.oidc_audience,
    )
    client = httpx2.Client(timeout=5)
    sources = HttpDraftSources(
        client,
        str(config.profile_api_base_url) if config.profile_api_base_url else None,
        str(config.job_api_base_url) if config.job_api_base_url else None,
    )

    def close() -> None:
        try:
            client.close()
        finally:
            engine.dispose()

    return create_app(
        Versions(SqlVersions(engine)),
        StartResume(SqlMigrations(engine)),
        verifier,
        close,
        drafts=GenerateDraft(sources),
    )
