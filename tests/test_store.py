import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from careeros_resume.adapters.postgresql.migrations.runner import SqlMigrations
from careeros_resume.adapters.postgresql.store import SqlVersions
from careeros_resume.application.versions import Actor, NotFound, RegistrationConflict, StartResume
from careeros_resume.domain.version import Artifact, Registration


@pytest.fixture(params=["sqlite", "postgresql"])
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{(tmp_path / 'resume.sqlite').as_posix()}"
    if request.param == "postgresql":
        postgres = os.environ.get("TEST_RESUME_DATABASE_URL")
        if postgres is None:
            pytest.skip("isolated PostgreSQL CI database not configured")
        assert make_url(postgres).database == "careeros_resume_test"
        url = postgres
    database = create_engine(url)
    try:
        yield database
    finally:
        SqlMigrations(database).downgrade()
        database.dispose()


def test_migration_applies_verifies_and_rolls_back(engine: Engine) -> None:
    runner = SqlMigrations(engine)
    assert not runner.verify()
    StartResume(runner).execute()
    StartResume(runner).execute()
    assert runner.verify()
    runner.downgrade()
    assert not runner.verify()


def test_concurrent_registrations_and_replays_are_atomic(engine: Engine) -> None:
    StartResume(SqlMigrations(engine)).execute()
    store = SqlVersions(engine)
    actor = Actor(account_id=UUID(int=1), tenant_id=UUID(int=2))
    resume = UUID(int=3)
    payload = Registration(
        profile_snapshot_id=UUID(int=4),
        artifacts=(Artifact(format="pdf", object_key=UUID(int=5), sha256="a" * 64, byte_size=10),),
    )
    keys = [UUID(int=6), UUID(int=6), UUID(int=7), UUID(int=7)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda key: store.register(actor, resume, key, payload), keys))
    assert sum(created for _, created in results) == 2
    assert results[0][0] == results[1][0] and results[2][0] == results[3][0]
    assert [version.version for version in store.list(actor, resume, 0, 20).items] == [1, 2]
    with pytest.raises(RegistrationConflict):
        store.register(
            actor, resume, keys[0], payload.model_copy(update={"profile_snapshot_id": UUID(int=9)})
        )
    with pytest.raises(NotFound):
        store.get(Actor(account_id=actor.account_id, tenant_id=UUID(int=99)), resume, 1)
