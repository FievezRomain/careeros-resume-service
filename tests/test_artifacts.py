from hashlib import sha256
from io import BytesIO
from os import environ
from uuid import UUID, uuid4

import boto3
import pytest
from botocore.config import Config
from pypdf import PdfReader

from careeros_resume.adapters.artifact_storage import S3Artifacts
from careeros_resume.adapters.documents import DocumentRenderer
from careeros_resume.application.drafts import DraftInputInvalid
from careeros_resume.application.versions import NotFound, RegistrationConflict
from careeros_resume.domain.draft import ResumeDraft, Skill
from careeros_resume.domain.version import Artifact


def test_template_paginates_and_preserves_the_last_fact() -> None:
    draft = ResumeDraft(
        profile_snapshot_id=UUID(int=1),
        opportunity_version_id=None,
        entries=tuple(
            Skill(kind="skill", source_fact_id=UUID(int=number), name=f"Example skill {number}")
            for number in range(1, 95)
        ),
    )
    pdf, _ = DocumentRenderer().render(draft)
    pages = PdfReader(BytesIO(pdf.data)).pages
    assert len(pages) > 1
    assert "Example skill 94" in pages[-1].extract_text()


def test_template_rejects_text_that_the_selected_font_cannot_preserve() -> None:
    draft = ResumeDraft(
        profile_snapshot_id=UUID(int=1),
        opportunity_version_id=None,
        entries=(Skill(kind="skill", source_fact_id=UUID(int=2), name="汉"),),
    )
    with pytest.raises(DraftInputInvalid):
        DocumentRenderer().render(draft)


def test_seaweedfs_immutable_write_and_scoped_read() -> None:
    endpoint = environ.get("TEST_RESUME_S3_ENDPOINT")
    if endpoint is None:
        pytest.skip("isolated SeaweedFS CI endpoint not configured")
    assert endpoint == "http://127.0.0.1:8333"
    scope, other_scope = uuid4(), uuid4()
    payload = b"synthetic S3 contract"
    artifact = Artifact(
        format="pdf",
        object_key=uuid4(),
        sha256=sha256(payload).hexdigest(),
        byte_size=len(payload),
    )
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="fixture-access",
        aws_secret_access_key="fixture-secret",
        config=Config(
            s3={"addressing_style": "path"}, request_checksum_calculation="when_required"
        ),
    )
    try:
        adapter = S3Artifacts(s3, "careeros-resumes-test")
        adapter.write(scope, artifact, payload, "original")
        adapter.write(scope, artifact, payload, "original")
        with pytest.raises(RegistrationConflict):
            adapter.write(scope, artifact, b"conflicting body", "conflict")
        assert adapter.read(scope, artifact) == payload
        with pytest.raises(NotFound):
            adapter.read(other_scope, artifact)
    finally:
        s3.close()
