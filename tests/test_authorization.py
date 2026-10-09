import asyncio
import json

import pytest
from scim2_models import ForbiddenException
from scim2_models import SCIMException
from scim2_models import UnauthorizedException

from scim2_server.handler import AsyncScimHandler
from scim2_server.handler import ScimHandler
from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.requests import ScimRequest
from scim2_server.routing import Operation
from scim2_server.routing import Target
from scim2_server.service import ScimService

from .conftest import SECRET

BASE_URL = "https://scim.example/v2"
JSON = {"Content-Type": "application/scim+json"}
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
BULK_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:BulkRequest"
SEARCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:SearchRequest"


class RulesService(ScimService):
    """Refuse the operations the subject lists, and record what authorize receives.

    The subject maps an operation and a resource type name to the exception
    that refuses it.
    """

    def __init__(self, provider):
        super().__init__(provider)
        self.calls = []

    def me_target(self, request):
        return self.get_resource_type("User"), request.subject["user_id"]

    def me_creation_type(self, request):
        return self.get_resource_type("User")

    def authorize(self, request, target, resource_type):
        self.calls.append((target, resource_type.name))
        refusals = (request.subject or {}).get("refused", {})
        exception = refusals.get((target.operation, resource_type.name))
        if exception is not None:
            raise exception()


def refusing(
    *operations, exception: type[SCIMException] = ForbiddenException, **subject
):
    return {"refused": dict.fromkeys(operations, exception), **subject}


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
    service = RulesService(scim_provider)
    if request.param == "sync":
        return ScimHandler(service, InMemoryStorage())
    return AsyncScimHandler(service, AsyncInMemoryStorage())


def call(handler, method, path, body=None, subject=None, query=None):
    request = ScimRequest(
        method,
        BASE_URL,
        path,
        query=query or {},
        headers=JSON,
        body=json.dumps(body).encode() if body is not None else b"",
        subject=subject,
    )
    return serve(handler, request)


def create_user(handler, user_name="bjensen"):
    body = {"schemas": [USER_SCHEMA], "userName": user_name}
    return call(handler, "POST", "/Users", body).body["id"]


def create_group(handler, display_name="admins"):
    body = {"schemas": [GROUP_SCHEMA], "displayName": display_name}
    return call(handler, "POST", "/Groups", body).body["id"]


def patch_body(display_name):
    return {
        "schemas": [PATCH_SCHEMA],
        "Operations": [{"op": "replace", "path": "displayName", "value": display_name}],
    }


def bulk(handler, operations, subject, **attributes):
    body = {"schemas": [BULK_SCHEMA], "Operations": operations, **attributes}
    return call(handler, "POST", "/Bulk", body, subject).body["Operations"]


def user_names(handler):
    resources = call(handler, "GET", "/Users").body.get("Resources", [])
    return sorted(resource["userName"] for resource in resources)


def test_every_operation_is_accepted_by_default(scim_provider):
    """A service that does not override authorize serves every operation."""
    handler = ScimHandler(ScimService(scim_provider, secret=SECRET), InMemoryStorage())

    user_id = create_user(handler)

    assert call(handler, "GET", f"/Users/{user_id}").status == 200


@pytest.mark.parametrize(
    "method,operation",
    [
        ("GET", Operation.query),
        ("PUT", Operation.replace),
        ("PATCH", Operation.patch),
        ("DELETE", Operation.delete),
    ],
)
def test_a_refused_operation_on_a_resource_answers_403_and_changes_nothing(
    handler, method, operation
):
    """A refused read, replacement, PATCH or deletion answers 403, and the stored resource stays the same."""
    user_id = create_user(handler)
    body = {
        "PUT": {"schemas": [USER_SCHEMA], "userName": "bjensen", "displayName": "B"},
        "PATCH": patch_body("B"),
    }.get(method)

    response = call(
        handler,
        method,
        f"/Users/{user_id}",
        body,
        refusing((operation, "User")),
    )

    assert response.status == 403
    assert "displayName" not in call(handler, "GET", f"/Users/{user_id}").body


def test_a_refused_creation_answers_403_and_creates_nothing(handler):
    """A refused creation answers 403, and the resource is not created."""
    body = {"schemas": [USER_SCHEMA], "userName": "bjensen"}

    response = call(
        handler, "POST", "/Users", body, refusing((Operation.create, "User"))
    )

    assert response.status == 403
    assert user_names(handler) == []


def test_a_refused_client_gets_403_for_an_invalid_body(handler):
    """The authorization comes before the validation of the body."""
    request = ScimRequest(
        "POST",
        BASE_URL,
        "/Users",
        headers=JSON,
        body=b"{",
        subject=refusing((Operation.create, "User")),
    )

    assert serve(handler, request).status == 403


def test_a_refused_client_cannot_tell_whether_a_resource_exists(handler):
    """A refused read answers 403 for an unknown resource, as for a known one."""
    response = call(
        handler, "GET", "/Users/unknown", subject=refusing((Operation.query, "User"))
    )

    assert response.status == 403


def test_a_refusal_with_another_status_keeps_it(handler):
    """The exception of authorize answers the request, such as a 401."""
    response = call(
        handler,
        "GET",
        "/Users/unknown",
        subject=refusing((Operation.query, "User"), exception=UnauthorizedException),
    )

    assert response.status == 401


def test_a_refusal_only_covers_its_resource_type(handler):
    """A client refused on users still manages the groups."""
    response = call(
        handler,
        "POST",
        "/Groups",
        {"schemas": [GROUP_SCHEMA], "displayName": "admins"},
        refusing((Operation.create, "User")),
    )

    assert response.status == 201


def test_replacement_and_patch_are_authorized_apart(handler):
    """A client refused PUT can still PATCH."""
    user_id = create_user(handler)

    response = call(
        handler,
        "PATCH",
        f"/Users/{user_id}",
        patch_body("Babs"),
        refusing((Operation.replace, "User")),
    )

    assert response.status == 204


def test_authorize_receives_the_target_of_a_resource(handler):
    """A request on a resource is authorized with its operation, endpoint and identifier."""
    user_id = create_user(handler)
    handler.service.calls.clear()

    call(handler, "GET", f"/Users/{user_id}")

    assert handler.service.calls == [
        (Target(Operation.query, endpoint="Users", resource_id=user_id), "User")
    ]


def test_authorize_receives_the_resolved_target_of_me(handler):
    """A request on /Me is authorized once resolved, and its target tells it comes from /Me."""
    user_id = create_user(handler)
    handler.service.calls.clear()

    call(handler, "DELETE", "/Me", subject={"user_id": user_id})

    assert handler.service.calls == [
        (
            Target(Operation.delete, endpoint="Users", resource_id=user_id, me=True),
            "User",
        )
    ]


def test_authorize_receives_the_creation_on_me(handler):
    """A POST on /Me is authorized as a creation of the type of me_creation_type."""
    call(
        handler,
        "POST",
        "/Me",
        {"schemas": [USER_SCHEMA], "userName": "bjensen"},
        subject={},
    )

    assert handler.service.calls == [
        (Target(Operation.create, endpoint="Users", me=True), "User")
    ]


def test_a_refused_bulk_operation_only_fails_itself(handler):
    """RFC 7644 §3.7.3: a refused operation answers 403, and the other operations run."""
    operations = [
        {
            "method": "POST",
            "path": "/Groups",
            "bulkId": "g",
            "data": {"schemas": [GROUP_SCHEMA], "displayName": "admins"},
        },
        {
            "method": "POST",
            "path": "/Users",
            "bulkId": "u",
            "data": {"schemas": [USER_SCHEMA], "userName": "bjensen"},
        },
    ]

    results = bulk(handler, operations, refusing((Operation.create, "Group")))

    assert [result["status"] for result in results] == ["403", "201"]
    assert user_names(handler) == ["bjensen"]


@pytest.mark.parametrize(
    "method,operation",
    [
        ("PUT", Operation.replace),
        ("PATCH", Operation.patch),
        ("DELETE", Operation.delete),
    ],
)
def test_a_bulk_operation_is_authorized_with_its_own_operation(
    handler, method, operation
):
    """Each bulk operation is authorized with the operation and the resource it targets, not with the bulk request."""
    user_id = create_user(handler)
    handler.service.calls.clear()
    data = {
        "PUT": {"schemas": [USER_SCHEMA], "userName": "bjensen"},
        "PATCH": patch_body("Babs"),
    }.get(method)
    operation_body = {"method": method, "path": f"/Users/{user_id}"}
    if data is not None:
        operation_body["data"] = data

    (result,) = bulk(handler, [operation_body], refusing((operation, "User")))

    assert result["status"] == "403"
    assert handler.service.calls == [
        (Target(operation, endpoint="Users", resource_id=user_id), "User")
    ]


def test_a_refused_bulk_operation_with_invalid_data_answers_403(handler):
    """In a bulk request, the authorization comes before the validation error of an operation."""
    operation = {"method": "POST", "path": "/Users", "bulkId": "u", "data": {}}

    (result,) = bulk(handler, [operation], refusing((Operation.create, "User")))

    assert result["status"] == "403"


def test_a_bulk_operation_on_an_unknown_endpoint_is_not_authorized(handler):
    """An operation on an endpoint that serves no resource type answers 404, without authorization."""
    (result,) = bulk(handler, [{"method": "DELETE", "path": "/Unknown/x"}], {})

    assert result["status"] == "404"
    assert handler.service.calls == []


def test_a_bulk_operation_is_authorized_with_its_resolved_references(handler):
    """A "bulkId:" reference in the path reaches authorize as the identifier of the created resource."""
    operations = [
        {"method": "PATCH", "path": "/Users/bulkId:u", "data": patch_body("Babs")},
        {
            "method": "POST",
            "path": "/Users",
            "bulkId": "u",
            "data": {"schemas": [USER_SCHEMA], "userName": "bjensen"},
        },
    ]

    results = bulk(handler, operations, {})

    user_id = results[1]["location"].rsplit("/", 1)[1]
    assert (
        Target(Operation.patch, endpoint="Users", resource_id=user_id),
        "User",
    ) in handler.service.calls


def test_an_operation_referencing_a_refused_creation_fails(handler):
    """An operation referencing a refused POST answers 409, as for any failed creation."""
    operations = [
        {
            "method": "POST",
            "path": "/Users",
            "bulkId": "u",
            "data": {"schemas": [USER_SCHEMA], "userName": "bjensen"},
        },
        {
            "method": "POST",
            "path": "/Groups",
            "bulkId": "g",
            "data": {
                "schemas": [GROUP_SCHEMA],
                "displayName": "admins",
                "members": [{"value": "bulkId:u"}],
            },
        },
    ]

    results = bulk(handler, operations, refusing((Operation.create, "User")))

    assert [result["status"] for result in results] == ["403", "409"]


def test_refused_bulk_operations_count_as_errors(handler):
    """RFC 7644 §3.7.3: a refusal counts towards failOnErrors."""
    operations = [
        {
            "method": "POST",
            "path": "/Users",
            "bulkId": user_name,
            "data": {"schemas": [USER_SCHEMA], "userName": user_name},
        }
        for user_name in ("bjensen", "jsmith")
    ]

    results = bulk(
        handler,
        operations,
        refusing((Operation.create, "User")),
        failOnErrors=1,
    )

    assert [result["status"] for result in results] == ["403"]


@pytest.mark.parametrize(
    "method,path,body,operation",
    [
        ("GET", "/Users", None, Operation.search),
        (
            "POST",
            "/Users/.search",
            {"schemas": [SEARCH_SCHEMA]},
            Operation.search_with_body,
        ),
    ],
    ids=["get", "post"],
)
def test_a_refused_search_on_a_resource_type_answers_403(
    handler, method, path, body, operation
):
    """A search on the endpoint of a refused resource type answers 403."""
    create_user(handler)

    response = call(handler, method, path, body, refusing((operation, "User")))

    assert response.status == 403


@pytest.mark.parametrize(
    "method,path,body,operation",
    [
        ("GET", "/", None, Operation.search),
        ("POST", "/.search", {"schemas": [SEARCH_SCHEMA]}, Operation.search_with_body),
    ],
    ids=["get", "post"],
)
def test_a_search_at_the_root_leaves_out_the_refused_types(
    handler, method, path, body, operation
):
    """A search at the root only finds and counts the resources of the types the client may search."""
    create_user(handler)
    create_group(handler)
    handler.service.calls.clear()

    response = call(handler, method, path, body, refusing((operation, "Group")))

    assert response.status == 200
    assert response.body["totalResults"] == 1
    assert [r["userName"] for r in response.body["Resources"]] == ["bjensen"]
    assert (Target(operation), "Group") in handler.service.calls


def test_a_search_at_the_root_with_every_type_refused_answers_403(handler):
    """A search at the root answers 403 when the client may search no type."""
    response = call(
        handler,
        "GET",
        "/",
        subject=refusing((Operation.search, "User"), (Operation.search, "Group")),
    )

    assert response.status == 403


def test_a_search_at_the_root_fails_on_another_refusal(handler):
    """At the root, a refusal other than a 403 answers the request."""
    response = call(
        handler,
        "GET",
        "/",
        subject=refusing((Operation.search, "Group"), exception=UnauthorizedException),
    )

    assert response.status == 401


def test_a_search_at_the_root_validates_with_every_type(handler):
    """A filter on an attribute of a refused type stays valid, and matches nothing."""
    create_user(handler)
    create_group(handler)

    response = call(
        handler,
        "GET",
        "/",
        query={"filter": "members pr"},
        subject=refusing((Operation.search, "Group")),
    )

    assert response.status == 200
    assert response.body["totalResults"] == 0


@pytest.mark.parametrize(
    "path", ["/ServiceProviderConfig", "/ResourceTypes", "/Schemas"]
)
def test_discovery_endpoints_are_not_authorized(handler, path):
    """The discovery endpoints do not call authorize."""
    assert call(handler, "GET", path).status == 200
    assert handler.service.calls == []
