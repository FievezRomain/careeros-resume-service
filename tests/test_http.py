import logging
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from careeros_resume.adapters.http import create_app
from careeros_resume.adapters.postgresql.migrations.runner import SqlMigrations
from careeros_resume.adapters.postgresql.store import SqlVersions
from careeros_resume.application.versions import Actor, StartResume, Versions


class FixtureVerifier:
    def verify(self, token: str) -> Actor:
        if token not in {"synthetic-owner", "synthetic-outsider"}:
            raise ValueError("invalid token")
        return Actor(
            account_id=UUID(int=1 if token == "synthetic-owner" else 2),
            tenant_id=UUID(int=10),
            scopes=("resume:versions:read", "resume:versions:write"),
        )


def test_http_contract_and_redacted_request_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'http.sqlite').as_posix()}")
    app = create_app(
        Versions(SqlVersions(engine)),
        StartResume(SqlMigrations(engine)),
        FixtureVerifier(),
        engine.dispose,
    )
    headers = {"Authorization": "Bearer synthetic-owner", "Idempotency-Key": str(UUID(int=5))}
    path = f"/v1/resumes/{UUID(int=3)}/versions"
    command = {
        "profileSnapshotId": str(UUID(int=4)),
        "artifacts": [
            {"format": "pdf", "objectKey": str(UUID(int=7)), "sha256": "a" * 64, "byteSize": 15}
        ],
    }
    with caplog.at_level(logging.INFO, logger="careeros.resume"), TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        registered = client.post(path, headers=headers, json=command)
        assert registered.status_code == 201
        assert registered.headers["location"] == f"{path}/1"
        assert client.get(f"{path}/1", headers=headers).json() == registered.json()
        outsider = client.get(f"{path}/1", headers={"Authorization": "Bearer synthetic-outsider"})
        assert outsider.status_code == 404
        assert str(UUID(int=4)) not in outsider.text
        denied = client.get(path, headers={"Authorization": "Bearer invalid-token"})
        assert denied.status_code == 401 and denied.headers["www-authenticate"] == "Bearer"
        bad = client.post(path, headers=headers, json={**command, "comment": "private content"})
        assert bad.status_code == 422 and "private content" not in bad.text
        assert bad.headers["content-type"] == "application/problem+json"
        assert bad.json()["correlationId"] == bad.headers["x-correlation-id"]
        assert bad.headers["cache-control"] == "no-store"
        contract = app.openapi()["paths"]["/v1/resumes/{resume_id}/versions"]["post"]
        assert "application/problem+json" in contract["responses"]["422"]["content"]
    assert bad.headers["x-correlation-id"] in caplog.text
    for private in ("synthetic-owner", "private content", str(UUID(int=3))):
        assert private not in caplog.text


def test_http_never_starts_if_the_migrations_are_incomplete() -> None:
    migrations = Mock()
    migrations.verify.return_value = False
    app = create_app(Versions(Mock()), StartResume(migrations), FixtureVerifier())
    with pytest.raises(RuntimeError, match="expected head"), TestClient(app):
        pytest.fail("service accepted traffic before verifying its schema")
