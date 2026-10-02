"""The SCIM application of the ``scim2-server`` command."""

from collections.abc import Iterable

from scim2_models import ScimProvider
from werkzeug import Request
from werkzeug import Response
from werkzeug.exceptions import Unauthorized

from scim2_server.service import ScimService
from scim2_server.storage import ScimStorage
from scim2_server.werkzeug.app import SCIMApplication


class BearerTokenApplication(SCIMApplication):
    """A SCIM application that only accepts static bearer tokens.

    Without token, it accepts every request.
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

    def finalize_response(self, request: Request, response: Response) -> Response:
        """Announce the bearer scheme to a client that sent no credentials."""
        response = super().finalize_response(request, response)
        if self.bearer_tokens and not request.authorization:
            # RFC 7644 §2
            response.headers.add("WWW-Authenticate", 'Bearer realm="SCIM Provider"')
        return response
