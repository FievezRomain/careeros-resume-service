from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

ArtifactFormat = Literal["pdf", "docx"]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, populate_by_name=True, alias_generator=to_camel
    )


class Artifact(Contract):
    format: ArtifactFormat
    object_key: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(strict=True, gt=0, le=2**63 - 1)


class Registration(Contract):
    profile_snapshot_id: UUID
    opportunity_version_id: UUID | None = None
    artifacts: Annotated[tuple[Artifact, ...], Field(min_length=1, max_length=2)]

    @model_validator(mode="after")
    def distinct_artifacts(self) -> Self:
        if len({item.format for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("each artifact must use a distinct format")
        if len({item.object_key for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("each artifact must use a distinct object key")
        return self


class Version(Registration):
    resume_id: UUID
    version: int = Field(strict=True, gt=0)
    created_at: AwareDatetime


class VersionPage(Contract):
    items: tuple[Version, ...]
    next_after: int | None = None
