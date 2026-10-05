import httpx2
import pytest

from scim2_server.memory import InMemoryStorage
from scim2_server.testserver.application import BEARER_TOKEN_SCHEME
from scim2_server.testserver.application import BearerTokenApplication
from scim2_server.utils import load_default_provider


@pytest.fixture
def make_client():
    clients = []

    def build(bearer_tokens=()):
        provider = load_default_provider()
        provider.config.authentication_schemes = [BEARER_TOKEN_SCHEME]
        app = BearerTokenApplication(
            InMemoryStorage(), provider, bearer_tokens=bearer_tokens
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
    """Without bearer token, a request without credentials is served."""
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
