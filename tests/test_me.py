import asyncio
import json

import httpx2
import pytest
from scim2_models import NotFoundException
from scim2_models import SCIMException
from scim2_models import UnauthorizedException

from scim2_server.applications.wsgi import WSGIApplication
from scim2_server.handler import AsyncScimHandler
from scim2_server.handler import ScimHandler
from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.requests import ScimRequest
from scim2_server.service import ScimService

BASE_URL = "https://scim.example/v2"
JSON = {"Content-Type": "application/scim+json"}
PATCH = json.dumps(
    {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "displayName", "value": "Babs"}],
    }
).encode()


class MeService(ScimService):
    """Serve /Me with the resource whose type name and identifier are the subject."""

    def me_target(self, request):
        if request.subject is None:
            raise UnauthorizedException
        if request.subject == "nobody":
            raise NotFoundException(detail="No resource for this client")
        name, resource_id = request.subject
        return self.get_resource_type(name), resource_id

    def me_creation_type(self, request):
        return self.get_resource_type("User")


def serve(handler, request):
    try:
        response = handler.handle(request)
        if asyncio.iscoroutine(response):
            response = asyncio.run(response)
    except SCIMException as exception:
        return handler.service.error_response(exception)
    return response


@pytest.fixture(params=["sync", "async"])
def handler(request, scim_provider):
    service = MeService(scim_provider)
    if request.param == "sync":
        return ScimHandler(service, InMemoryStorage())
    return AsyncScimHandler(service, AsyncInMemoryStorage())


def create(handler, endpoint, body):
    request = ScimRequest("POST", BASE_URL, f"/{endpoint}", headers=JSON, body=body)
    return serve(handler, request).body["id"]


def me(method, subject, **kwargs):
    return ScimRequest(method, BASE_URL, "/Me", subject=subject, **kwargs)


def test_me_serves_the_resource_of_the_subject(handler):
    """A GET on /Me reads the resource of the subject, and gives its location."""
    user_id = create(handler, "Users", b'{"userName": "bjensen"}')

    response = serve(handler, me("GET", ("User", user_id)))

    assert response.status == 200
    assert response.body["id"] == user_id
    assert response.headers["Location"] == f"{BASE_URL}/Users/{user_id}"
    assert response.body["meta"]["location"] == response.headers["Location"]
    assert response.headers["Content-Location"] == response.headers["Location"]


def test_me_can_stand_for_another_resource_type(handler):
    """/Me serves the resource of the subject, whatever its type."""
    group_id = create(handler, "Groups", b'{"displayName": "admins"}')

    response = serve(handler, me("GET", ("Group", group_id)))

    assert response.body["displayName"] == "admins"
    assert response.headers["Location"] == f"{BASE_URL}/Groups/{group_id}"


@pytest.mark.parametrize(
    ("method", "kwargs", "status"),
    [
        ("PUT", {"headers": JSON, "body": b'{"userName": "bjensen"}'}, 200),
        ("PATCH", {"headers": JSON, "body": PATCH}, 204),
        ("DELETE", {}, 204),
    ],
)
def test_me_changes_the_resource_of_the_subject(handler, method, kwargs, status):
    """A PUT, a PATCH or a DELETE on /Me acts on the resource of the subject, and gives its location."""
    user_id = create(handler, "Users", b'{"userName": "bjensen"}')

    response = serve(handler, me(method, ("User", user_id), **kwargs))

    assert response.status == status
    assert response.headers["Location"] == f"{BASE_URL}/Users/{user_id}"


def test_post_on_me_creates_at_the_chosen_endpoint(handler):
    """A POST on /Me creates a resource at the endpoint the service chooses."""
    response = serve(
        handler, me("POST", None, headers=JSON, body=b'{"userName": "bjensen"}')
    )

    assert response.status == 201
    assert response.headers["Location"].startswith(f"{BASE_URL}/Users/")


@pytest.mark.parametrize(("subject", "status"), [(None, 401), ("nobody", 404)])
def test_me_without_a_resource(handler, subject, status):
    """/Me answers the error the service raises when it finds no resource."""
    assert serve(handler, me("GET", subject)).status == status


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE", "POST"])
def test_me_is_not_implemented_by_default(scim_provider, method):
    """Without an override of the service, every method on /Me answers 501."""
    handler = ScimHandler(ScimService(scim_provider), InMemoryStorage())

    assert serve(handler, me(method, ("User", "1"))).status == 501


def test_the_application_passes_its_subject(storage, scim_provider):
    """The subject that WSGIApplication returns is passed to the service."""

    class MeApplication(WSGIApplication):
        def get_subject(self, request):
            user_id = request.header("X-User")
            return None if user_id is None else ("User", user_id)

    app = MeApplication(storage, scim_provider, service=MeService(scim_provider))
    with httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="https://scim.example.com"
    ) as client:
        user_id = client.post("/v2/Users", json={"userName": "bjensen"}).json()["id"]
        response = client.get("/v2/Me", headers={"X-User": user_id})

    assert response.status_code == 200
    assert response.headers["Location"] == (
        f"https://scim.example.com/v2/Users/{user_id}"
    )


def test_an_unknown_resource_type_name(scim_provider):
    """Looking up a resource type of an unknown name is a programming error."""
    with pytest.raises(ValueError, match="No resource type named 'Device'"):
        ScimService(scim_provider).get_resource_type("Device")
