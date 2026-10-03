"""The WSGI application of the SCIM server, with no dependency."""

import json
from collections.abc import Callable
from collections.abc import Iterable
from http import HTTPStatus
from threading import Lock
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl
from wsgiref.util import application_uri

from scim2_models import NotFoundException
from scim2_models import SCIMException
from scim2_models import ScimProvider

from scim2_server.applications.base import VERSION_PREFIX
from scim2_server.applications.base import BaseApplication
from scim2_server.handler import ScimHandler
from scim2_server.requests import ScimRequest
from scim2_server.responses import ScimResponse
from scim2_server.service import ScimService
from scim2_server.storage import ScimStorage

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIApplication as WSGICallable
    from _typeshed.wsgi import WSGIEnvironment

RESERVED_TENANTS = frozenset({"v2"})


def send_response(
    response: ScimResponse, start_response: "StartResponse"
) -> Iterable[bytes]:
    """Send a SCIM response as a WSGI response."""
    body = b"" if response.body is None else json.dumps(response.body).encode()
    status = f"{response.status.value} {response.status.phrase}"
    start_response(status, list(response.headers.items()))
    return [body]


class WSGIApplication(BaseApplication):
    """A WSGI application serving the SCIM protocol over a storage.

    It reads the WSGI requests, serves them with a
    :class:`~scim2_server.handler.ScimHandler`, and writes the responses.

    :param storage: The storage of the resources.
    :param provider: The description of the service.
    :param service: The service serving the requests, built upon ``provider``.
    """

    def __init__(
        self,
        storage: ScimStorage,
        provider: ScimProvider,
        service: ScimService | None = None,
    ):
        super().__init__(provider, service)
        self.storage = storage
        self.handler = ScimHandler(self.service, storage)

    @staticmethod
    def get_base_url(environ: "WSGIEnvironment") -> str:
        """Return the root URL of the SCIM endpoints, as the client sees it."""
        return f"{application_uri(environ).rstrip('/')}{VERSION_PREFIX}"

    def read_request(self, environ: "WSGIEnvironment") -> ScimRequest:
        """Return the SCIM request of a WSGI request.

        The body is read up to one byte more than the service accepts, so that
        the service answers 413 to a larger body.
        """
        headers = {
            key.removeprefix("HTTP_").replace("_", "-"): value
            for key, value in environ.items()
            if key.startswith("HTTP_")
        }
        if environ.get("CONTENT_TYPE"):
            headers["Content-Type"] = environ["CONTENT_TYPE"]
        request = ScimRequest(
            method=environ["REQUEST_METHOD"],
            base_url=self.get_base_url(environ),
            path=self.split_path(environ.get("PATH_INFO", "")),
            query=dict(parse_qsl(environ.get("QUERY_STRING", ""))),
            headers=headers,
        )
        request.body = self.read_body(environ, request)
        return request

    def read_body(self, environ: "WSGIEnvironment", request: ScimRequest) -> bytes:
        """Read the body of a request, up to one byte more than the service accepts.

        A body without :mdn:`Content-Length` is read up to its end, when the
        server marks its input as terminated.
        """
        try:
            limit = self.service.max_body_size(request)
        except SCIMException:
            limit = None
        if environ.get("CONTENT_LENGTH"):
            size = int(environ["CONTENT_LENGTH"])
        elif environ.get("wsgi.input_terminated"):
            size = -1
        else:
            return b""
        if limit is not None and (size < 0 or size > limit):
            size = limit + 1
        body: bytes = environ["wsgi.input"].read(size)
        return body

    def dispatch_request(self, request: ScimRequest) -> ScimResponse:
        """Authenticate a request and serve it.

        Override this method to act before or after a request is served.
        """
        if self.needs_auth(request):
            self.check_auth(request)
        request.subject = self.get_subject(request)
        return self.handler.handle(request)

    def serve(self, request: ScimRequest) -> ScimResponse:
        """Serve a request and return the response sent to the client."""
        try:
            response = self.dispatch_request(request)
        except Exception as exception:
            response = self.handle_exception(request, exception)
        return self.finalize_response(request, response)

    def __call__(
        self, environ: "WSGIEnvironment", start_response: "StartResponse"
    ) -> Iterable[bytes]:
        """Serve a WSGI request."""
        return send_response(self.serve(self.read_request(environ)), start_response)


class TenantDispatcher:
    """A WSGI application serving each tenant with its own SCIM application.

    The tenant is the first segment of the request path, as in the URL prefix
    method of :rfc:`RFC 7644 §6.1 <7644#section-6.1>`: a request to ``/<tenant>/v2/Users`` is served by
    the application of ``<tenant>``, mounted under ``/<tenant>``.

    The application of a tenant is built on its first request, then kept for
    the following ones.

    :param factory: Build the application of a tenant from its name, or
        return :data:`None` when the tenant does not exist.
    """

    def __init__(self, factory: Callable[[str], "WSGICallable | None"]):
        self.factory = factory
        self.applications: dict[str, WSGICallable] = {}
        self.lock = Lock()

    @staticmethod
    def is_valid_tenant(tenant: str) -> bool:
        """Tell whether a name can identify a tenant.

        The version segment is refused, so that a request without a tenant is
        not served by a tenant named ``v2``.
        """
        return bool(tenant) and "/" not in tenant and tenant not in RESERVED_TENANTS

    def select_tenant(self, environ: "WSGIEnvironment") -> str | None:
        """Return the tenant of a request, and move it from the path to the mount prefix.

        Override this method to read the tenant from somewhere else, such as
        a header or a sub-domain (:rfc:`RFC 7644 §6.1 <7644#section-6.1>`).
        """
        path_info: str = environ.get("PATH_INFO", "")
        _, _, path = path_info.partition("/")
        tenant, separator, rest = path.partition("/")
        if not self.is_valid_tenant(tenant):
            return None
        environ["SCRIPT_NAME"] = f"{environ.get('SCRIPT_NAME', '')}/{tenant}"
        environ["PATH_INFO"] = separator + rest
        return tenant

    def get_application(self, tenant: str) -> "WSGICallable | None":
        """Return the application of a tenant, building it on its first request."""
        with self.lock:
            if tenant not in self.applications:
                application = self.factory(tenant)
                if application is None:
                    return None
                self.applications[tenant] = application
            return self.applications[tenant]

    def __call__(
        self, environ: "WSGIEnvironment", start_response: "StartResponse"
    ) -> Iterable[bytes]:
        """Dispatch a request to the application of its tenant."""
        tenant = self.select_tenant(environ)
        application = self.get_application(tenant) if tenant is not None else None
        if application is None:
            error = NotFoundException(detail="Unknown tenant").to_error()
            response = ScimResponse(HTTPStatus.NOT_FOUND, error.model_dump())
            return send_response(response, start_response)
        return application(environ, start_response)


class ForwardedHeaders:
    """A WSGI middleware that trusts the ``X-Forwarded-*`` headers of a reverse proxy.

    The application then builds its URLs from the URL the client used.
    Only use it behind a proxy that sets these headers.
    """

    def __init__(self, application: "WSGICallable"):
        self.application = application

    def __call__(
        self, environ: "WSGIEnvironment", start_response: "StartResponse"
    ) -> Iterable[bytes]:
        """Rewrite the environment of a request from its forwarded headers."""

        def forwarded(name: str) -> str | None:
            value = environ.get(f"HTTP_X_FORWARDED_{name}")
            return value.split(",")[0].strip() if value else None

        if proto := forwarded("PROTO"):
            environ["wsgi.url_scheme"] = proto
        if host := forwarded("HOST"):
            environ["HTTP_HOST"] = host
        if port := forwarded("PORT"):
            host = environ.get("HTTP_HOST", "").partition(":")[0]
            environ["HTTP_HOST"] = f"{host}:{port}"
            environ["SERVER_PORT"] = port
        if prefix := forwarded("PREFIX"):
            environ["SCRIPT_NAME"] = prefix.rstrip("/") + environ.get("SCRIPT_NAME", "")
        if client := forwarded("FOR"):
            environ["REMOTE_ADDR"] = client
        return self.application(environ, start_response)
