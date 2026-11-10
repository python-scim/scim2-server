"""The SCIM application of the ``scim2-server`` command."""

import base64
import binascii
import hmac
from collections.abc import Iterable
from typing import TYPE_CHECKING

from scim2_models import AuthenticationScheme
from scim2_models import External
from scim2_models import ForbiddenException
from scim2_models import Reference
from scim2_models import ScimProvider
from scim2_models import ServiceProviderConfig
from scim2_models import UnauthorizedException

from scim2_server.applications.wsgi import WSGIApplication
from scim2_server.requests import ScimRequest
from scim2_server.responses import ScimResponse
from scim2_server.service import CHALLENGES
from scim2_server.service import ScimService
from scim2_server.storage import ScimStorage

if TYPE_CHECKING:
    from scim2_server.testserver.oauth import AccessTokenValidator

BEARER_TOKEN_SCHEME = AuthenticationScheme(
    type=AuthenticationScheme.Type.oauthbearertoken,
    name="bearer_token",
    description="HTTP Bearer Token",
    spec_uri=Reference[External]("https://datatracker.ietf.org/doc/html/rfc6750"),
)

BASIC_SCHEME = AuthenticationScheme(
    type=AuthenticationScheme.Type.httpbasic,
    name="basic",
    description="HTTP Basic",
    spec_uri=Reference[External]("https://datatracker.ietf.org/doc/html/rfc7617"),
)

OAUTH2_SCHEME = AuthenticationScheme(
    type=AuthenticationScheme.Type.oauth2,
    name="oauth2",
    description="OAuth 2.0 JWT access token",
    spec_uri=Reference[External]("https://datatracker.ietf.org/doc/html/rfc9068"),
)


def announce_authentication_schemes(
    config: ServiceProviderConfig, bearer_token: bool, basic: bool, oauth: bool
) -> None:
    """Add the schemes of the accepted credentials to a service provider configuration.

    Without credentials, the application accepts any bearer token. If the
    configuration has no scheme then, it announces the bearer token scheme,
    because the ``authenticationSchemes`` attribute is required
    (:rfc:`RFC 7643 §5 <7643#section-5>`).
    """
    schemes = list(config.authentication_schemes or [])
    if bearer_token or not (basic or oauth or schemes):
        schemes.append(BEARER_TOKEN_SCHEME)

    if basic:
        schemes.append(BASIC_SCHEME)

    if oauth:
        schemes.append(OAUTH2_SCHEME)

    config.authentication_schemes = schemes


class InvalidAccessTokenException(UnauthorizedException):
    """A 401 error for an invalid or expired OAuth access token.

    Its response carries a Bearer challenge with an error code
    (:rfc:`RFC 6750 §3 <6750#section-3>`).
    """

    def __init__(self, challenge: str):
        super().__init__()
        self.challenge = challenge


class InsufficientScopeException(ForbiddenException):
    """A 403 error for an OAuth access token without the required scope.

    Its response carries a Bearer challenge with an error code
    (:rfc:`RFC 6750 §3 <6750#section-3>`).
    """

    def __init__(self, challenge: str):
        super().__init__()
        self.challenge = challenge


def _equals(value: str, expected: str) -> bool:
    return hmac.compare_digest(value.encode(), expected.encode())


def decode_basic_credentials(value: str) -> tuple[str, str] | None:
    """Return the user and the password of Basic credentials, or None if they are malformed."""
    try:
        decoded = base64.b64decode(value, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None

    user, separator, password = decoded.partition(":")
    if not separator:
        return None

    return user, password


class CredentialsApplication(WSGIApplication):
    """A SCIM application that only accepts the credentials it is given.

    It accepts static bearer tokens, Basic credentials, and the OAuth JWT
    access tokens of an ``access_token_validator``. Without any of them, it
    accepts every request. List :data:`BEARER_TOKEN_SCHEME`,
    :data:`BASIC_SCHEME` and :data:`OAUTH2_SCHEME` in the service provider
    configuration, so that the 401 responses carry a :mdn:`WWW-Authenticate`
    header.
    """

    def __init__(
        self,
        storage: ScimStorage,
        provider: ScimProvider,
        service: ScimService | None = None,
        bearer_tokens: Iterable[str] = (),
        basic_credentials: Iterable[tuple[str, str]] = (),
        access_token_validator: "AccessTokenValidator | None" = None,
    ):
        super().__init__(storage, provider, service)
        self.bearer_tokens = list(bearer_tokens)
        self.basic_credentials = list(basic_credentials)
        self.access_token_validator = access_token_validator

    def check_auth(self, request: ScimRequest) -> None:
        """Refuse a request without valid credentials."""
        if not (
            self.bearer_tokens or self.basic_credentials or self.access_token_validator
        ):
            return

        scheme, _, value = (request.header("Authorization") or "").partition(" ")
        if scheme.lower() == "bearer" and self.is_valid_token(value):
            return

        if scheme.lower() == "basic" and self.are_valid_credentials(value):
            return

        if scheme.lower() == "bearer" and self.access_token_validator:
            self.access_token_validator.check(value)
            return

        raise UnauthorizedException

    def handle_exception(
        self, request: ScimRequest, exception: Exception
    ) -> ScimResponse:
        """Return the error response, with the Bearer challenge of an access token error.

        The 401 response of an invalid access token keeps the challenges of
        the other schemes.
        """
        response = super().handle_exception(request, exception)
        if isinstance(exception, InsufficientScopeException):
            response.headers["WWW-Authenticate"] = exception.challenge

        if isinstance(exception, InvalidAccessTokenException):
            schemes = self.service.config.authentication_schemes or []
            others = dict.fromkeys(
                CHALLENGES[scheme.type]
                for scheme in schemes
                if scheme.type in CHALLENGES
                and not CHALLENGES[scheme.type].startswith("Bearer ")
            )
            response.headers["WWW-Authenticate"] = ", ".join(
                [exception.challenge, *others]
            )

        return response

    def is_valid_token(self, token: str) -> bool:
        """Tell whether a token is one of the bearer tokens."""
        return any(_equals(token, expected) for expected in self.bearer_tokens)

    def are_valid_credentials(self, value: str) -> bool:
        """Tell whether encoded Basic credentials are among the accepted ones."""
        credentials = decode_basic_credentials(value)
        if credentials is None:
            return False

        user, password = credentials
        return any(
            _equals(user, expected_user) and _equals(password, expected_password)
            for expected_user, expected_password in self.basic_credentials
        )
