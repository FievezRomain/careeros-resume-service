from typing import Protocol
from uuid import UUID

from pydantic import SecretStr

from careeros_resume.application.versions import Actor, Denied
from careeros_resume.domain.draft import DraftEntry, DraftRequest, ResumeDraft, SourceAccess


class DraftInputInvalid(Exception):
    pass


class SourceInvalid(Exception):
    pass


class SourceUnavailable(Exception):
    pass


class DraftSources(Protocol):
    def confirmed_facts(
        self, snapshot_id: UUID, credential: SecretStr, correlation_id: UUID
    ) -> tuple[DraftEntry, ...]: ...

    def opportunity_access(
        self, version_id: UUID, credential: SecretStr, correlation_id: UUID
    ) -> SourceAccess: ...


class GenerateDraft:
    def __init__(self, sources: DraftSources) -> None:
        self._sources = sources

    def execute(
        self, actor: Actor, credential: SecretStr, request: DraftRequest, correlation_id: UUID
    ) -> ResumeDraft:
        required = {"resume:drafts:generate", "profile:read"}
        target = request.opportunity_version_id
        if target is not None:
            required.add("jobs:read")
        if not required.issubset(actor.scopes):
            raise Denied

        facts = self._sources.confirmed_facts(
            request.profile_snapshot_id, credential, correlation_id
        )
        if request.fact_ids is not None:
            selected = set(request.fact_ids)
            facts = tuple(fact for fact in facts if fact.source_fact_id in selected)
            if len(facts) != len(selected):
                raise DraftInputInvalid
        if not facts:
            raise DraftInputInvalid
        if (
            target is not None
            and self._sources.opportunity_access(target, credential, correlation_id) != "full_text"
        ):
            raise DraftInputInvalid
        return ResumeDraft(
            profile_snapshot_id=request.profile_snapshot_id,
            opportunity_version_id=target,
            entries=facts,
        )
