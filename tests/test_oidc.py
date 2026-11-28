from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from careeros_resume.adapters.oidc import OidcVerifier


def test_oidc_verifies_signature_audience_and_tenant() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = Mock()
    jwks.get_signing_key_from_jwt.return_value = jwt.PyJWK.from_json(
        jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())
    )
    issuer = "https://identity.example.invalid/realms/example"
    verifier = OidcVerifier(jwks, issuer, "example-api")
    now = datetime.now(UTC)
    claims = {
        "iss": issuer,
        "aud": "example-api",
        "sub": str(UUID(int=4)),
        "tenant_id": str(UUID(int=5)),
        "iat": now,
        "exp": now + timedelta(minutes=1),
        "scope": "resume:versions:read",
    }
    actor = verifier.verify(jwt.encode(claims, key, algorithm="RS256"))
    assert actor.account_id == UUID(int=4) and actor.tenant_id == UUID(int=5)
    assert actor.scopes == ("resume:versions:read",)
    for invalid in (
        {**claims, "aud": "other-api"},
        {**claims, "tenant_id": "unrecognized"},
        {**claims, "exp": now - timedelta(minutes=1)},
    ):
        with pytest.raises(ValueError, match="invalid bearer token"):
            verifier.verify(jwt.encode(invalid, key, algorithm="RS256"))
