import logging
from typing import Literal

import jwt
from fastapi import FastAPI
from pydantic import AnyHttpUrl, Field, field_validator
from sqlalchemy import create_engine

from careeros_resume.adapters.http import create_app
from careeros_resume.adapters.oidc import OidcVerifier
from careeros_resume.adapters.postgresql.migrations.runner import DatabaseSettings, SqlMigrations
from careeros_resume.adapters.postgresql.store import SqlVersions
from careeros_resume.application.versions import StartResume, Versions


class Settings(DatabaseSettings):
    oidc_issuer: AnyHttpUrl
    oidc_audience: str = Field(min_length=1)
    oidc_jwks_url: AnyHttpUrl
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("oidc_issuer", "oidc_jwks_url")
    @classmethod
    def trusted_address(cls, url: AnyHttpUrl) -> AnyHttpUrl:
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("OIDC URLs cannot contain credentials, queries or fragments")
        if url.scheme != "https" and url.host not in {"localhost", "127.0.0.1", "[::1]"}:
            raise ValueError("OIDC URLs require HTTPS outside local development")
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
    return create_app(
        Versions(SqlVersions(engine)), StartResume(SqlMigrations(engine)), verifier, engine.dispose
    )
