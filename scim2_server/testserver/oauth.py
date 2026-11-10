"""The OAuth access token validation of the ``--oauth-*`` options of the ``scim2-server`` command."""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from authlib.oauth2.rfc6750.errors import InsufficientScopeError
from authlib.oauth2.rfc6750.errors import InvalidTokenError
from authlib.oauth2.rfc9068 import JWTBearerTokenValidator
from joserfc.jwk import GuestProtocol
from joserfc.jwk import KeySet

from scim2_server.testserver.application import InsufficientScopeException
from scim2_server.testserver.application import InvalidAccessTokenException


def fetch_json(url: str) -> Any:
    """Download a JSON document.

    An HTTP error is closed before it is raised, so that it holds no connection.
    """
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        error.close()
        raise


def metadata_urls(issuer: str) -> tuple[str, str]:
    """Return the URLs of the metadata of an issuer.

    The first one is the URL of RFC 8414 §3.1, the second one the URL of
    OpenID Connect Discovery 1.0 §4.
    """
    scheme, netloc, path, _, _ = urllib.parse.urlsplit(issuer)
    path = path.rstrip("/")
    rfc8414 = urllib.parse.urlunsplit(
        (scheme, netloc, f"/.well-known/oauth-authorization-server{path}", "", "")
    )
    return rfc8414, f"{issuer.rstrip('/')}/.well-known/openid-configuration"


class AccessTokenValidator(JWTBearerTokenValidator):  # type: ignore[misc]
    """Validate JWT access tokens (RFC 9068) with the keys of the issuer.

    Without ``jwks_uri``, the URL of the keys comes from the metadata of the
    issuer. The keys are downloaded with the first token, then kept. A token signed
    with an unknown key downloads them again, at most once per
    :attr:`refresh_interval` seconds, so that forged tokens cannot flood the
    authorization server.
    """

    refresh_interval = 60

    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_uri: str | None = None,
        scope: str | None = None,
        fetch: Callable[[str], Any] = fetch_json,
    ):
        super().__init__(issuer=issuer, resource_server=audience, realm="SCIM")
        self.jwks_uri = jwks_uri
        self.scope = scope
        self.fetch = fetch
        self.keys: KeySet | None = None
        self.downloaded_at = float("-inf")
        self.lock = threading.Lock()

    def get_jwks(self) -> Callable[[GuestProtocol], KeySet]:
        return self.resolve_keys

    def resolve_keys(self, token: GuestProtocol) -> KeySet:
        """Return the keys that may sign a token, and download them when needed."""
        with self.lock:
            if self.keys is None or self.needs_download(self.keys, token):
                self.downloaded_at = time.monotonic()
                self.jwks_uri = self.jwks_uri or self.discover_jwks_uri()
                self.keys = KeySet.import_key_set(self.fetch(self.jwks_uri))

            return self.keys

    def discover_jwks_uri(self) -> str:
        """Return the URL of the keys, from the metadata of the issuer.

        The metadata of RFC 8414 are tried first, then the ones of OpenID
        Connect Discovery. Per RFC 8414 §3.3, the issuer of the metadata must
        be the issuer of the tokens.
        """
        rfc8414, openid = metadata_urls(self.issuer)
        try:
            metadata = self.fetch(rfc8414)
        except urllib.error.HTTPError:
            metadata = self.fetch(openid)

        if metadata.get("issuer") != self.issuer:
            raise ValueError(
                f"The metadata of {self.issuer} are for the issuer {metadata.get('issuer')}"
            )

        if "jwks_uri" not in metadata:
            raise ValueError(f"The metadata of {self.issuer} have no jwks_uri")

        return str(metadata["jwks_uri"])

    def needs_download(self, keys: KeySet, token: GuestProtocol) -> bool:
        """Tell whether a token is signed with an unknown key, and the refresh interval has passed."""
        kid = token.headers().get("kid")
        if not kid or any(key.kid == kid for key in keys.keys):
            return False

        return time.monotonic() - self.downloaded_at >= self.refresh_interval

    def check(self, token_string: str) -> None:
        """Refuse an invalid or expired access token, or one without the scope."""
        scopes = [self.scope] if self.scope else None
        try:
            token = self.authenticate_token(token_string)
            self.validate_token(token, scopes, None)
        except InvalidTokenError as error:
            challenge = dict(error.get_headers())["WWW-Authenticate"]
            raise InvalidAccessTokenException(challenge) from error
        except InsufficientScopeError as error:
            challenge = (
                f'Bearer realm="SCIM", error="{error.error}", scope="{self.scope}"'
            )
            raise InsufficientScopeException(challenge) from error
