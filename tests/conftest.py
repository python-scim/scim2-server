import asyncio
import importlib.resources
import json

import httpx2
import pytest
from scim2_models import ScimProvider

from scim2_server.handler import AsyncScimHandler
from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.utils import load_default_provider
from scim2_server.utils import load_default_resource_types
from scim2_server.utils import load_default_schemas
from scim2_server.wsgi import WSGIApplication


@pytest.fixture(scope="session")
def scim_provider():
    return load_default_provider()


@pytest.fixture(scope="session")
def user_type(scim_provider):
    return next(rt for rt in scim_provider.resource_types if rt.id == "User")


@pytest.fixture
def storage():
    return InMemoryStorage()


@pytest.fixture(scope="session")
def static_data():
    return load_default_schemas(), load_default_resource_types()


def load_json_resource(json_name: str) -> list:
    fp = importlib.resources.files("tests") / json_name
    with open(fp) as f:
        return json.load(f)


@pytest.fixture(scope="session")
def fake_user_data():
    return load_json_resource("fake_user_data.json")


class BlockingHandler:
    """Serve the requests of a SCIM application with an AsyncScimHandler, one coroutine at a time."""

    def __init__(self, handler):
        self.handler = handler

    def __getattr__(self, name):
        method = getattr(self.handler, name)

        def call(*args, **kwargs):
            return asyncio.run(method(*args, **kwargs))

        return call


@pytest.fixture(params=["sync", "async"])
def app(request, storage, scim_provider):
    """Return a SCIM application, served by the synchronous handler, then by the asynchronous one."""
    app = WSGIApplication(storage, scim_provider)
    if request.param == "async":
        app.handler = BlockingHandler(
            AsyncScimHandler(app.service, AsyncInMemoryStorage(storage))
        )
    return app


@pytest.fixture
def wsgi(app):
    transport = httpx2.WSGITransport(app=app)
    client = httpx2.Client(transport=transport, base_url="https://scim.example.com")
    client.__enter__()
    yield client
    app.storage.resources = []
    client.__exit__(None, None, None)


@pytest.fixture
def wsgi_with(storage, scim_provider):
    """Build clients of applications serving the default resources under another configuration or policy."""
    clients = []

    def build(config=None, policy=None):
        provider = ScimProvider(
            models=scim_provider.models,
            resource_types=scim_provider.resource_types,
            config=config or scim_provider.config,
            policy=policy,
        )
        transport = httpx2.WSGITransport(app=WSGIApplication(storage, provider))
        client = httpx2.Client(transport=transport, base_url="https://scim.example.com")
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.close()


@pytest.fixture
def first_fake_user(wsgi, fake_user_data):
    r = wsgi.post("/v2/Users", json=fake_user_data[0])
    return r.json()["id"]
