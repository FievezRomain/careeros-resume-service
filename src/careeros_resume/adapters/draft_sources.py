from typing import Literal
from uuid import UUID

import httpx2
from pydantic import ConfigDict, RootModel, SecretStr, ValidationError

from careeros_resume.application.drafts import SourceInvalid, SourceUnavailable
from careeros_resume.application.versions import Denied, NotFound
from careeros_resume.domain.draft import DraftEntry, SourceAccess
from careeros_resume.domain.version import Contract


class SnapshotItem(Contract):
    model_config = ConfigDict(extra="allow")
    item_id: UUID
    validation_status: Literal["userConfirmed", "unconfirmed"]


class ProfileSnapshot(Contract):
    model_config = ConfigDict(extra="ignore")
    snapshot_id: UUID
    items: tuple[SnapshotItem, ...]


class Opportunity(Contract):
    model_config = ConfigDict(extra="ignore")
    opportunity_id: UUID
    source_access: SourceAccess


class SourceEntry(RootModel[DraftEntry]):
    pass


class HttpDraftSources:
    def __init__(self, client: httpx2.Client, profile_url: str | None, job_url: str | None) -> None:
        self._client = client
        self._profile_url = profile_url
        self._job_url = job_url

    def confirmed_facts(
        self, snapshot_id: UUID, credential: SecretStr, correlation_id: UUID
    ) -> tuple[DraftEntry, ...]:
        raw = self._read(
            self._profile_url, f"/v1/profile/snapshots/{snapshot_id}", credential, correlation_id
        )
        try:
            snapshot = ProfileSnapshot.model_validate_json(raw)
            if snapshot.snapshot_id != snapshot_id or len(
                {item.item_id for item in snapshot.items}
            ) != len(snapshot.items):
                raise SourceInvalid
            return tuple(
                SourceEntry.model_validate({**item.model_dump(), "sourceFactId": item.item_id}).root
                for item in snapshot.items
                if item.validation_status == "userConfirmed"
            )
        except ValidationError:
            raise SourceInvalid from None

    def opportunity_access(
        self, version_id: UUID, credential: SecretStr, correlation_id: UUID
    ) -> SourceAccess:
        raw = self._read(self._job_url, f"/v1/jobs/{version_id}", credential, correlation_id)
        try:
            opportunity = Opportunity.model_validate_json(raw)
        except ValidationError:
            raise SourceInvalid from None
        if opportunity.opportunity_id != version_id:
            raise SourceInvalid
        return opportunity.source_access

    def _read(
        self, base: str | None, path: str, credential: SecretStr, correlation_id: UUID
    ) -> bytearray:
        if base is None:
            raise SourceUnavailable
        try:
            with self._client.stream(
                "GET",
                f"{base.rstrip('/')}{path}",
                follow_redirects=False,
                headers={
                    "Authorization": f"Bearer {credential.get_secret_value()}",
                    "X-Correlation-ID": str(correlation_id),
                },
            ) as response:
                if response.status_code in (401, 403):
                    raise Denied
                if response.status_code == 404:
                    raise NotFound
                if response.status_code >= 500:
                    raise SourceUnavailable
                if response.status_code != 200:
                    raise SourceInvalid
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise SourceInvalid
                return body
        except httpx2.HTTPError:
            raise SourceUnavailable from None
