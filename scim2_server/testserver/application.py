"""The SCIM application of the ``scim2-server`` command."""

from collections.abc import Iterable

from scim2_models import AuthenticationScheme
from scim2_models import External
from scim2_models import Reference
from scim2_models import ScimProvider
from werkzeug import Request
from werkzeug.exceptions import Unauthorized

from scim2_server.provider import SCIMApplication
from scim2_server.service import ScimService
from scim2_server.storage import ScimStorage

BEARER_TOKEN_SCHEME = AuthenticationScheme(
    type=AuthenticationScheme.Type.oauthbearertoken,
    name="bearer_token",
    description="HTTP Bearer Token",
    spec_uri=Reference[External]("https://datatracker.ietf.org/doc/html/rfc6750"),
)


class BearerTokenApplication(SCIMApplication):
    """A SCIM application that only accepts static bearer tokens.

    Without token, it accepts every request. List
    :data:`BEARER_TOKEN_SCHEME` in the service provider configuration, so
    that the 401 responses carry a ``WWW-Authenticate`` header.
    """

    def __init__(
        self,
        storage: ScimStorage,
        provider: ScimProvider,
        service: ScimService | None = None,
        bearer_tokens: Iterable[str] = (),
    ):
        super().__init__(storage, provider, service)
        self.bearer_tokens = set(bearer_tokens)

    def check_auth(self, request: Request) -> None:
        """Refuse a request without one of the bearer tokens."""
        if not self.bearer_tokens:
            return
        if (
            not request.authorization
            or request.authorization.token not in self.bearer_tokens
        ):
            raise Unauthorized
