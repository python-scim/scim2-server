import itertools
import json
import logging
from collections.abc import Iterable
from collections.abc import Mapping
from http import HTTPStatus
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import urljoin

from scim2_models import Error
from scim2_models import NotImplementedException
from scim2_models import ScimProvider
from werkzeug import Request
from werkzeug import Response
from werkzeug.exceptions import HTTPException
from werkzeug.exceptions import MethodNotAllowed
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.exceptions import Unauthorized
from werkzeug.routing import BaseConverter
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.routing.exceptions import RequestRedirect

from scim2_server.conditions import Conditions
from scim2_server.handler import ScimHandler
from scim2_server.responses import ScimResponse
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


class SCIMApplication:
    """A WSGI application implementing a SCIM provider (server).

    It routes the requests, reads them, and serves them with a
    :class:`~scim2_server.handler.ScimHandler`.
    """

    def __init__(self, storage: ScimStorage, provider: ScimProvider):
        self.bearer_tokens: set[str] = set()
        self.storage = storage
        self.provider = provider
        self.service = ScimService(provider)
        self.handler = ScimHandler(self.service, storage)
        self.log = logging.getLogger("SCIMApplication")

        # Register the URL mapping. The endpoint refers to the name of the function to be called in this SCIMApplication ("call_" + endpoint).
        rules = itertools.chain.from_iterable(
            [
                Rule(
                    f"{prefix}/ServiceProviderConfig",
                    endpoint="service_provider_config",
                    methods=("GET",),
                ),
                Rule(
                    f"{prefix}/ResourceTypes",
                    endpoint="resource_types",
                    methods=("GET",),
                ),
                Rule(
                    f"{prefix}/ResourceTypes/<string:resource_type>",
                    endpoint="resource_type",
                    methods=("GET",),
                ),
                Rule(
                    f"{prefix}/Schemas",
                    endpoint="schemas",
                    methods=("GET",),
                ),
                Rule(
                    f"{prefix}/Schemas/<string:schema_id>",
                    endpoint="schema",
                    methods=("GET",),
                ),
                Rule(
                    f"{prefix}/Me",
                    endpoint="me",
                    methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
                ),
                Rule(
                    f"{prefix}/<resource_endpoint:resource_endpoint>",
                    endpoint="resource",
                    methods=("GET", "POST"),
                ),
                Rule(
                    f"{prefix}/<resource_endpoint:resource_endpoint>/.search",
                    endpoint="resource_search",
                    methods=("POST",),
                ),
                Rule(
                    f"{prefix}/<resource_endpoint:resource_endpoint>/<string:resource_id>",
                    endpoint="single_resource",
                    methods=("GET", "PUT", "PATCH", "DELETE"),
                ),
                Rule(
                    f"{prefix}/Bulk",
                    endpoint="bulk",
                    methods=("POST",),
                ),
                Rule(f"{prefix}/", endpoint="query_all", methods=("GET",)),
                Rule(f"{prefix}/.search", endpoint="query_all", methods=("POST",)),
            ]
            for prefix in ("", "/v2")
        )

        self.url_map = Map(
            rules, converters={"resource_endpoint": ResourceEndpointConverter}
        )

    # -- Reading requests -----------------------------------------------

    @staticmethod
    def get_base_url(request: Request) -> str:
        """Return the root URL of the SCIM endpoints, as the client sees it."""
        return urljoin(request.url_root, "v2")

    @staticmethod
    def get_conditions(request: Request) -> Conditions:
        """Return the conditional headers of a request."""
        return Conditions(
            if_match=request.headers.get("If-Match"),
            if_none_match=request.headers.get("If-None-Match"),
        )

    def get_bulk_body(self, request: Request) -> bytes:
        """Read the body of a bulk request, without reading more than maxPayloadSize.

        :raises ~scim2_models.PayloadTooLargeException: When the body is larger.
        """
        limit = self.service.bulk_max_payload_size()
        if limit is None:
            return request.get_data()
        # Werkzeug refuses a Content-Length above the limit, but silently cuts a
        # streamed body at the limit. One extra byte tells a cut body apart.
        request.max_content_length = limit + 1
        try:
            return request.get_data()
        except RequestEntityTooLarge:
            raise self.service.payload_too_large(limit) from None

    # -- Endpoints ------------------------------------------------------

    def call_single_resource(
        self, request: Request, resource_endpoint: str, resource_id: str, **kwargs: Any
    ) -> Response:
        base_url = self.get_base_url(request)
        conditions = self.get_conditions(request)
        match request.method:
            case "GET":
                result = self.handler.query(
                    base_url, resource_endpoint, resource_id, request.args, conditions
                )
            case "DELETE":
                result = self.handler.delete(resource_endpoint, resource_id, conditions)
            case "PUT":
                result = self.handler.replace(
                    base_url,
                    resource_endpoint,
                    resource_id,
                    request.get_data(),
                    request.content_type,
                    request.args,
                    conditions,
                )
            case _:  # "PATCH"
                result = self.handler.patch(
                    base_url,
                    resource_endpoint,
                    resource_id,
                    request.get_data(),
                    request.content_type,
                    request.args,
                    conditions,
                )
        return self.make_response(result)

    def call_resource(
        self, request: Request, resource_endpoint: str, **kwargs: Any
    ) -> Response:
        base_url = self.get_base_url(request)
        if request.method == "GET":
            result = self.handler.search(base_url, resource_endpoint, request.args)
        else:
            result = self.handler.create(
                base_url, resource_endpoint, request.get_data(), request.content_type
            )
        return self.make_response(result)

    def call_resource_search(
        self, request: Request, resource_endpoint: str, **kwargs: Any
    ) -> Response:
        return self.make_response(
            self.handler.search_with_body(
                self.get_base_url(request),
                resource_endpoint,
                request.get_data(),
                request.content_type,
            )
        )

    def call_query_all(self, request: Request, **kwargs: Any) -> Response:
        base_url = self.get_base_url(request)
        if request.method == "GET":
            result = self.handler.search(base_url, None, request.args)
        else:
            result = self.handler.search_with_body(
                base_url, None, request.get_data(), request.content_type
            )
        return self.make_response(result)

    def call_bulk(self, request: Request, **kwargs: Any) -> Response:
        """Implement the /Bulk endpoint (RFC 7644 §3.7)."""
        return self.make_response(
            self.handler.bulk(
                self.get_base_url(request),
                self.get_bulk_body(request),
                request.content_type,
            )
        )

    def call_me(self, request: Request, **kwargs: Any) -> Response:
        """Implement the /Me endpoint.

        RFC 7644, Section 3.11 allows raising a 501 (Not Implemented) if
        the endpoint does not provide this feature.
        """
        raise NotImplementedException(detail="/Me is not supported")

    def call_service_provider_config(self, request: Request, **kwargs: Any) -> Response:
        """Return the ServiceProviderConfig."""
        return self.make_response(
            self.handler.service_provider_config(request.base_url, request.args)
        )

    def call_resource_types(self, request: Request, **kwargs: Any) -> Response:
        """Return a ListResponse of all known resource types."""
        return self.make_response(
            self.handler.resource_types(request.base_url, request.args)
        )

    def call_resource_type(
        self, request: Request, resource_type: str, **kwargs: Any
    ) -> Response:
        """Return a single resource type."""
        return self.make_response(
            self.handler.resource_type(request.base_url, resource_type, request.args)
        )

    def call_schemas(self, request: Request, **kwargs: Any) -> Response:
        """Return a ListResponse of all known schemas."""
        return self.make_response(self.handler.schemas(request.base_url, request.args))

    def call_schema(self, request: Request, schema_id: str) -> Response:
        """Return a single schema."""
        return self.make_response(
            self.handler.schema(request.base_url, schema_id, request.args)
        )

    # -- Authentication -------------------------------------------------

    def register_bearer_token(self, token: str) -> None:
        """Register a static bearer token for authentication.

        :param token: Bearer token
        """
        self.bearer_tokens.add(token)

    def check_auth(self, request: Request) -> None:
        """Check the authorization headers."""
        if not self.bearer_tokens:
            return
        if (
            not request.authorization
            or request.authorization.token not in self.bearer_tokens
        ):
            raise Unauthorized

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

    def error_from(self, exception: Exception) -> Error:
        """Log an exception raised while serving a request and return its SCIM Error."""
        self.log.exception(exception)
        if isinstance(exception, HTTPException):
            return Error(status=exception.code, detail=exception.description)
        return self.service.error_of(exception)

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
            self.log.exception(e)
            response = e.get_response(environ)
        except Exception as e:
            response = self.handle_exception(request, e)
        return self.finalize_response(request, response)

    def dispatch_request(
        self, request: Request, endpoint: str, args: Mapping[str, Any]
    ) -> Response:
        """Authenticate a routed request and serve it with the method of its endpoint.

        Override this method to act before or after a request is served.

        :param endpoint: The endpoint of the request, served by the ``call_<endpoint>`` method.
        :param args: The arguments read from the request path.
        """
        if endpoint != "service_provider_config":
            # RFC7643, Section 5: skip authentication for ServiceProviderConfig
            self.check_auth(request)

        response: Response = getattr(self, f"call_{endpoint}")(request, **args)
        return response

    def handle_exception(self, request: Request, exception: Exception) -> Response:
        """Return the SCIM error response of an exception raised while serving a request.

        Override this method to observe the errors of the requests. The errors
        of the operations of a bulk request are not passed to this method.
        """
        response = self.make_error(self.error_from(exception))
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
        if self.bearer_tokens and not request.authorization:
            # RFC 7644, Section 2
            response.headers.add("WWW-Authenticate", 'Bearer realm="SCIM Provider"')
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
