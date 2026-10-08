import argparse
import json
import logging
import os
import pprint
import secrets
from collections.abc import Iterable
from socketserver import ThreadingMixIn
from typing import TYPE_CHECKING
from typing import Any
from wsgiref.simple_server import WSGIServer
from wsgiref.simple_server import make_server

from scim2_models import ResourceType
from scim2_models import Schema
from scim2_models import ScimProvider
from scim2_models import ServiceProviderConfig

from scim2_server.applications.wsgi import ForwardedHeaders
from scim2_server.applications.wsgi import TenantDispatcher
from scim2_server.memory import InMemoryStorage
from scim2_server.service import ScimService
from scim2_server.testserver.application import BEARER_TOKEN_SCHEME
from scim2_server.testserver.application import BearerTokenApplication
from scim2_server.utils import load_default_resource_types
from scim2_server.utils import load_default_schemas
from scim2_server.utils import load_default_service_provider_config

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIApplication
    from _typeshed.wsgi import WSGIEnvironment


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """A WSGI server that serves each request in its own thread."""

    daemon_threads = True


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


def build_parser() -> argparse.ArgumentParser:
    """Build the parser of the ``scim2-server`` command."""
    parser = argparse.ArgumentParser(
        prog="scim2-server",
        description="Serve SCIM resources from memory, "
        "for the tests and the demos of a SCIM client.",
    )
    parser.add_argument(
        "--schema",
        type=argparse.FileType("r"),
        metavar="FILE",
        help="Serve the schemas of a JSON file holding a list of schemas. "
        "Defaults to the schemas of RFC 7643.",
    )
    parser.add_argument(
        "--resource-type",
        type=argparse.FileType("r"),
        metavar="FILE",
        help="Serve the resource types of a JSON file holding a list of resource "
        "types. Defaults to User and Group.",
    )
    parser.add_argument(
        "--service-provider-config",
        type=argparse.FileType("r"),
        metavar="FILE",
        help="Announce the service provider configuration of a JSON file. "
        "Defaults to every feature supported, except cursor pagination.",
    )
    parser.add_argument(
        "--bearer-token",
        action="append",
        metavar="TOKEN",
        help="Accept a static bearer token, and announce the bearer token scheme. "
        "Can be repeated. Without it, the server accepts every request.",
    )
    parser.add_argument(
        "--precis",
        action="store_true",
        help="Compare the usernames and the passwords with the PRECIS profiles of "
        "RFC 8265, and refuse the values they do not allow. Needs the precis extra.",
    )
    parser.add_argument(
        "--hostname",
        default="127.0.0.1",
        metavar="HOST",
        help="Listen on this address. Defaults to 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        default=8080,
        type=int,
        help="Listen on this port. Defaults to 8080.",
    )
    parser.add_argument(
        "--reverse-proxy",
        action="store_true",
        help="Read the X-Forwarded-* headers of a reverse proxy.",
    )
    parser.add_argument(
        "--dump-resources",
        type=argparse.FileType("w"),
        metavar="FILE",
        help="Write the resources to a JSON file when the server stops.",
    )
    parser.add_argument(
        "--tenant",
        action="append",
        help="Serve a tenant under /TENANT, with its own resources. Can be repeated.",
    )
    parser.add_argument(
        "--dynamic-tenants",
        action="store_true",
        help="Create a tenant on the first request to /TENANT. Any client can "
        "create tenants, and they stay in memory until the server stops.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log the WSGI environment of each request. Never use it on a server "
        "reachable by others.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    for tenant in args.tenant or []:
        if not TenantDispatcher.is_valid_tenant(tenant):
            parser.error(f"invalid tenant name: {tenant!r}")

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

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

    policy = None
    if args.precis:
        try:
            # Imported on demand, so that the server runs without the precis extra.
            from scim2_server.testserver.precis import PRECIS_POLICY
        except ModuleNotFoundError:
            parser.error(
                "--precis needs the precis extra: pip install 'scim2-server[precis]'"
            )
        policy = PRECIS_POLICY

    provider = ScimProvider.from_discovery(
        schemas, resource_types, config=config, policy=policy
    )

    try:
        # The server runs in a single process, so a secret drawn at start serves
        # every cursor it issues.
        secret = os.environ.get("SCIM2_SERVER_SECRET") or secrets.token_urlsafe(32)
        service = ScimService(provider, secret=secret)
    except ModuleNotFoundError:
        parser.error(
            "cursor pagination needs the cursor extra: "
            "pip install 'scim2-server[cursor]'"
        )

    storages: dict[str | None, InMemoryStorage] = {}

    def make_application(tenant: str | None = None) -> BearerTokenApplication:
        storages[tenant] = InMemoryStorage()
        return BearerTokenApplication(
            storages[tenant],
            provider,
            service=service,
            bearer_tokens=args.bearer_token or [],
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
        wsgi_app = ForwardedHeaders(wsgi_app)

    with make_server(
        args.hostname, args.port, wsgi_app, server_class=ThreadingWSGIServer
    ) as server:
        print(f"Serving SCIM on http://{args.hostname}:{args.port}/v2", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

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
