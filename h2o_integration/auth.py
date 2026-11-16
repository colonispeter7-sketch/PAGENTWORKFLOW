"""OAuth resource-server verification; token issuance belongs to the operator's IdP."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt
from mcp.server.auth.provider import AccessToken

SCOPE = "h2o:review"


def https_url(value: str) -> str:
    parsed = urlsplit(value)
    if (value != value.strip() or any(char in value for char in ("\r", "\n", "*"))
            or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("OAuth resource, issuer and JWKS configuration must use HTTPS URLs without credentials, queries or fragments.")
    return value


@dataclass(frozen=True)
class OAuthConfig:
    resource: str
    issuer: str
    jwks_url: str

    def __post_init__(self):
        for url in (self.resource, self.issuer, self.jwks_url):
            https_url(url)
        if urlsplit(self.resource).path != "/mcp":
            raise ValueError("The canonical MCP resource URL must end in /mcp without a trailing slash.")

    @classmethod
    def from_env(cls) -> OAuthConfig:
        try:
            return cls(os.environ["H2O_MCP_RESOURCE_URL"], os.environ["H2O_MCP_OAUTH_ISSUER"],
                       os.environ["H2O_MCP_JWKS_URL"])
        except KeyError:
            raise ValueError("HTTP requires H2O_MCP_RESOURCE_URL, H2O_MCP_OAUTH_ISSUER and H2O_MCP_JWKS_URL.") from None


class OAuthJWTVerifier:
    """Only RS256 access tokens from the configured issuer/JWKS and audience."""

    def __init__(self, config: OAuthConfig, *, jwks_client=None):
        self.config = config
        self.jwks = jwks_client or jwt.PyJWKClient(config.jwks_url, timeout=5, lifespan=300)

    async def verify_token(self, token: str) -> AccessToken | None:
        if len(token) > 16_384:
            return None
        try:
            key = await asyncio.to_thread(self.jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=self.config.resource,
                                issuer=self.config.issuer, options={"require": ["exp", "iat", "iss", "aud", "sub"]})
            if not isinstance(claims["sub"], str) or not claims["sub"]:
                return None
            scopes = claims.get("scope", "")
            if not isinstance(scopes, str):
                return None
            client_id = claims.get("client_id") or claims.get("azp") or claims["sub"]
            if not isinstance(client_id, str) or not client_id:
                return None
            return AccessToken(token=token, client_id=client_id, subject=claims["sub"],
                               scopes=scopes.split(), expires_at=int(claims["exp"]), resource=self.config.resource)
        except Exception:
            # Fail closed without logging tokens, provider payloads or key data.
            return None
