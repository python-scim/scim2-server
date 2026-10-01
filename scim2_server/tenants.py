from collections.abc import Callable
from collections.abc import Iterable
from threading import Lock
from typing import TYPE_CHECKING

from scim2_models import Error

from scim2_server.provider import SCIMApplication

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIEnvironment

RESERVED_TENANTS = frozenset({"v2"})


class TenantDispatcher:
    """A WSGI application serving each tenant with its own SCIM application.

    The tenant is the first segment of the request path, as in the URL prefix
    method of RFC 7644 §6.1: a request to ``/<tenant>/v2/Users`` is served by
    the application of ``<tenant>``, mounted under ``/<tenant>``.

    The application of a tenant is built on its first request, then kept for
    the following ones.

    :param factory: Build the application of a tenant from its name, or
        return :data:`None` when the tenant does not exist.
    """

    def __init__(self, factory: Callable[[str], SCIMApplication | None]):
        self.factory = factory
        self.applications: dict[str, SCIMApplication] = {}
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
        a header or a sub-domain (RFC 7644 §6.1).
        """
        path_info: str = environ.get("PATH_INFO", "")
        _, _, path = path_info.partition("/")
        tenant, separator, rest = path.partition("/")
        if not self.is_valid_tenant(tenant):
            return None
        environ["SCRIPT_NAME"] = f"{environ.get('SCRIPT_NAME', '')}/{tenant}"
        environ["PATH_INFO"] = separator + rest
        return tenant

    def get_application(self, tenant: str) -> SCIMApplication | None:
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
            response = SCIMApplication.make_response(
                Error(status=404, detail="Unknown tenant").model_dump(), status=404
            )
            return response(environ, start_response)
        return application(environ, start_response)
