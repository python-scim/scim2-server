import httpx2
import pytest

from scim2_server.memory import InMemoryStorage
from scim2_server.provider import SCIMApplication
from scim2_server.tenants import TenantDispatcher

BASE_URL = "https://scim.example.com"


@pytest.fixture
def factory(scim_provider):
    return lambda tenant: SCIMApplication(InMemoryStorage(), scim_provider)


def make_client(dispatcher, script_name=""):
    transport = httpx2.WSGITransport(app=dispatcher, script_name=script_name)
    return httpx2.Client(transport=transport, base_url=BASE_URL)


@pytest.fixture
def dispatcher(factory):
    return TenantDispatcher(factory)


@pytest.fixture
def client(dispatcher):
    with make_client(dispatcher) as client:
        yield client


def test_resources_are_isolated_between_tenants(client, fake_user_data):
    """A resource created in a tenant is not visible from another tenant."""
    user_id = client.post("/a/v2/Users", json=fake_user_data[0]).json()["id"]

    assert client.get(f"/a/v2/Users/{user_id}").status_code == 200
    assert client.get(f"/b/v2/Users/{user_id}").status_code == 404
    assert client.get("/a/v2/Users").json()["totalResults"] == 1
    assert client.get("/b/v2/Users").json()["totalResults"] == 0


def test_same_user_name_in_two_tenants(client, fake_user_data):
    """Uniqueness only considers the resources of the tenant."""
    assert client.post("/a/v2/Users", json=fake_user_data[0]).status_code == 201
    assert client.post("/b/v2/Users", json=fake_user_data[0]).status_code == 201
    assert client.post("/a/v2/Users", json=fake_user_data[0]).status_code == 409


def test_location_includes_the_tenant(client, fake_user_data):
    """The location of a resource starts with the prefix of its tenant."""
    r = client.post("/a/v2/Users", json=fake_user_data[0])
    location = f"{BASE_URL}/a/v2/Users/{r.json()['id']}"
    assert r.headers["Location"] == location
    assert r.json()["meta"]["location"] == location
    assert client.get("/a/Users").json()["Resources"][0]["meta"]["location"] == (
        location
    )


def test_bulk_location_includes_the_tenant(client, fake_user_data):
    """The location of a bulk operation starts with the prefix of its tenant."""
    r = client.post(
        "/a/v2/Bulk",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:BulkRequest"],
            "Operations": [
                {
                    "method": "POST",
                    "path": "/Users",
                    "bulkId": "u",
                    "data": fake_user_data[0],
                }
            ],
        },
    )
    assert r.json()["Operations"][0]["location"].startswith(f"{BASE_URL}/a/v2/Users/")


def test_discovery_is_served_in_every_tenant(client):
    """The discovery endpoints are available in each tenant, under its prefix."""
    r = client.get("/a/v2/ServiceProviderConfig")
    assert r.status_code == 200
    assert r.json()["meta"]["location"] == f"{BASE_URL}/a/v2/ServiceProviderConfig"


def test_tenant_under_a_mount_prefix(dispatcher, fake_user_data):
    """The tenant comes after the prefix the dispatcher is mounted under."""
    with make_client(dispatcher, script_name="/scim") as client:
        r = client.post("/a/v2/Users", json=fake_user_data[0])
    assert r.json()["meta"]["location"].startswith(f"{BASE_URL}/scim/a/v2/Users/")


def test_tenant_is_built_once(client, dispatcher):
    """The first request to a tenant builds its application, later ones reuse it."""
    assert dispatcher.applications == {}
    client.get("/a/v2/Users")
    application = dispatcher.applications["a"]
    client.get("/a/v2/Users")
    assert dispatcher.applications == {"a": application}


def test_unknown_tenant(factory, fake_user_data):
    """A tenant the factory does not build gets a 404."""
    dispatcher = TenantDispatcher(
        lambda tenant: factory(tenant) if tenant == "a" else None
    )
    with make_client(dispatcher) as client:
        assert client.post("/a/v2/Users", json=fake_user_data[0]).status_code == 201
        r = client.get("/b/v2/Users")
    assert r.status_code == 404
    assert r.headers["Content-Type"] == "application/scim+json"
    assert r.json() == {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": "404",
        "detail": "Unknown tenant",
    }
    assert list(dispatcher.applications) == ["a"]


def test_unknown_tenant_is_asked_again(factory):
    """A tenant that did not exist is served once the factory builds it."""
    existing = set()
    dispatcher = TenantDispatcher(
        lambda tenant: factory(tenant) if tenant in existing else None
    )
    with make_client(dispatcher) as client:
        assert client.get("/a/v2/Users").status_code == 404
        existing.add("a")
        assert client.get("/a/v2/Users").status_code == 200


@pytest.mark.parametrize("path", ["/", "/v2/Users", "//Users"])
def test_request_without_tenant(dispatcher, path):
    """A request whose path has no valid tenant gets a 404 without calling the factory."""
    calls = []
    dispatcher.factory = calls.append
    with make_client(dispatcher) as client:
        r = client.get(path)
    assert r.status_code == 404
    assert r.json()["detail"] == "Unknown tenant"
    assert calls == []


def test_tenant_from_a_header(factory, fake_user_data):
    """A subclass can read the tenant from a header and keep the path unchanged."""

    class HeaderTenantDispatcher(TenantDispatcher):
        def select_tenant(self, environ):
            return environ.get("HTTP_X_TENANT")

    dispatcher = HeaderTenantDispatcher(factory)
    with make_client(dispatcher) as client:
        r = client.post("/v2/Users", json=fake_user_data[0], headers={"X-Tenant": "a"})
        assert r.json()["meta"]["location"].startswith(f"{BASE_URL}/v2/Users/")
        assert (
            client.get("/v2/Users", headers={"X-Tenant": "b"}).json()["totalResults"]
            == 0
        )
        assert client.get("/v2/Users").status_code == 404
