import argparse
import json
import logging
import pprint

from scim2_models import ResourceType
from scim2_models import Schema
from werkzeug.middleware.proxy_fix import ProxyFix

from scim2_server.backend import InMemoryBackend
from scim2_server.provider import SCIMApplication
from scim2_server.utils import load_default_resource_types
from scim2_server.utils import load_default_schemas


def log_environ(handler):
    """Build a simple decorator to log all WSGI environment variables."""

    def _inner(environ, start_fn):
        logging.getLogger("log_environ").debug(pprint.pformat(environ))
        return handler(environ, start_fn)

    return _inner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--schema", type=argparse.FileType("r"), help="Schema definitions"
    )
    parser.add_argument(
        "--resource-type", type=argparse.FileType("r"), help="Resource Type definitions"
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

    backend = InMemoryBackend()
    app = SCIMApplication(backend)

    if args.schema is None:
        for schema in load_default_schemas().values():
            app.register_schema(schema)
    else:
        def_sch = json.load(args.schema)
        for sc in def_sch:
            schema = Schema.model_validate(sc)
            app.register_schema(schema)
        args.schema.close()

    if args.resource_type is None:
        for resource_type in load_default_resource_types().values():
            app.register_resource_type(resource_type)
    else:
        def_rt = json.load(args.resource_type)
        for rt in def_rt:
            resource_type = ResourceType.model_validate(rt)
            app.register_resource_type(resource_type)
        args.resource_type.close()

    if args.bearer_token is not None:
        for bearer_token in args.bearer_token:
            app.register_bearer_token(bearer_token)

    if args.debug:
        app = log_environ(app)
    if args.reverse_proxy:
        app = ProxyFix(app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

    run_simple(
        args.hostname,
        args.port,
        app,
        use_debugger=args.debug,
        use_reloader=args.debug,
    )

    if args.dump_resources:
        with args.dump_resources as f:
            f.write(json.dumps([r.model_dump() for r in backend.resources], indent=2))


if __name__ == "__main__":
    main()
