"""The ASGI application of the SCIM server, with no dependency."""

import json
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl

from scim2_models import SCIMException
from scim2_models import ScimProvider

from scim2_server.applications.base import VERSION_PREFIX
from scim2_server.applications.base import BaseApplication
from scim2_server.handler import AsyncScimHandler
from scim2_server.requests import ScimRequest
from scim2_server.responses import ScimResponse
from scim2_server.service import ScimService
from scim2_server.storage import AsyncScimStorage

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class ASGIApplication(BaseApplication):
    """An ASGI application serving the SCIM protocol over an asynchronous storage.

    It reads the ASGI requests, serves them with an
    :class:`~scim2_server.handler.AsyncScimHandler`, and writes the responses.
    It answers the ``lifespan`` messages of the server.

    :param storage: The storage of the resources.
    :param provider: The description of the service.
    :param service: The service serving the requests, built upon ``provider``.
    """

    def __init__(
        self,
        storage: AsyncScimStorage,
        provider: ScimProvider,
        service: ScimService | None = None,
    ):
        super().__init__(provider, service)
        self.storage = storage
        self.handler = AsyncScimHandler(self.service, storage)

    @staticmethod
    def get_base_url(scope: Scope) -> str:
        """Return the root URL of the SCIM endpoints, as the client sees it."""
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode()
        if not host and scope.get("server"):
            host = "{}:{}".format(*scope["server"])
        return f"{scope['scheme']}://{host}{scope.get('root_path', '')}{VERSION_PREFIX}"

    @staticmethod
    def get_path(scope: Scope) -> str:
        """Return the path of a request, without the root path of the application.

        The ``path`` of an ASGI scope includes its ``root_path``.
        """
        path: str = scope["path"]
        root_path: str = scope.get("root_path", "")
        return path.removeprefix(root_path) if root_path else path

    async def read_request(self, scope: Scope, receive: Receive) -> ScimRequest:
        """Return the SCIM request of an ASGI request.

        The body is read up to one byte more than the service accepts, so that
        the service answers 413 to a larger body.
        """
        request = ScimRequest(
            method=scope["method"],
            base_url=self.get_base_url(scope),
            path=self.split_path(self.get_path(scope)),
            query=dict(parse_qsl(scope["query_string"].decode())),
            headers=[
                (name.decode(), value.decode()) for name, value in scope["headers"]
            ],
        )
        try:
            limit = self.service.max_body_size(request)
        except SCIMException:
            limit = None
        body = b""
        more_body = True
        while more_body:
            message = await receive()
            if limit is None or len(body) <= limit:
                body += message.get("body", b"")
            more_body = message.get("more_body", False)
        request.body = body if limit is None else body[: limit + 1]
        return request

    async def dispatch_request(self, request: ScimRequest) -> ScimResponse:
        """Authenticate a request and serve it.

        Override this method to act before or after a request is served.
        """
        if self.needs_auth(request):
            self.check_auth(request)
        request.subject = self.get_subject(request)
        return await self.handler.handle(request)

    async def serve(self, request: ScimRequest) -> ScimResponse:
        """Serve a request and return the response sent to the client."""
        try:
            response = await self.dispatch_request(request)
        except Exception as exception:
            response = self.handle_exception(request, exception)
        return self.finalize_response(request, response)

    @staticmethod
    async def send_response(response: ScimResponse, send: Send) -> None:
        """Send a SCIM response as an ASGI response."""
        body = b"" if response.body is None else json.dumps(response.body).encode()
        await send(
            {
                "type": "http.response.start",
                "status": int(response.status),
                "headers": [
                    (name.encode(), value.encode())
                    for name, value in response.headers.items()
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def serve_lifespan(receive: Receive, send: Send) -> None:
        """Answer the startup and the shutdown of the server."""
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            else:  # lifespan.shutdown
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve an ASGI request."""
        if scope["type"] == "lifespan":
            await self.serve_lifespan(receive, send)
            return
        request = await self.read_request(scope, receive)
        await self.send_response(await self.serve(request), send)
