import argparse
import json
import logging
import pprint
from collections.abc import Iterable
from typing import TYPE_CHECKING
from typing import Any

from scim2_models import AuthenticationScheme
from scim2_models import External
from scim2_models import Reference
from scim2_models import ResourceType
from scim2_models import Schema
from scim2_models import ScimProvider
from scim2_models import ServiceProviderConfig
from werkzeug.middleware.proxy_fix import ProxyFix

from scim2_server.memory import InMemoryStorage
from scim2_server.tenants import TenantDispatcher
from scim2_server.testserver.application import BearerTokenApplication
from scim2_server.utils import load_default_resource_types
from scim2_server.utils import load_default_schemas
from scim2_server.utils import load_default_service_provider_config

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIApplication
    from _typeshed.wsgi import WSGIEnvironment

BEARER_TOKEN_SCHEME = AuthenticationScheme(
    type=AuthenticationScheme.Type.oauthbearertoken,
    name="bearer_token",
    description="HTTP Bearer Token",
    spec_uri=Reference[External]("https://datatracker.ietf.org/doc/html/rfc6750"),
)


def log_environ(handler: "WSGIApplication") -> "WSGIApplication":
    """Build a simple decorator to log all WSGI environment variables."""

    def _inner(
        environ: "WSGIEnvironment", start_fn: "StartResponse"
    ) -> Iterable[bytes]:
        logging.getLogger("log_environ").debug(pprint.pformat(environ))
        return handler(environ, start_fn)

    return _inner


def dump_resources(storage: InMemoryStorage) -> list[dict[str, Any]]:
    """Return the JSON representation of the resources of a storage."""
    return [r.model_dump() for r in storage.resources]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--schema", type=argparse.FileType("r"), help="Schema definitions"
    )
    parser.add_argument(
        "--resource-type", type=argparse.FileType("r"), help="Resource Type definitions"
    )
    parser.add_argument(
        "--service-provider-config",
        type=argparse.FileType("r"),
        help="Service provider configuration",
    )
    parser.add_argument("--bearer-token", action="append", help="Add Bearer Token")
    parser.add_argument("--hostname", default="127.0.0.1", help="Hostname")
    parser.add_argument("--port", default=8080, type=int, help="Port number")
    parser.add_argument(
        "--reverse-proxy",
        action="store_true",
        help='Allow running behind a reverse proxy (respect "X-Forwarded-*" HTTP headers)',
    )
    parser.add_argument(
        "--dump-resources",
        type=argparse.FileType("w"),
        help="Dump resources to a JSON file on exit",
    )
    parser.add_argument(
        "--tenant",
        action="append",
        help="Serve a tenant under /TENANT, with its own resources",
    )
    parser.add_argument(
        "--dynamic-tenants",
        action="store_true",
        help="Create a tenant on the first request to /TENANT. "
        "Any client can then create tenants, and they stay in memory until exit",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable the interactive debugger, the reloader and the logging of the WSGI environment",
    )
    args = parser.parse_args()
    for tenant in args.tenant or []:
        if not TenantDispatcher.is_valid_tenant(tenant):
            parser.error(f"invalid tenant name: {tenant!r}")

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    from werkzeug.serving import run_simple

    schemas: Iterable[Schema]
    if args.schema is None:
        schemas = load_default_schemas().values()
    else:
        with args.schema:
            schemas = [Schema.model_validate(sc) for sc in json.load(args.schema)]

    resource_types: Iterable[ResourceType]
    if args.resource_type is None:
        resource_types = load_default_resource_types().values()
    else:
        with args.resource_type:
            resource_types = [
                ResourceType.model_validate(rt) for rt in json.load(args.resource_type)
            ]

    if args.service_provider_config is None:
        config = load_default_service_provider_config()
    else:
        with args.service_provider_config:
            config = ServiceProviderConfig.model_validate(
                json.load(args.service_provider_config)
            )

    if args.bearer_token is not None:
        config.authentication_schemes = [
            *(config.authentication_schemes or []),
            BEARER_TOKEN_SCHEME,
        ]

    provider = ScimProvider.from_discovery(schemas, resource_types, config=config)

    storages: dict[str | None, InMemoryStorage] = {}

    def make_application(tenant: str | None = None) -> BearerTokenApplication:
        storages[tenant] = InMemoryStorage()
        return BearerTokenApplication(
            storages[tenant], provider, bearer_tokens=args.bearer_token or []
        )

    def make_tenant_application(tenant: str) -> BearerTokenApplication | None:
        if not args.dynamic_tenants and tenant not in args.tenant:
            return None
        return make_application(tenant)

    use_tenants = bool(args.tenant or args.dynamic_tenants)
    wsgi_app: WSGIApplication
    if use_tenants:
        wsgi_app = TenantDispatcher(make_tenant_application)
    else:
        wsgi_app = make_application()

    if args.debug:
        wsgi_app = log_environ(wsgi_app)
    if args.reverse_proxy:
        wsgi_app = ProxyFix(
            wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1
        )

    run_simple(
        args.hostname,
        args.port,
        wsgi_app,
        use_debugger=args.debug,
        use_reloader=args.debug,
        threaded=True,
    )

    if args.dump_resources:
        dump: Any = (
            {tenant: dump_resources(storage) for tenant, storage in storages.items()}
            if use_tenants
            else dump_resources(storages[None])
        )
        with args.dump_resources as f:
            f.write(json.dumps(dump, indent=2))


if __name__ == "__main__":
    main()
