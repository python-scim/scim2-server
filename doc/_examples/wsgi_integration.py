import json
import os
from urllib.parse import parse_qsl
from wsgiref.util import application_uri

from scim2_models import SCIMException

from scim2_server.handler import ScimHandler
from scim2_server.memory import InMemoryStorage
from scim2_server.requests import ScimRequest
from scim2_server.responses import ScimResponse
from scim2_server.service import ScimService
from scim2_server.utils import load_default_provider

service = ScimService(load_default_provider(), secret=os.environ["SCIM_SECRET"])
handler = ScimHandler(service, InMemoryStorage())


def wsgi_to_scim_request(environ):
    """Turn a WSGI request into a SCIM request."""
    headers = {
        key.removeprefix("HTTP_").replace("_", "-"): value
        for key, value in environ.items()
        if key.startswith("HTTP_")
    }
    headers["Content-Type"] = environ.get("CONTENT_TYPE", "")
    return ScimRequest(
        method=environ["REQUEST_METHOD"],
        base_url=application_uri(environ),
        path=environ["PATH_INFO"],
        query=dict(parse_qsl(environ.get("QUERY_STRING", ""))),
        headers=headers,
        body=environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0)),
    )


def scim_to_wsgi_response(response: ScimResponse, start_response):
    """Send a SCIM response as a WSGI response."""
    body = b"" if response.body is None else json.dumps(response.body).encode()
    status = f"{response.status.value} {response.status.phrase}"
    start_response(status, list(response.headers.items()))
    return [body]


def application(environ, start_response):
    """Serve the SCIM endpoints at the root of the application."""
    try:
        response = handler.handle(wsgi_to_scim_request(environ))
    except SCIMException as exception:
        response = service.error_response(exception)
    return scim_to_wsgi_response(response, start_response)
