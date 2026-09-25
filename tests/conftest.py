import importlib.resources
import json

import httpx2
import pytest
from scim2_models import ScimProvider

from scim2_server.backend import InMemoryBackend
from scim2_server.provider import SCIMApplication
from scim2_server.utils import load_default_provider
from scim2_server.utils import load_default_resource_types
from scim2_server.utils import load_default_schemas


@pytest.fixture(scope="session")
def scim_provider():
    return load_default_provider()


@pytest.fixture(scope="session")
def user_type(scim_provider):
    return next(rt for rt in scim_provider.resource_types if rt.id == "User")


@pytest.fixture
def backend():
    return InMemoryBackend()


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


@pytest.fixture
def app(backend, scim_provider):
    return SCIMApplication(backend, scim_provider)


@pytest.fixture
def wsgi(app):
    transport = httpx2.WSGITransport(app=app)
    client = httpx2.Client(transport=transport, base_url="https://scim.example.com")
    client.__enter__()
    yield client
    app.backend.resources = []
    client.__exit__(None, None, None)


@pytest.fixture
def wsgi_with(backend, scim_provider):
    """Build clients of applications serving the default resources under another configuration."""
    clients = []

    def build(config):
        provider = ScimProvider(
            models=scim_provider.models,
            resource_types=scim_provider.resource_types,
            config=config,
        )
        transport = httpx2.WSGITransport(app=SCIMApplication(backend, provider))
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
