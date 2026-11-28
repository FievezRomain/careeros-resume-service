from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, StrictBool, model_validator

from careeros_resume.domain.version import Contract


class DraftRequest(Contract):
    profile_snapshot_id: UUID
    opportunity_version_id: UUID | None = None
    fact_ids: tuple[UUID, ...] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def unique_facts(self) -> Self:
        if self.fact_ids is not None and len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("factIds must be unique")
        return self


class Entry(Contract):
    model_config = ConfigDict(extra="ignore")
    source_fact_id: UUID


class DatedEntry(Entry):
    start_date: date
    end_date: date | None = None
    current: StrictBool

    @model_validator(mode="after")
    def valid_dates(self) -> Self:
        if self.end_date is not None and (self.current or self.start_date > self.end_date):
            raise ValueError("invalid date range")
        return self


class Experience(DatedEntry):
    kind: Literal["experience"]
    title: str = Field(min_length=1, max_length=200)
    employer: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class Education(DatedEntry):
    kind: Literal["education"]
    institution: str = Field(min_length=1, max_length=200)
    qualification: str = Field(min_length=1, max_length=200)
    field_of_study: str | None = Field(default=None, max_length=200)


class Skill(Entry):
    kind: Literal["skill"]
    name: str = Field(min_length=1, max_length=120)
    level: Literal["beginner", "intermediate", "advanced", "expert"] | None = None
    level_source: Literal["selfDeclared"] | None = None

    @model_validator(mode="after")
    def valid_level(self) -> Self:
        if (self.level is None) != (self.level_source is None):
            raise ValueError("skill level requires self-declared provenance")
        return self


DraftEntry = Annotated[Experience | Education | Skill, Field(discriminator="kind")]
SourceAccess = Literal["full_text", "external_to_verify"]


class ResumeDraft(Contract):
    profile_snapshot_id: UUID
    opportunity_version_id: UUID | None
    entries: tuple[DraftEntry, ...] = Field(min_length=1)
