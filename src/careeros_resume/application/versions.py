from typing import Protocol
from uuid import UUID

from pydantic import Field

from careeros_resume.domain.version import Contract, Registration, Version, VersionPage


class Actor(Contract):
    account_id: UUID
    tenant_id: UUID
    scopes: tuple[str, ...] = Field(default=())


class Denied(Exception):
    pass


class NotFound(Exception):
    pass


class RegistrationConflict(Exception):
    pass


class VersionRepository(Protocol):
    def register(
        self, actor: Actor, resume_id: UUID, key: UUID, request: Registration
    ) -> tuple[Version, bool]: ...

    def get(self, actor: Actor, resume_id: UUID, number: int) -> Version: ...

    def list(self, actor: Actor, resume_id: UUID, after: int, limit: int) -> VersionPage: ...


class Versions:
    def __init__(self, repository: VersionRepository) -> None:
        self._repository = repository

    def register(
        self, actor: Actor, resume_id: UUID, key: UUID, request: Registration
    ) -> tuple[Version, bool]:
        if "resume:versions:write" not in actor.scopes:
            raise Denied
        return self._repository.register(actor, resume_id, key, request)

    def get(self, actor: Actor, resume_id: UUID, number: int) -> Version:
        if "resume:versions:read" not in actor.scopes:
            raise Denied
        return self._repository.get(actor, resume_id, number)

    def list(self, actor: Actor, resume_id: UUID, after: int, limit: int) -> VersionPage:
        if "resume:versions:read" not in actor.scopes:
            raise Denied
        return self._repository.list(actor, resume_id, after, limit)


class MigrationRunner(Protocol):
    def apply_pending(self) -> None: ...

    def verify(self) -> bool: ...


class StartResume:
    def __init__(self, runner: MigrationRunner) -> None:
        self._runner = runner

    def execute(self) -> None:
        self._runner.apply_pending()
        if not self._runner.verify():
            raise RuntimeError("resume schema is not at its expected head")
