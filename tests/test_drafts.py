from unittest.mock import Mock
from uuid import UUID

import httpx2
import pytest
from fastapi.testclient import TestClient

from careeros_resume.adapters.draft_sources import HttpDraftSources
from careeros_resume.adapters.http import create_app
from careeros_resume.application.drafts import GenerateDraft
from careeros_resume.application.versions import Actor, StartResume, Versions

snapshot = UUID(int=30)
education = {
    "itemId": str(UUID(int=16)),
    "validationStatus": "userConfirmed",
    "kind": "education",
    "institution": "Example School",
    "qualification": "Example Diploma",
    "fieldOfStudy": "Systems",
    "startDate": "2018-01-01",
    "endDate": "2020-01-01",
    "current": False,
}


@pytest.mark.parametrize(
    ("upstream_status", "payload", "status"),
    [
        (200, {"snapshotId": str(snapshot), "items": [education]}, 200),
        (200, {"snapshotId": str(snapshot), "items": []}, 422),
        (200, {"snapshotId": str(UUID(int=2)), "items": [education]}, 502),
        (200, {"snapshotId": str(snapshot), "items": [education, education]}, 502),
        (200, {"snapshotId": str(snapshot), "items": [{**education, "current": True}]}, 502),
        (302, {}, 502),
        (503, {}, 503),
    ],
)
def test_draft_checks_profile_contract(
    upstream_status: int, payload: dict[str, object], status: int
) -> None:
    actor = Actor(
        account_id=UUID(int=1),
        tenant_id=UUID(int=2),
        scopes=("resume:drafts:generate", "profile:read"),
    )
    verifier = Mock()
    verifier.verify.return_value = actor

    def upstream(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "profiles.example.invalid"
        return httpx2.Response(
            upstream_status,
            json=payload,
            headers={"Location": "https://untrusted.example.invalid/"}
            if upstream_status == 302
            else {},
        )

    with httpx2.Client(
        transport=httpx2.MockTransport(upstream), follow_redirects=True
    ) as transport:
        drafts = GenerateDraft(
            HttpDraftSources(transport, "https://profiles.example.invalid", None)
        )
        app = create_app(Versions(Mock()), StartResume(Mock()), verifier, drafts=drafts)
        with TestClient(app) as client:
            response = client.post(
                "/v1/resume-drafts",
                headers={"Authorization": "Bearer synthetic-session"},
                json={"profileSnapshotId": str(snapshot)},
            )
        assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
        if status == 200:
            content = {
                key: value
                for key, value in education.items()
                if key not in {"itemId", "validationStatus"}
            }
            assert response.json()["entries"] == [{**content, "sourceFactId": education["itemId"]}]
        else:
            assert response.headers["content-type"] == "application/problem+json"
            assert "Example School" not in response.text
