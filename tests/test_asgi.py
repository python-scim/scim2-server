import asyncio
import json
from unittest.mock import patch

import httpx2
import pytest
from scim2_models import UnauthorizedException

from scim2_server.applications.asgi import ASGIApplication
from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.utils import load_default_provider

JSON = {"Content-Type": "application/scim+json"}


@pytest.fixture
def app(scim_provider):
    return ASGIApplication(AsyncInMemoryStorage(), scim_provider)


def send(app, method, path, **kwargs):
    async def scenario():
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="https://scim.example.com"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(scenario())


def call(app, scope, messages):
    """Call the application with raw ASGI messages, and return the messages it sent."""
    received = list(messages)
    sent = []

    async def receive():
        return received.pop(0)

    async def send_message(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send_message))
    return sent


def http_scope(method, path, headers=(), server=None):
    return {
        "type": "http",
        "method": method,
        "scheme": "https",
        "path": path,
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "server": server,
    }


def test_a_resource_is_created_and_read(app):
    """The ASGI application creates a resource, and serves it at its location."""
    created = send(app, "POST", "/v2/Users", json={"userName": "bjensen"}, headers=JSON)
    read = send(app, "GET", f"/v2/Users/{created.json()['id']}")

    assert created.status_code == 201
    assert created.headers["Location"] == read.headers["Location"]
    assert read.json()["userName"] == "bjensen"


def test_an_error_is_a_scim_error(app):
    """A refused request answers a SCIM error."""
    r = send(app, "GET", "/v2/Users/unknown")

    assert r.status_code == 404
    assert r.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]


def test_an_unexpected_error_answers_500(app):
    """An unexpected exception answers 500, without its message."""
    with patch.object(
        app.service, "service_provider_config", side_effect=RuntimeError("secret")
    ):
        r = send(app, "GET", "/v2/ServiceProviderConfig")

    assert r.status_code == 500
    assert "secret" not in r.text


def test_check_auth_refuses_a_request(scim_provider):
    """A request that check_auth refuses answers 401, but the configuration stays open."""

    class ClosedApplication(ASGIApplication):
        def check_auth(self, request):
            raise UnauthorizedException

    app = ClosedApplication(AsyncInMemoryStorage(), scim_provider)

    assert send(app, "GET", "/v2/Users").status_code == 401
    assert send(app, "GET", "/v2/ServiceProviderConfig").status_code == 200


def test_a_large_streamed_body_answers_413():
    """A bulk body larger than maxPayloadSize answers 413, even when it arrives in chunks."""
    provider = load_default_provider()
    provider.config.bulk.max_payload_size = 10
    app = ASGIApplication(AsyncInMemoryStorage(), provider)
    chunk = {"type": "http.request", "body": b"x" * 8, "more_body": True}
    last = {"type": "http.request", "body": b"x" * 8, "more_body": False}

    sent = call(
        app,
        http_scope("POST", "/v2/Bulk", [(b"content-type", b"application/scim+json")]),
        [chunk, chunk, last],
    )

    assert sent[0]["status"] == 413


def test_the_host_falls_back_to_the_server(app):
    """Without Host header, the locations use the address of the server."""
    sent = call(
        app,
        http_scope("GET", "/v2/ServiceProviderConfig", server=("10.0.0.1", 8000)),
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    body = json.loads(sent[1]["body"])
    assert body["meta"]["location"] == "https://10.0.0.1:8000/v2/ServiceProviderConfig"


def test_the_lifespan_is_answered(app):
    """The application answers the startup and the shutdown of the server."""
    sent = call(
        app,
        {"type": "lifespan"},
        [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}],
    )

    assert [message["type"] for message in sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]


def test_an_unknown_path_with_a_body_answers_404(app):
    """A request with a body on an unknown path answers 404."""
    r = send(
        app, "POST", "/v2/Unknown/path/more", json={"userName": "bjensen"}, headers=JSON
    )

    assert r.status_code == 404


def test_the_root_path_is_part_of_the_urls(app):
    """An application mounted under a root path serves its paths, and keeps the root path in its URLs."""
    scope = http_scope("GET", "/directory/v2/ServiceProviderConfig")
    scope["root_path"] = "/directory"
    scope["headers"] = [(b"host", b"scim.example.com")]

    sent = call(app, scope, [{"type": "http.request", "body": b"", "more_body": False}])

    body = json.loads(sent[1]["body"])
    assert body["meta"]["location"] == (
        "https://scim.example.com/directory/v2/ServiceProviderConfig"
    )
