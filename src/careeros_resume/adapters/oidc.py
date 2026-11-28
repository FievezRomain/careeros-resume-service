import jwt
from pydantic import ValidationError

from careeros_resume.application.versions import Actor


class OidcVerifier:
    def __init__(self, jwks: jwt.PyJWKClient, issuer: str, audience: str) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._audience = audience

    def verify(self, token: str) -> Actor:
        try:
            claims = jwt.decode(
                token,
                self._jwks.get_signing_key_from_jwt(token),
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iat", "sub", "tenant_id"]},
            )
            scope = claims.get("scope", "")
            return Actor.model_validate(
                {
                    "account_id": claims["sub"],
                    "tenant_id": claims["tenant_id"],
                    "scopes": scope.split() if isinstance(scope, str) else [],
                }
            )
        except (jwt.PyJWTError, ValidationError):
            raise ValueError("invalid bearer token") from None
