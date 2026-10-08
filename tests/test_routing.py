import asyncio

import pytest
from scim2_models import SCIMException

from scim2_server.handler import AsyncScimHandler
from scim2_server.handler import ScimHandler
from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.requests import ScimRequest
from scim2_server.service import ScimService

from .conftest import SECRET

BASE_URL = "https://scim.example/v2"
JSON = {"Content-Type": "application/scim+json"}
SEARCH = b'{"schemas": ["urn:ietf:params:scim:api:messages:2.0:SearchRequest"]}'


def scim_request(method, path, **kwargs):
    return ScimRequest(method, BASE_URL, path, **kwargs)


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
    service = ScimService(scim_provider, secret=SECRET)
    if request.param == "sync":
        return ScimHandler(service, InMemoryStorage())
    return AsyncScimHandler(service, AsyncInMemoryStorage())


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "status"),
    [
        ("GET", "/ServiceProviderConfig", {}, 200),
        ("GET", "/ResourceTypes", {}, 200),
        ("GET", "/ResourceTypes/User", {}, 200),
        ("GET", "/Schemas", {}, 200),
        ("GET", "/Schemas/urn:ietf:params:scim:schemas:core:2.0:User", {}, 200),
        ("GET", "/", {}, 200),
        ("GET", "", {}, 200),
        ("POST", "/.search", {"headers": JSON, "body": SEARCH}, 200),
        ("GET", "/Users", {}, 200),
        ("GET", "/Users/", {}, 200),
        ("POST", "/Users/.search", {"headers": JSON, "body": SEARCH}, 200),
        ("POST", "/Users", {"headers": JSON, "body": b'{"userName": "bjensen"}'}, 201),
        ("GET", "/Users/unknown", {}, 404),
        ("PUT", "/Users/unknown", {"headers": JSON, "body": b"{}"}, 404),
        ("DELETE", "/Users/unknown", {}, 404),
        ("GET", "/Unknown", {}, 404),
        ("GET", "/Users/unknown/more", {}, 404),
        ("GET", "/.search", {}, 405),
        ("GET", "/Me", {}, 501),
        ("POST", "/Me", {}, 501),
    ],
)
def test_handle_serves_each_route(handler, method, path, kwargs, status):
    """A request is served by the operation of its route, or answers its routing error."""
    response = serve(handler, scim_request(method, path, **kwargs))

    assert response.status == status


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("POST", "/Schemas", "GET"),
        ("GET", "/Bulk", "POST"),
        ("POST", "/Users/unknown", "DELETE, GET, PATCH, PUT"),
    ],
)
def test_a_method_the_endpoint_does_not_support_answers_405(
    handler, method, path, allowed
):
    """A 405 response lists the methods the endpoint supports (RFC 9110 §15.5.6)."""
    response = serve(handler, scim_request(method, path))

    assert response.status == 405
    assert response.headers["Allow"] == allowed


def test_a_request_for_another_operation_is_a_routing_bug(scim_provider):
    """An operation method refuses a request that its route does not serve."""
    handler = ScimHandler(ScimService(scim_provider, secret=SECRET), InMemoryStorage())

    with pytest.raises(RuntimeError, match="asks for create, not for query"):
        handler.query(scim_request("POST", "/Users"))


def test_the_headers_are_case_insensitive():
    """A header is read whatever the case of its name, and repeated headers are joined."""
    request = ScimRequest(
        "GET",
        BASE_URL,
        "/Users/1",
        headers=[("If-Match", 'W/"1"'), ("if-match", 'W/"2"')],
    )

    assert request.header("IF-MATCH") == 'W/"1", W/"2"'
    assert request.header("If-None-Match") is None


def test_the_method_is_case_insensitive(scim_provider):
    """A request is routed whatever the case of its method."""
    handler = ScimHandler(ScimService(scim_provider, secret=SECRET), InMemoryStorage())

    assert handler.handle(scim_request("get", "/Users")).status == 200


def test_a_request_can_be_completed(scim_provider):
    """A request built without body gets its body afterwards."""
    handler = ScimHandler(ScimService(scim_provider, secret=SECRET), InMemoryStorage())
    request = scim_request("POST", "/Users", headers=JSON)
    request.body = b'{"userName": "bjensen"}'

    assert handler.handle(request).status == 201


@pytest.mark.parametrize(
    ("path", "url"),
    [
        ("/ServiceProviderConfig", f"{BASE_URL}/ServiceProviderConfig"),
        ("/", BASE_URL),
    ],
)
def test_the_url_of_a_request(path, url):
    """The URL of a request is the base URL followed by its path."""
    assert ScimRequest("GET", f"{BASE_URL}/", path).url == url
