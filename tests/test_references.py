from typing import Annotated

import pytest
from scim2_models import URI
from scim2_models import External
from scim2_models import Meta
from scim2_models import Reference
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ScimProvider
from scim2_models import Uniqueness
from scim2_models import User

from scim2_server.service import ScimService

from .conftest import SECRET

BASE_URL = "https://scim.example.com/v2"


def group_with_member(wsgi, ref):
    return wsgi.post(
        "/v2/Groups",
        json={"displayName": "Admins", "members": [{"value": "1", "$ref": ref}]},
    )


def member_ref(group):
    return group["members"][0]["$ref"]


def test_a_relative_reference_becomes_the_location_of_the_resource(wsgi):
    """A reference relative to the SCIM root is returned as the URL of the resource."""
    user = wsgi.post("/v2/Users", json={"userName": "bjensen"}).json()
    group = group_with_member(wsgi, f"Users/{user['id']}").json()

    assert member_ref(group) == user["meta"]["location"]
    assert (
        member_ref(wsgi.get(f"/v2/Groups/{group['id']}").json())
        == (user["meta"]["location"])
    )
    listed = wsgi.get("/v2/Groups").json()["Resources"][0]
    assert member_ref(listed) == user["meta"]["location"]


def test_the_stored_reference_stays_relative(wsgi):
    """Publishing a resource does not change the stored resource."""
    group_with_member(wsgi, "Users/1")

    stored = wsgi._transport.app.storage.resources[0]
    assert stored.members[0].ref == "Users/1"


@pytest.mark.parametrize(
    "ref",
    [
        "https://other.example.com/v2/Users/1",
        "/v2/Users/1",
    ],
)
def test_an_absolute_reference_is_kept(wsgi, ref):
    """A reference with a scheme or an absolute path is returned as is."""
    group = group_with_member(wsgi, ref).json()

    assert member_ref(group) == ref


@pytest.mark.parametrize(
    ("ref", "location"),
    [
        ("Devices/1", f"{BASE_URL}/Devices/1"),
        ("Users/", f"{BASE_URL}/Users/"),
        ("1", f"{BASE_URL}/1"),
    ],
)
def test_a_relative_reference_without_resource_type_is_resolved_against_the_root(
    wsgi, ref, location
):
    """A relative reference that does not lead to a resource of a served type is joined to the SCIM root."""
    group = group_with_member(wsgi, ref).json()

    assert member_ref(group) == location


def test_a_reference_in_an_extension_is_resolved(wsgi):
    """The references of the extensions are resolved too."""
    enterprise = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
    user = wsgi.post(
        "/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User", enterprise],
            "userName": "bjensen",
            enterprise: {"manager": {"value": "1", "$ref": "Users/1"}},
        },
    ).json()

    assert user[enterprise]["manager"]["$ref"] == f"{BASE_URL}/Users/1"


def test_a_group_without_members_is_published(wsgi):
    """A resource without any reference is published unchanged."""
    group = wsgi.post("/v2/Groups", json={"displayName": "Admins"}).json()

    assert "members" not in group


def test_the_location_of_the_references_follows_resource_location(scim_provider):
    """Serving the resources at other URLs changes the URLs of the references too."""

    class Service(ScimService):
        def resource_location(self, base_url, resource_type, resource_id):
            return f"{base_url}/{resource_type.id.lower()}s/{resource_id}"

    service = Service(scim_provider)

    assert service.reference_location(BASE_URL, "Users/1") == f"{BASE_URL}/users/1"


class Badge(Resource):
    __schema__ = "urn:example:2.0:Badge"

    code: Annotated[str | None, Uniqueness.server] = None
    owner: Reference["User"] | None = None
    holders: list[Reference["User"]] | None = None
    anything: Reference | None = None
    schema_ref: Reference[URI] | None = None
    picture: Reference[External] | None = None


def test_references_outside_complex_attributes_are_resolved():
    """Single and multi-valued reference attributes are resolved, other references are kept."""
    service = ScimService(
        ScimProvider(
            models=[Badge], resource_types=[ResourceType.from_resource(Badge)]
        ),
        secret=SECRET,
    )
    badge = Badge(
        id="1",
        meta=Meta(resource_type="Badge"),
        owner="Users/1",
        holders=["Users/2", "Users/3"],
        anything="Users/4",
        schema_ref="urn:example:2.0:Badge",
        picture="/badge.png",
    )

    published = service.publish(BASE_URL, badge)

    assert published.owner == f"{BASE_URL}/Users/1"
    assert published.holders == [f"{BASE_URL}/Users/2", f"{BASE_URL}/Users/3"]
    assert published.anything == "Users/4"
    assert published.schema_ref == "urn:example:2.0:Badge"
    assert published.picture == "/badge.png"
    assert badge.owner == "Users/1"
