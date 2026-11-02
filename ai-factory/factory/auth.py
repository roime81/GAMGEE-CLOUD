import hmac
import re

import jwt
from jwt import PyJWKClient

from factory.config import Settings

_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384"]


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Authenticator:
    """Resolves the caller's tenant.

    - OIDC JWT (when OIDC_ISSUER and OIDC_AUDIENCE are set): signature, issuer, audience and
      expiry are verified; the tenant comes from the token claim and the header is ignored.
    - Shared service token (GAMGEE_API_TOKEN): trusted infrastructure only; the tenant comes
      from X-Gamgee-Tenant.
    """

    def __init__(self, settings: Settings, jwks_client: PyJWKClient | None = None):
        self._s = settings
        self.oidc_enabled = bool(settings.oidc_issuer and settings.oidc_audience)
        self._jwks = jwks_client
        if self.oidc_enabled and self._jwks is None:
            url = settings.oidc_jwks_url or f"{settings.oidc_issuer.rstrip('/')}/jwks/"
            self._jwks = PyJWKClient(url, cache_keys=True, timeout=10)

    @property
    def configured(self) -> bool:
        return bool(self._s.api_token) or self.oidc_enabled

    def authenticate(self, bearer: str | None, header_tenant: str) -> str:
        if not self.configured:
            raise AuthError(503, "The AI Factory is not configured.")
        if not bearer:
            raise AuthError(401, "A valid bearer token is required.")
        if self._s.api_token and hmac.compare_digest(bearer, self._s.api_token):
            if not _TENANT.fullmatch(header_tenant):
                raise AuthError(422, "X-Gamgee-Tenant must be a lowercase DNS label.")
            return header_tenant
        if self.oidc_enabled and bearer.count(".") == 2:
            return self._verify_jwt(bearer)
        raise AuthError(401, "A valid bearer token is required.")

    def _verify_jwt(self, token: str) -> str:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=_ALGORITHMS,
                audience=self._s.oidc_audience,
                issuer=self._s.oidc_issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError:
            raise AuthError(401, "A valid bearer token is required.") from None
        tenant = claims.get(self._s.oidc_tenant_claim)
        if not isinstance(tenant, str) or not _TENANT.fullmatch(tenant):
            raise AuthError(403, "The token does not carry a valid tenant.")
        return tenant
