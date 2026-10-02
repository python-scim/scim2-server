import asyncio

import pytest
from pydantic import ValidationError
from scim2_models import AuthenticationScheme
from scim2_models import ForbiddenException
from scim2_models import NotFoundException
from scim2_models import SCIMException
from scim2_models import SearchRequest
from scim2_models import UnauthorizedException

from scim2_server.conditions import NO_CONDITIONS
from scim2_server.handler import AsyncScimHandler
from scim2_server.handler import ScimHandler
from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.memory import InMemoryStorage
from scim2_server.service import ScimService
from scim2_server.service import is_json_media_type
from scim2_server.utils import load_default_provider


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


def scheme(type):
    return AuthenticationScheme(type=type, name=type, description=type)


def service_announcing(*types):
    provider = load_default_provider()
    provider.config.authentication_schemes = [scheme(type) for type in types]
    return ScimService(provider)


@pytest.mark.parametrize(
    ("types", "challenge"),
    [
        (["oauthbearertoken"], 'Bearer realm="SCIM"'),
        (["oauth2", "oauthbearertoken"], 'Bearer realm="SCIM"'),
        (["httpbasic", "oauthbearertoken"], 'Basic realm="SCIM", Bearer realm="SCIM"'),
    ],
)
def test_a_401_announces_the_authentication_schemes(types, challenge):
    """A 401 response carries one challenge per announced Bearer or Basic scheme."""
    result = service_announcing(*types).error_response(UnauthorizedException())

    assert result.status == 401
    assert result.headers["WWW-Authenticate"] == challenge


@pytest.mark.parametrize("types", [[], ["httpdigest", "oauth"]])
def test_a_401_without_known_scheme_has_no_challenge(types):
    """A 401 response has no WWW-Authenticate header when no Bearer or Basic scheme is announced."""
    result = service_announcing(*types).error_response(UnauthorizedException())

    assert "WWW-Authenticate" not in result.headers


def test_only_a_401_announces_the_schemes():
    """A 403 response carries no WWW-Authenticate header."""
    result = service_announcing("oauthbearertoken").error_response(ForbiddenException())

    assert "WWW-Authenticate" not in result.headers


def test_the_challenge_can_be_overridden(scim_provider):
    """A subclass of the service builds its own challenge, such as an RFC 9728 one."""

    class MetadataService(ScimService):
        def www_authenticate(self, exception):
            return 'Bearer resource_metadata="https://scim.example/.well-known/oauth-protected-resource"'

    result = MetadataService(scim_provider).error_response(UnauthorizedException())

    assert result.headers["WWW-Authenticate"].startswith("Bearer resource_metadata=")
