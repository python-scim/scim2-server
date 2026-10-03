import json
import logging
from collections.abc import Iterable
from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any

from scim2_models import Error
from scim2_models import SCIMException
from scim2_models import ScimProvider
from werkzeug import Request
from werkzeug import Response
from werkzeug.exceptions import HTTPException
from werkzeug.exceptions import MethodNotAllowed
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.routing import BaseConverter
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.routing.exceptions import RequestRedirect

from scim2_server.handler import ScimHandler
from scim2_server.requests import ScimRequest
from scim2_server.responses import ScimResponse
from scim2_server.routing import ROUTES
from scim2_server.service import ScimService
from scim2_server.storage import ScimStorage

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIEnvironment


class ResourceEndpointConverter(BaseConverter):
    """Match a resource endpoint, but not the endpoints RFC 7644 reserves nor the version prefix.

    A request with a method a reserved endpoint does not support then gets a
    405 answer, instead of being routed to a resource type of that name.
    """

    # A reserved name followed by the end of the path segment is refused.
    regex = (
        r"(?!(?:ServiceProviderConfig|ResourceTypes|Schemas|Bulk|Me|v2)(?![^/]))[^/]+"
    )
    part_isolating = True


def werkzeug_pattern(pattern: str) -> str:
    """Return the werkzeug rule of a route pattern."""
    return (
        pattern.replace("{endpoint}", "<resource_endpoint:endpoint>")
        .replace("{resource_id}", "<string:resource_id>")
        .rstrip("/")
        or "/"
    )


class SCIMApplication:
    """A WSGI application implementing a SCIM provider (server).

    It routes the requests, reads them, and serves them with a
    :class:`~scim2_server.handler.ScimHandler`.

    :param storage: The storage of the resources.
    :param provider: The description of the service.
    :param service: The service serving the requests, built upon ``provider``.
        Pass a subclass of :class:`~scim2_server.service.ScimService` to change
        one of its steps, such as the URL of the resources. A
        :class:`~scim2_server.service.ScimService` of ``provider`` by default.
    """

    def __init__(
        self,
        storage: ScimStorage,
        provider: ScimProvider,
        service: ScimService | None = None,
    ):
        self.storage = storage
        self.provider = provider
        self.service = service if service is not None else ScimService(provider)
        self.handler = ScimHandler(self.service, storage)
        self.log = logging.getLogger("SCIMApplication")

        rules = [
            Rule(
                prefix + werkzeug_pattern(route.pattern),
                endpoint=route.name,
                methods=(route.method,),
            )
            for prefix in ("", "/v2")
            for route in ROUTES
        ]
        self.url_map = Map(
            rules, converters={"resource_endpoint": ResourceEndpointConverter}
        )

    # -- Reading requests -----------------------------------------------

    @staticmethod
    def has_version_prefix(request: Request) -> bool:
        """Tell whether the path of a request starts with the ``/v2`` prefix."""
        return request.path == "/v2" or request.path.startswith("/v2/")

    @staticmethod
    def get_base_url(request: Request) -> str:
        """Return the root URL of the SCIM endpoints, as the client sees it.

        It ends with ``/v2``, whether the request has this prefix or not.
        """
        return f"{request.url_root.rstrip('/')}/v2"

    def scim_request(self, request: Request) -> ScimRequest:
        """Return the SCIM request of a werkzeug request.

        Its path is relative to :meth:`get_base_url`. Its body is read up to
        the limit of the service.
        """
        path = request.path
        if self.has_version_prefix(request):
            path = path.removeprefix("/v2")
        scim_request = ScimRequest(
            method=request.method,
            base_url=self.get_base_url(request),
            path=path,
            query=request.args,
            headers=request.headers,
            subject=self.get_subject(request),
        )
        scim_request.body = self.read_body(request, scim_request)
        return scim_request

    def get_subject(self, request: Request) -> Any:
        """Return the authenticated subject of a request.

        It returns :data:`None` by default. Override it to pass the subject
        that :meth:`check_auth` authenticated to the service, for instance to
        serve ``/Me``.
        """
        return None

    def read_body(self, request: Request, scim_request: ScimRequest) -> bytes:
        """Read the body of a request, without reading more than the service accepts.

        :raises ~scim2_models.PayloadTooLargeException: When the body is larger.
        """
        limit = self.service.max_body_size(scim_request)
        if limit is None:
            return request.get_data()
        # Werkzeug refuses a Content-Length above the limit, but silently cuts a
        # streamed body at the limit. One extra byte tells a cut body apart.
        request.max_content_length = limit + 1
        try:
            return request.get_data()
        except RequestEntityTooLarge:
            raise self.service.payload_too_large(limit) from None

    # -- Authentication -------------------------------------------------

    def check_auth(self, request: Request) -> None:
        """Authenticate the client of a request.

        It accepts every request. Override it, and raise
        :class:`~werkzeug.exceptions.Unauthorized` or
        :class:`~werkzeug.exceptions.Forbidden` to refuse a request.
        """

    # -- Responses ------------------------------------------------------

    @staticmethod
    def make_response(result: ScimResponse) -> Response:
        """Construct a werkzeug response from a SCIM response."""
        headers = dict(result.headers)
        content_type = headers.pop("Content-Type")
        headers.setdefault("Cache-Control", "no-cache")
        headers.setdefault("Server", "scim-provider")
        content = json.dumps(result.body) if result.body is not None else None
        return Response(
            content,
            status=int(result.status),
            content_type=content_type,
            headers=headers,
        )

    def scim_exception_from(self, exception: Exception) -> Exception:
        """Log an exception raised while serving a request, and return it as a SCIM exception.

        A SCIM or werkzeug HTTP error answers the client: it is logged at the
        INFO level, without traceback. A werkzeug HTTP error keeps its status.
        Any other exception is a bug: it is logged at the ERROR level, with its
        traceback, and returned unchanged.
        """
        if isinstance(exception, HTTPException):
            self.log.info("%s", exception)
            return SCIMException.from_error(
                Error(status=exception.code, detail=exception.description)
            )
        if isinstance(exception, SCIMException):
            self.log.info("%s %s", exception.status, exception)
            return exception
        self.log.exception(exception)
        return exception

    def make_error(self, error: Error) -> Response:
        """Construct a werkzeug response from a SCIM Error."""
        return self.make_response(
            ScimResponse(HTTPStatus(error.status or 500), error.model_dump())
        )

    # -- WSGI -----------------------------------------------------------

    def wsgi_app(self, request: Request, environ: "WSGIEnvironment") -> Response:
        """Serve a request and return the response sent to the client."""
        try:
            endpoint, args = self.url_map.bind_to_environ(environ).match()
            response = self.dispatch_request(request, endpoint, args)
        except RequestRedirect as e:
            # urls.match may cause a redirect, handle it as a special case of HTTPException
            response = e.get_response(environ)
        except Exception as e:
            response = self.handle_exception(request, e)
        return self.finalize_response(request, response)

    def dispatch_request(
        self, request: Request, endpoint: str, args: Mapping[str, Any]
    ) -> Response:
        """Authenticate a routed request and serve it with the method of its endpoint.

        Override this method to act before or after a request is served.

        :param endpoint: The name of the route of the request, such as ``query``
            (:data:`~scim2_server.routing.ROUTES`).
        :param args: The arguments read from the request path.
        """
        if endpoint != "service_provider_config":
            # RFC 7643 §5: the service provider configuration needs no authentication.
            self.check_auth(request)

        return self.make_response(self.handler.handle(self.scim_request(request)))

    def handle_exception(self, request: Request, exception: Exception) -> Response:
        """Return the SCIM error response of an exception raised while serving a request.

        Override this method to observe the errors of the requests. The errors
        of the operations of a bulk request are not passed to this method.
        """
        response = self.make_response(
            self.service.error_response(self.scim_exception_from(exception))
        )
        if isinstance(exception, MethodNotAllowed) and exception.valid_methods:
            # RFC 9110 §15.5.6: a 405 answer lists the supported methods.
            response.headers["Allow"] = ", ".join(sorted(exception.valid_methods))
        return response

    def finalize_response(self, request: Request, response: Response) -> Response:
        """Add the headers every response carries, and return the response.

        Override this method to change or observe the response sent to the client.
        """
        if "Location" not in response.headers:
            # The spec is not explicit about requiring the "Location" header in all responses,
            # but the examples in RFC 7644 include the "Location" header even for responses that
            # did not create a new resource
            response.headers.add("Location", request.url)
        return response

    def __call__(
        self, environ: "WSGIEnvironment", start_response: "StartResponse"
    ) -> Iterable[bytes]:
        """Return the actual WSGI server implementation."""
        if environ.get("PATH_INFO", "").endswith(".scim"):
            # RFC 7644, Section 3.8
            # Just strip .scim suffix, the provider always returns application/scim+json
            environ["PATH_INFO"], _, _ = environ["PATH_INFO"].rpartition(".scim")
        request = Request(environ)
        response = self.wsgi_app(request, environ)
        return response(environ, start_response)
