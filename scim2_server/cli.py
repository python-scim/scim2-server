import argparse
import json
import logging
import pprint
from collections.abc import Iterable
from typing import TYPE_CHECKING

from scim2_models import AuthenticationScheme
from scim2_models import External
from scim2_models import Reference
from scim2_models import ResourceType
from scim2_models import Schema
from scim2_models import ScimProvider
from scim2_models import ServiceProviderConfig
from werkzeug.middleware.proxy_fix import ProxyFix

from scim2_server.backend import InMemoryBackend
from scim2_server.provider import SCIMApplication
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
        "--debug",
        action="store_true",
        help="Enable the interactive debugger, the reloader and the logging of the WSGI environment",
    )
    args = parser.parse_args()

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

    backend = InMemoryBackend()
    app = SCIMApplication(
        backend, ScimProvider.from_discovery(schemas, resource_types, config=config)
    )

    if args.bearer_token is not None:
        for bearer_token in args.bearer_token:
            app.register_bearer_token(bearer_token)

    wsgi_app: WSGIApplication = app
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
        with args.dump_resources as f:
            f.write(json.dumps([r.model_dump() for r in backend.resources], indent=2))


if __name__ == "__main__":
    main()
