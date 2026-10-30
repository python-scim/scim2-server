import base64

import httpx2
import pytest

from scim2_server.memory import InMemoryStorage
from scim2_server.testserver.application import BASIC_SCHEME
from scim2_server.testserver.application import BEARER_TOKEN_SCHEME
from scim2_server.testserver.application import StaticCredentialsApplication
from scim2_server.utils import load_default_provider


def basic(credentials):
    return {"Authorization": "Basic " + base64.b64encode(credentials).decode()}


@pytest.fixture
def make_client():
    clients = []

    def build(bearer_tokens=(), basic_credentials=()):
        provider = load_default_provider()
        provider.config.authentication_schemes = [
            *([BEARER_TOKEN_SCHEME] if bearer_tokens else []),
            *([BASIC_SCHEME] if basic_credentials else []),
        ]
        app = StaticCredentialsApplication(
            InMemoryStorage(),
            provider,
            bearer_tokens=bearer_tokens,
            basic_credentials=basic_credentials,
        )
        client = httpx2.Client(
            transport=httpx2.WSGITransport(app=app),
            base_url="https://scim.example.com",
        )
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.close()


def test_every_request_is_accepted_without_token(make_client):
    """Without bearer token nor Basic credentials, a request without credentials is served."""
    r = make_client().get("/v2/Users")

    assert r.status_code == 200
    assert "WWW-Authenticate" not in r.headers


def test_a_request_without_credentials_is_refused(make_client):
    """With a bearer token, a request without credentials gets a 401 announcing the bearer scheme."""
    r = make_client(["s3cret"]).get("/v2/Users")

    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == 'Bearer realm="SCIM"'


def test_a_request_with_an_unknown_token_is_refused(make_client):
    """A request with another token than the registered ones gets a 401 announcing the bearer scheme."""
    r = make_client(["s3cret"]).get(
        "/v2/Users", headers={"Authorization": "Bearer other"}
    )

    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == 'Bearer realm="SCIM"'


def test_a_request_with_a_registered_token_is_served(make_client):
    """A request with any of the registered tokens is served."""
    client = make_client(["s3cret", "other"])

    assert (
        client.get("/v2/Users", headers={"Authorization": "Bearer other"}).status_code
        == 200
    )


def test_the_service_provider_config_stays_open(make_client):
    """The service provider configuration is served without credentials, and announces the bearer scheme."""
    r = make_client(["s3cret"]).get("/v2/ServiceProviderConfig")

    assert r.status_code == 200
    assert r.json()["authenticationSchemes"][0]["type"] == "oauthbearertoken"


def test_a_request_with_registered_basic_credentials_is_served(make_client):
    """A request with any of the registered Basic credentials is served."""
    client = make_client(basic_credentials=[("alice", "s3cret"), ("bob", "other")])

    assert client.get("/v2/Users", headers=basic(b"bob:other")).status_code == 200


def test_a_password_can_hold_a_colon(make_client):
    """The Basic user ends at the first colon, so the password can hold colons."""
    client = make_client(basic_credentials=[("alice", "s3:cr:et")])

    assert client.get("/v2/Users", headers=basic(b"alice:s3:cr:et")).status_code == 200


def test_a_utf8_password_is_accepted(make_client):
    """Basic credentials are decoded as UTF-8."""
    client = make_client(basic_credentials=[("alice", "sécret")])

    assert (
        client.get("/v2/Users", headers=basic("alice:sécret".encode())).status_code
        == 200
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        basic(b"alice:wrong"),
        basic(b"bob:s3cret"),
        basic(b"alice"),
        basic(b"\xffalice:s3cret"),
        {"Authorization": "Basic not base64!"},
        {"Authorization": "Bearer s3cret"},
    ],
    ids=[
        "no credentials",
        "wrong password",
        "unknown user",
        "no colon",
        "not utf-8",
        "not base64",
        "bearer scheme",
    ],
)
def test_a_request_without_valid_basic_credentials_is_refused(make_client, headers):
    """A request without valid Basic credentials gets a 401 announcing the Basic scheme."""
    r = make_client(basic_credentials=[("alice", "s3cret")]).get(
        "/v2/Users", headers=headers
    )

    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == 'Basic realm="SCIM"'


def test_bearer_tokens_and_basic_credentials_are_both_accepted(make_client):
    """With both schemes, a request is served with either one, and a 401 announces both."""
    client = make_client(["token"], [("alice", "s3cret")])

    assert (
        client.get("/v2/Users", headers={"Authorization": "Bearer token"}).status_code
        == 200
    )
    assert client.get("/v2/Users", headers=basic(b"alice:s3cret")).status_code == 200
    r = client.get("/v2/Users")
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == 'Bearer realm="SCIM", Basic realm="SCIM"'
