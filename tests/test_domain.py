from unittest.mock import Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from careeros_resume.application.versions import Actor, Denied, StartResume, Versions
from careeros_resume.domain.version import Artifact, Registration


def test_registration_rejects_conflicting_metadata() -> None:
    key = UUID(int=3)
    snapshot = UUID(int=2)
    pdf = Artifact(format="pdf", object_key=key, sha256="a" * 64, byte_size=10)
    docx = Artifact(format="docx", object_key=key, sha256="b" * 64, byte_size=50)
    with pytest.raises(ValidationError, match="distinct object key"):
        Registration(profile_snapshot_id=snapshot, artifacts=(pdf, docx))
    with pytest.raises(ValidationError, match="distinct format"):
        Registration(profile_snapshot_id=snapshot, artifacts=(pdf, pdf))


def test_scope_denial_never_calls_the_repository() -> None:
    repository = Mock()
    use_cases = Versions(repository)
    account = Actor(account_id=UUID(int=1), tenant_id=UUID(int=2))
    with pytest.raises(Denied):
        use_cases.get(account, UUID(int=3), 1)
    with pytest.raises(Denied):
        use_cases.list(account, UUID(int=3), 0, 20)
    assert repository.mock_calls == []


def test_startup_refuses_an_incomplete_head() -> None:
    runner = Mock()
    runner.verify.return_value = False
    with pytest.raises(RuntimeError, match="expected head"):
        StartResume(runner).execute()
    assert [call[0] for call in runner.mock_calls] == ["apply_pending", "verify"]
