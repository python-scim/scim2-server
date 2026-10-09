import json
import os
from urllib.parse import parse_qsl

from scim2_models import SCIMException

from scim2_server.handler import AsyncScimHandler
from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.requests import ScimRequest
from scim2_server.responses import ScimResponse
from scim2_server.service import ScimService
from scim2_server.utils import load_default_provider

service = ScimService(load_default_provider(), secret=os.environ["SCIM_SECRET"])
handler = AsyncScimHandler(service, AsyncInMemoryStorage())


async def asgi_to_scim_request(scope, receive):
    """Turn an ASGI request into a SCIM request."""
    headers = [(name.decode(), value.decode()) for name, value in scope["headers"]]
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    return ScimRequest(
        method=scope["method"],
        base_url=f"{scope['scheme']}://{dict(headers)['host']}{scope['root_path']}",
        path=scope["path"],
        query=dict(parse_qsl(scope["query_string"].decode())),
        headers=headers,
        body=body,
    )


async def scim_to_asgi_response(response: ScimResponse, send):
    """Send a SCIM response as an ASGI response."""
    body = b"" if response.body is None else json.dumps(response.body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": response.status,
            "headers": [(k.encode(), v.encode()) for k, v in response.headers.items()],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def application(scope, receive, send):
    """Serve the SCIM endpoints at the root of the application."""
    try:
        response = await handler.handle(await asgi_to_scim_request(scope, receive))
    except SCIMException as exception:
        response = service.error_response(exception)
    await scim_to_asgi_response(response, send)
