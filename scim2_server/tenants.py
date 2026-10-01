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

    :param factory: Build the application of a tenant from its name.
    :param tenants: The tenants created at startup.
    :param dynamic: Whether a request to an unknown tenant creates it. Any
        client can then create tenants, and each one stays in memory.
    """

    def __init__(
        self,
        factory: Callable[[str], SCIMApplication],
        tenants: Iterable[str] = (),
        dynamic: bool = False,
    ):
        self.factory = factory
        self.dynamic = dynamic
        self.applications: dict[str, SCIMApplication] = {}
        self.lock = Lock()
        for tenant in tenants:
            if not self.is_valid_tenant(tenant):
                raise ValueError(f"Invalid tenant name: {tenant!r}")
            self.applications[tenant] = factory(tenant)

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
        """Return the application of a tenant, creating it if tenants are dynamic."""
        with self.lock:
            if tenant not in self.applications and self.dynamic:
                self.applications[tenant] = self.factory(tenant)
            return self.applications.get(tenant)

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
