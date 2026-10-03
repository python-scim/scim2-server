import logging
from typing import Any

from scim2_models import SCIMException
from scim2_models import ScimProvider

from scim2_server.requests import ScimRequest
from scim2_server.responses import ScimResponse
from scim2_server.routing import Operation
from scim2_server.service import ScimService

VERSION_PREFIX = "/v2"


class BaseApplication:
    """The parts of the WSGI and ASGI applications that do no input or output.

    :param provider: The description of the service.
    :param service: The service serving the requests, built upon ``provider``.
        Pass a subclass of :class:`~scim2_server.service.ScimService` to change
        one of its steps, such as the URL of the resources. A
        :class:`~scim2_server.service.ScimService` of ``provider`` by default.
    """

    def __init__(self, provider: ScimProvider, service: ScimService | None = None):
        self.provider = provider
        self.service = service if service is not None else ScimService(provider)
        self.log = logging.getLogger("scim2_server")

    @staticmethod
    def split_path(path: str) -> str:
        """Return the path of a request, relative to the root URL of the SCIM endpoints.

        The ``/v2`` prefix is optional, and the ``.scim`` suffix is removed
        (RFC 7644 §3.8).
        """
        if path == VERSION_PREFIX or path.startswith(f"{VERSION_PREFIX}/"):
            path = path.removeprefix(VERSION_PREFIX)
        return path.removesuffix(".scim")

    def get_subject(self, request: ScimRequest) -> Any:
        """Return the authenticated subject of a request.

        It returns :data:`None` by default. Override it to pass the subject
        that :meth:`check_auth` authenticated to the service, for instance to
        serve ``/Me``.
        """
        return None

    def check_auth(self, request: ScimRequest) -> None:
        """Authenticate the client of a request.

        It accepts every request. Override it, and raise
        :class:`~scim2_models.UnauthorizedException` or
        :class:`~scim2_models.ForbiddenException` to refuse a request. It is
        not called for ``/ServiceProviderConfig`` (RFC 7643 §5).
        """

    def needs_auth(self, request: ScimRequest) -> bool:
        """Tell whether a request needs authentication.

        The service provider configuration needs none (RFC 7643 §5).
        """
        operation = self.service.match(request).operation
        return operation is not Operation.service_provider_config

    def handle_exception(
        self, request: ScimRequest, exception: Exception
    ) -> ScimResponse:
        """Return the SCIM error response of an exception raised while serving a request.

        An error of the client is logged at the INFO level, without traceback.
        Any other exception is a bug: it is logged at the ERROR level, with its
        traceback. Override this method to observe the errors of the requests.
        The errors of the operations of a bulk request are not passed to it.
        """
        if isinstance(exception, SCIMException):
            self.log.info("%s %s", exception.status, exception)
        else:
            self.log.exception(exception)
        return self.service.error_response(exception)

    def finalize_response(
        self, request: ScimRequest, response: ScimResponse
    ) -> ScimResponse:
        """Add the headers every response carries, and return the response.

        Override this method to change or observe the response sent to the
        client.
        """
        # RFC 7644 does not require a Location header on every response, but
        # its examples include one even when no resource was created.
        response.headers.setdefault("Location", request.url)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response
