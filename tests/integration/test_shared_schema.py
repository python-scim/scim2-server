import httpx2
import pytest
from scim2_models import ResourceType
from scim2_models import ScimProvider

from scim2_server.backend import InMemoryBackend
from scim2_server.provider import SCIMApplication
from scim2_server.utils import load_default_resource_types
from scim2_server.utils import load_default_schemas
from scim2_server.utils import load_default_service_provider_config

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"


@pytest.fixture
def wsgi():
    """Build a client of a service serving users and admins with the same schema."""
    admin = ResourceType(
        id="Admin", name="Admin", endpoint="/Admins", schema=USER_SCHEMA
    )
    provider = ScimProvider.from_discovery(
        load_default_schemas().values(),
        [*load_default_resource_types().values(), admin],
        config=load_default_service_provider_config(),
    )
    transport = httpx2.WSGITransport(app=SCIMApplication(InMemoryBackend(), provider))
    with httpx2.Client(
        transport=transport, base_url="https://scim.example.com"
    ) as client:
        yield client


def test_resource_types_sharing_a_schema_serve_disjoint_resources(wsgi):
    """A resource belongs to the resource type it was created through only."""
    user_id = wsgi.post("/v2/Users", json={"userName": "bjensen"}).json()["id"]
    admin = wsgi.post("/v2/Admins", json={"userName": "root"}).json()
    assert admin["meta"]["resourceType"] == "Admin"
    assert (
        admin["meta"]["location"] == f"https://scim.example.com/v2/Admins/{admin['id']}"
    )

    assert wsgi.get(f"/v2/Users/{admin['id']}").status_code == 404
    assert wsgi.get(f"/v2/Admins/{user_id}").status_code == 404
    assert wsgi.delete(f"/v2/Users/{admin['id']}").status_code == 404
    assert [r["id"] for r in wsgi.get("/v2/Users").json()["Resources"]] == [user_id]
    assert [r["id"] for r in wsgi.get("/v2/Admins").json()["Resources"]] == [
        admin["id"]
    ]


def test_a_root_search_spans_the_resource_types_sharing_a_schema(wsgi):
    """RFC 7644 §3.4.2.1: a search on the root returns the resources of every type, each with its resourceType."""
    wsgi.post("/v2/Users", json={"userName": "bjensen"})
    wsgi.post("/v2/Admins", json={"userName": "root"})
    resources = wsgi.get("/v2/").json()["Resources"]
    assert sorted(r["meta"]["resourceType"] for r in resources) == ["Admin", "User"]

    r = wsgi.post(
        "/v2/.search",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:SearchRequest"],
            "filter": 'userName eq "root"',
        },
    )
    assert [r["meta"]["resourceType"] for r in r.json()["Resources"]] == ["Admin"]


def test_a_shared_schema_is_published_once(wsgi):
    """The schema two resource types share appears once on /Schemas."""
    schemas = wsgi.get("/v2/Schemas").json()["Resources"]
    assert [s["id"] for s in schemas].count(USER_SCHEMA) == 1
    resource_types = wsgi.get("/v2/ResourceTypes").json()["Resources"]
    assert {rt["id"] for rt in resource_types} == {"User", "Group", "Admin"}


@pytest.mark.parametrize("user_name", ["bjensen", "BJensen"])
def test_uniqueness_spans_the_resource_types_sharing_a_schema(wsgi, user_name):
    """RFC 7643 erratum 8279: uniqueness holds among the resources using the same schema."""
    wsgi.post("/v2/Users", json={"userName": "bjensen"})
    r = wsgi.post("/v2/Admins", json={"userName": user_name})
    assert r.status_code == 409
    assert r.json()["scimType"] == "uniqueness"


def test_a_group_holds_members_of_another_resource_type(wsgi):
    """The type and $ref of a member may name any resource type."""
    admin_id = wsgi.post("/v2/Admins", json={"userName": "root"}).json()["id"]
    member = {
        "value": admin_id,
        "$ref": f"https://scim.example.com/v2/Admins/{admin_id}",
        "type": "Admin",
    }
    r = wsgi.post("/v2/Groups", json={"displayName": "Operators", "members": [member]})
    assert r.status_code == 201
    assert r.json()["members"] == [member]
