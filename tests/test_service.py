import asyncio

import pytest
from pydantic import ValidationError
from scim2_models import NotFoundException
from scim2_models import SCIMException
from scim2_models import SearchRequest

from scim2_server.conditions import NO_CONDITIONS
from scim2_server.handler import AsyncScimHandler
from scim2_server.handler import ScimHandler
from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.service import ScimService
from scim2_server.service import is_json_media_type


@pytest.mark.parametrize(
    ("content_type", "is_json"),
    [
        ("application/scim+json", True),
        ("application/scim+json; charset=utf-8", True),
        ("Application/SCIM+JSON", True),
        ("application/json", True),
        ("application/vnd.example+json", True),
        ("text/plain", False),
        ("text/json", False),
        ("", False),
        (None, False),
    ],
)
def test_json_media_types(content_type, is_json):
    """SCIM, plain and suffixed JSON media types are accepted, whatever their parameters."""
    assert is_json_media_type(content_type) is is_json


def test_error_response(scim_provider):
    """An exception becomes the SCIM error response of its status."""
    result = ScimService(scim_provider).error_response(NotFoundException(detail="Gone"))

    assert result.status == 404
    assert result.body["detail"] == "Gone"
    assert result.headers["Content-Type"] == "application/scim+json"


def test_unexpected_error_response(scim_provider):
    """An unexpected exception becomes a 500 that discloses nothing."""
    result = ScimService(scim_provider).error_response(RuntimeError("secret"))

    assert result.status == 500
    assert result.body["detail"] == "Internal server error"


def test_a_validation_error_is_unexpected(scim_provider):
    """A pydantic validation error that reaches the error conversion is a bug, and becomes a 500."""
    with pytest.raises(ValidationError) as excinfo:
        SearchRequest.model_validate({"count": "many"})

    result = ScimService(scim_provider).error_response(excinfo.value)

    assert result.status == 500


INVALID_REQUESTS = {
    "creation": lambda handler: handler.create(
        "https://scim.example/v2",
        "Users",
        b'{"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"]}',
        "application/scim+json",
    ),
    "query": lambda handler: handler.query(
        "https://scim.example/v2",
        "Users",
        "unknown",
        {"attributes": "userName", "excludedAttributes": "displayName"},
        NO_CONDITIONS,
    ),
    "search": lambda handler: handler.search(
        "https://scim.example/v2", "Users", {"count": "many"}
    ),
}


@pytest.mark.parametrize("request_of", INVALID_REQUESTS.values(), ids=INVALID_REQUESTS)
def test_an_invalid_request_raises_a_scim_exception(scim_provider, request_of):
    """The handler turns the validation errors of a request into SCIM exceptions answering 400."""
    handler = ScimHandler(ScimService(scim_provider), InMemoryStorage())

    with pytest.raises(SCIMException) as excinfo:
        request_of(handler)

    assert excinfo.value.status == 400


@pytest.mark.parametrize("request_of", INVALID_REQUESTS.values(), ids=INVALID_REQUESTS)
def test_an_invalid_request_raises_a_scim_exception_asynchronously(
    scim_provider, request_of
):
    """The asynchronous handler turns the validation errors of a request into SCIM exceptions."""
    handler = AsyncScimHandler(ScimService(scim_provider), AsyncInMemoryStorage())

    with pytest.raises(SCIMException) as excinfo:
        asyncio.run(request_of(handler))

    assert excinfo.value.status == 400
