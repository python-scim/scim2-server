import importlib.resources
import json

import httpx2
import pytest

from scim2_server.backend import InMemoryBackend
from scim2_server.provider import SCIMApplication
from scim2_server.utils import load_default_resource_types
from scim2_server.utils import load_default_schemas


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
def app(backend, static_data):
    app = SCIMApplication(backend)
    for schema in static_data[0].values():
        app.register_schema(schema)
    for resource_type in static_data[1].values():
        app.register_resource_type(resource_type)
    return app


@pytest.fixture
def wsgi(app):
    transport = httpx2.WSGITransport(app=app)
    client = httpx2.Client(transport=transport, base_url="https://scim.example.com")
    client.__enter__()
    yield client
    app.backend.resources = []
    client.__exit__(None, None, None)


@pytest.fixture
def first_fake_user(wsgi, fake_user_data):
    r = wsgi.post("/v2/Users", json=fake_user_data[0])
    return r.json()["id"]
