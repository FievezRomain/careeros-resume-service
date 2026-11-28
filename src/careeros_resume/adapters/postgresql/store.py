from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine, Table, and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql.elements import ColumnElement

from careeros_resume.adapters.postgresql.migrations.runner import documents, versions
from careeros_resume.application.versions import Actor, NotFound, RegistrationConflict
from careeros_resume.domain.version import Registration, Version, VersionPage


def _scope(table: Table, owner: dict[str, str]) -> ColumnElement[bool]:
    return and_(*(table.c[field] == identifier for field, identifier in owner.items()))


def _owner(actor: Actor, resume_id: UUID) -> dict[str, str]:
    return {
        "tenant_id": str(actor.tenant_id),
        "account_id": str(actor.account_id),
        "resume_id": str(resume_id),
    }


class SqlVersions:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def register(
        self, actor: Actor, resume_id: UUID, key: UUID, request: Registration
    ) -> tuple[Version, bool]:
        owner = _owner(actor, resume_id)
        owned = _scope(versions, owner)
        with self._engine.begin() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            connection.execute(insert(documents).values(**owner).on_conflict_do_nothing())
            connection.execute(
                select(documents.c.resume_id).where(_scope(documents, owner)).with_for_update()
            ).scalar_one()
            previous: str | None = connection.scalar(
                select(versions.c.payload).where(owned, versions.c.request_key == str(key))
            )
            if previous is not None:
                current = Version.model_validate_json(previous)
                if (
                    current.model_dump(include=set(Registration.model_fields))
                    != request.model_dump()
                ):
                    raise RegistrationConflict
                return current, False

            latest: int | None = connection.scalar(
                select(versions.c.number).where(owned).order_by(versions.c.number.desc()).limit(1)
            )
            version = Version(
                **request.model_dump(),
                resume_id=resume_id,
                version=(latest or 0) + 1,
                created_at=datetime.now(UTC),
            )
            connection.execute(
                versions.insert().values(
                    **owner,
                    number=version.version,
                    request_key=str(key),
                    payload=version.model_dump_json(),
                )
            )
            return version, True

    def get(self, actor: Actor, resume_id: UUID, number: int) -> Version:
        with self._engine.connect() as connection:
            payload: str | None = connection.scalar(
                select(versions.c.payload).where(
                    _scope(versions, _owner(actor, resume_id)), versions.c.number == number
                )
            )
        if payload is None:
            raise NotFound
        return Version.model_validate_json(payload)

    def list(self, actor: Actor, resume_id: UUID, after: int, limit: int) -> VersionPage:
        owner = _owner(actor, resume_id)
        with self._engine.connect() as connection:
            if not connection.scalar(select(documents.c.resume_id).where(_scope(documents, owner))):
                raise NotFound
            payloads: tuple[str, ...] = tuple(
                connection.scalars(
                    select(versions.c.payload)
                    .where(_scope(versions, owner), versions.c.number > after)
                    .order_by(versions.c.number)
                    .limit(limit + 1)
                )
            )
        items = tuple(Version.model_validate_json(payload) for payload in payloads[:limit])
        return VersionPage(
            items=items, next_after=items[-1].version if len(payloads) > limit else None
        )
