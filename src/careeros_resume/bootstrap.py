import logging
from typing import Literal

import boto3
import httpx2
import jwt
from botocore.config import Config
from fastapi import FastAPI
from pydantic import AnyHttpUrl, Field, FilePath, field_validator
from sqlalchemy import create_engine

from careeros_resume.adapters.artifact_storage import S3Artifacts
from careeros_resume.adapters.documents import DocumentRenderer
from careeros_resume.adapters.draft_sources import HttpDraftSources
from careeros_resume.adapters.http import create_app
from careeros_resume.adapters.oidc import OidcVerifier
from careeros_resume.adapters.postgresql.migrations.runner import DatabaseSettings, SqlMigrations
from careeros_resume.adapters.postgresql.store import SqlVersions
from careeros_resume.application.drafts import GenerateDraft
from careeros_resume.application.exports import ResumeExports
from careeros_resume.application.versions import StartResume, Versions


class Settings(DatabaseSettings):
    oidc_issuer: AnyHttpUrl
    oidc_audience: str = Field(min_length=1)
    oidc_jwks_url: AnyHttpUrl
    profile_api_base_url: AnyHttpUrl | None = None
    job_api_base_url: AnyHttpUrl | None = None
    resume_s3_bucket: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"
    )
    resume_s3_endpoint: AnyHttpUrl | None = None
    resume_s3_region: str = Field(default="us-east-1", min_length=1)
    resume_pdf_font: FilePath | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator(
        "oidc_issuer",
        "oidc_jwks_url",
        "profile_api_base_url",
        "job_api_base_url",
        "resume_s3_endpoint",
    )
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
    versions = Versions(SqlVersions(engine))
    drafts = GenerateDraft(sources)
    s3 = None
    exports = None
    if config.resume_s3_bucket is not None:
        renderer = DocumentRenderer(config.resume_pdf_font)
        s3 = boto3.client(
            "s3",
            region_name=config.resume_s3_region,
            endpoint_url=str(config.resume_s3_endpoint) if config.resume_s3_endpoint else None,
            config=Config(
                connect_timeout=3,
                read_timeout=8,
                s3={"addressing_style": "path"},
                request_checksum_calculation="when_required",
            ),
        )
        exports = ResumeExports(
            drafts, versions, renderer, S3Artifacts(s3, config.resume_s3_bucket)
        )

    def close() -> None:
        try:
            if s3 is not None:
                s3.close()
            client.close()
        finally:
            engine.dispose()

    return create_app(
        versions,
        StartResume(SqlMigrations(engine)),
        verifier,
        close,
        drafts=drafts,
        exports=exports,
    )
