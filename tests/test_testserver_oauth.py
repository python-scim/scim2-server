import json
import time
import urllib.error
import urllib.request
from email.message import Message

import httpx2
import pytest
from joserfc import jwt
from joserfc.jwk import ECKey
from joserfc.jwk import KeySet

from scim2_server.memory import InMemoryStorage
from scim2_server.testserver.application import BASIC_SCHEME
from scim2_server.testserver.application import OAUTH2_SCHEME
from scim2_server.testserver.application import CredentialsApplication
from scim2_server.testserver.oauth import AccessTokenValidator
from scim2_server.testserver.oauth import fetch_json
from scim2_server.testserver.oauth import metadata_urls
from scim2_server.utils import load_default_provider

ISSUER = "https://auth.example.com/realms/apps"
AUDIENCE = "https://scim.example.com"
JWKS_URI = "https://auth.example.com/realms/apps/certs"
RFC8414_URL = (
    "https://auth.example.com/.well-known/oauth-authorization-server/realms/apps"
)
OPENID_URL = "https://auth.example.com/realms/apps/.well-known/openid-configuration"


def make_key(kid):
    return ECKey.generate_key("P-256", parameters={"kid": kid})


def make_token(key, typ="at+jwt", kid=True, **claims):
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "service-account",
        "client_id": "keycloak",
        "iat": now,
        "exp": now + 300,
        "jti": "token-id",
        **claims,
    }
    payload = {name: value for name, value in payload.items() if value is not None}
    header = {"alg": "ES256", "typ": typ, **({"kid": key.kid} if kid else {})}
    return jwt.encode(header, payload, key)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


class AuthorizationServer:
    """Serve the metadata and the public keys of an issuer, and count the key downloads.

    When ``failure`` or ``document`` is set, a key download raises it or returns it.
    """

    def __init__(self, *keys):
        self.keys = list(keys)
        self.downloads = 0
        self.failure = None
        self.document = None
        self.metadata = {RFC8414_URL: {"issuer": ISSUER, "jwks_uri": JWKS_URI}}
        self.requested = []

    def fetch(self, url):
        self.requested.append(url)
        if url in self.metadata:
            return self.metadata[url]

        if url != JWKS_URI:
            error = urllib.error.HTTPError(url, 404, "Not Found", Message(), None)
            error.close()
            raise error

        self.downloads += 1
        if self.failure:
            raise self.failure

        return self.document or KeySet(self.keys).as_dict(private=False)


@pytest.fixture
def key():
    return make_key("first")


@pytest.fixture
def server(key):
    return AuthorizationServer(key)


@pytest.fixture
def validator(server):
    return AccessTokenValidator(ISSUER, AUDIENCE, JWKS_URI, fetch=server.fetch)


@pytest.fixture
def make_client(validator):
    clients = []

    def build(bearer_tokens=(), access_token_validator=validator):
        provider = load_default_provider()
        provider.config.authentication_schemes = [OAUTH2_SCHEME]
        app = CredentialsApplication(
            InMemoryStorage(),
            provider,
            bearer_tokens=bearer_tokens,
            access_token_validator=access_token_validator,
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


@pytest.mark.parametrize(
    "audience",
    [AUDIENCE, [AUDIENCE, "account"]],
    ids=["single audience", "among other audiences"],
)
def test_a_valid_access_token_is_accepted(make_client, key, audience):
    """A JWT access token signed by the authorization server, for the audience, is accepted."""
    r = make_client().get("/v2/Users", headers=bearer(make_token(key, aud=audience)))

    assert r.status_code == 200


@pytest.mark.parametrize(
    "token",
    [
        lambda key: make_token(key, exp=int(time.time()) - 60),
        lambda key: make_token(key, aud="https://other.example.com"),
        lambda key: make_token(key, iss="https://other.example.com"),
        lambda key: make_token(key, typ="JWT"),
        lambda key: make_token(key, client_id=None),
        lambda key: make_token(make_key("first")),
        lambda key: "not-a-jwt",
        lambda key: "",
    ],
    ids=[
        "expired",
        "other audience",
        "other issuer",
        "not an access token type",
        "without client_id",
        "signed by another key",
        "not a JWT",
        "empty",
    ],
)
def test_an_invalid_access_token_is_refused(make_client, key, token):
    """An invalid access token gets a 401 with the invalid_token error code (RFC 6750 §3.1)."""
    r = make_client().get("/v2/Users", headers=bearer(token(key)))

    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"].startswith(
        'Bearer realm="SCIM", error="invalid_token", error_description='
    )
    assert r.json()["status"] == "401"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic YWxpY2U6czNjcmV0"}],
    ids=["no credentials", "other scheme"],
)
def test_a_request_without_access_token_gets_no_error_code(make_client, headers):
    """Without access token, the 401 challenge has no error code (RFC 6750 §3.1)."""
    r = make_client().get("/v2/Users", headers=headers)

    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == 'Bearer realm="SCIM"'


def test_the_keys_are_downloaded_once(make_client, server, key):
    """The keys are downloaded with the first token, then kept."""
    client = make_client()

    for _ in range(3):
        assert (
            client.get("/v2/Users", headers=bearer(make_token(key))).status_code == 200
        )

    assert server.downloads == 1


@pytest.mark.parametrize(
    "headers",
    [{}, bearer("not-a-jwt")],
    ids=["no token", "not a JWT"],
)
def test_a_request_without_a_jwt_does_not_download_the_keys(
    make_client, server, headers
):
    """A request without token, or with a token that is not a JWT, does not download the keys."""
    make_client().get("/v2/Users", headers=headers)

    assert server.downloads == 0


def test_a_token_signed_with_a_new_key_downloads_the_keys_again(
    make_client, validator, server, key
):
    """After a key rotation, a token signed with the new key downloads the keys again."""
    client = make_client()
    client.get("/v2/Users", headers=bearer(make_token(key)))
    validator.downloaded_at -= validator.refresh_interval
    new_key = make_key("second")
    server.keys = [new_key]

    r = client.get("/v2/Users", headers=bearer(make_token(new_key)))

    assert r.status_code == 200
    assert server.downloads == 2


def test_unknown_keys_do_not_download_the_keys_within_the_interval(
    make_client, server, key
):
    """Tokens signed with unknown keys cannot make the server flood the authorization server."""
    client = make_client()
    client.get("/v2/Users", headers=bearer(make_token(key)))

    for kid in ["forged", "other-forged"]:
        r = client.get("/v2/Users", headers=bearer(make_token(make_key(kid))))
        assert r.status_code == 401

    assert server.downloads == 1


def test_a_token_without_key_id_does_not_download_the_keys_again(
    make_client, validator, server, key
):
    """A token without kid header cannot ask for a key rotation."""
    client = make_client()
    client.get("/v2/Users", headers=bearer(make_token(key)))
    validator.downloaded_at -= validator.refresh_interval

    client.get("/v2/Users", headers=bearer(make_token(make_key("forged"), kid=False)))

    assert server.downloads == 1


@pytest.mark.parametrize(
    "unavailable",
    [
        {"failure": urllib.error.URLError("connection refused")},
        {"document": {"error": "realm not ready"}},
    ],
    ids=["unreachable", "not a JWK set"],
)
def test_the_keys_are_downloaded_once_the_authorization_server_is_up(
    make_client, server, key, unavailable
):
    """Without the keys, the server answers 500, not 401, and downloads them again with the next token."""
    client = make_client()
    for name, value in unavailable.items():
        setattr(server, name, value)

    r = client.get("/v2/Users", headers=bearer(make_token(key)))

    assert r.status_code == 500
    assert "WWW-Authenticate" not in r.headers

    server.failure = server.document = None
    r = client.get("/v2/Users", headers=bearer(make_token(key)))

    assert r.status_code == 200
    assert server.downloads == 2


def test_a_failed_key_rotation_keeps_the_keys(make_client, validator, server, key):
    """When the keys cannot be downloaded again, the tokens signed with the known keys are still accepted."""
    client = make_client()
    client.get("/v2/Users", headers=bearer(make_token(key)))
    validator.downloaded_at -= validator.refresh_interval
    server.failure = urllib.error.URLError("connection refused")

    r = client.get("/v2/Users", headers=bearer(make_token(make_key("second"))))

    assert r.status_code == 500
    assert client.get("/v2/Users", headers=bearer(make_token(key))).status_code == 200


@pytest.mark.parametrize(
    ("scope", "status"),
    [("scim", 200), ("openid scim profile", 200), ("openid", 403), (None, 403)],
    ids=["exact scope", "among other scopes", "other scope", "no scope"],
)
def test_the_access_token_needs_the_scope(make_client, server, key, scope, status):
    """With a required scope, a token without it gets a 403 with the insufficient_scope error code."""
    validator = AccessTokenValidator(
        ISSUER, AUDIENCE, JWKS_URI, scope="scim", fetch=server.fetch
    )
    client = make_client(access_token_validator=validator)

    r = client.get("/v2/Users", headers=bearer(make_token(key, scope=scope)))

    assert r.status_code == status
    if status == 403:
        assert r.headers["WWW-Authenticate"] == (
            'Bearer realm="SCIM", error="insufficient_scope", scope="scim"'
        )
        assert r.json()["status"] == "403"


def test_static_bearer_tokens_and_access_tokens_are_both_accepted(make_client, key):
    """A static bearer token and an access token are both accepted."""
    client = make_client(bearer_tokens=["s3cret"])

    assert client.get("/v2/Users", headers=bearer("s3cret")).status_code == 200
    assert client.get("/v2/Users", headers=bearer(make_token(key))).status_code == 200
    assert client.get("/v2/Users", headers=bearer("wrong")).status_code == 401


def test_an_invalid_access_token_keeps_the_challenges_of_the_other_schemes(validator):
    """With Basic credentials too, the 401 of an invalid access token still announces the Basic scheme."""
    provider = load_default_provider()
    provider.config.authentication_schemes = [OAUTH2_SCHEME, BASIC_SCHEME]
    app = CredentialsApplication(
        InMemoryStorage(),
        provider,
        basic_credentials=[("alice", "s3cret")],
        access_token_validator=validator,
    )

    with httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="https://scim.example.com"
    ) as client:
        r = client.get("/v2/Users", headers=bearer("not-a-jwt"))

    challenge = r.headers["WWW-Authenticate"]
    assert challenge.startswith('Bearer realm="SCIM", error="invalid_token"')
    assert challenge.endswith(', Basic realm="SCIM"')


def test_the_service_provider_configuration_announces_oauth2(make_client):
    """The service provider configuration is served without token, and announces the oauth2 scheme."""
    r = make_client().get("/v2/ServiceProviderConfig")

    assert r.status_code == 200
    assert r.json()["authenticationSchemes"][0]["type"] == "oauth2"


def test_fetch_json_closes_the_http_errors(monkeypatch):
    """An HTTP error holds no connection once raised."""
    error = urllib.error.HTTPError(JWKS_URI, 404, "Not Found", Message(), None)

    def urlopen(url, timeout):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    with pytest.raises(urllib.error.HTTPError):
        fetch_json(JWKS_URI)

    assert error.fp.closed


def test_fetch_json_downloads_a_json_document(tmp_path):
    """The keys are downloaded as JSON."""
    path = tmp_path / "certs.json"
    path.write_text(json.dumps({"keys": []}))

    assert fetch_json(path.as_uri()) == {"keys": []}


@pytest.mark.parametrize(
    ("issuer", "urls"),
    [
        (
            "https://auth.example.com/realms/apps",
            (RFC8414_URL, OPENID_URL),
        ),
        (
            "https://auth.example.com/realms/apps/",
            (RFC8414_URL, OPENID_URL),
        ),
        (
            "https://auth.example.com",
            (
                "https://auth.example.com/.well-known/oauth-authorization-server",
                "https://auth.example.com/.well-known/openid-configuration",
            ),
        ),
    ],
    ids=["with a path", "with a trailing slash", "without path"],
)
def test_metadata_urls(issuer, urls):
    """The RFC 8414 well-known segment goes before the path, the OpenID one after it."""
    assert metadata_urls(issuer) == urls


@pytest.fixture
def discovering_validator(server):
    return AccessTokenValidator(ISSUER, AUDIENCE, fetch=server.fetch)


@pytest.fixture
def discovering_client(make_client, discovering_validator):
    return make_client(access_token_validator=discovering_validator)


def test_the_keys_are_found_in_the_metadata_of_the_issuer(
    discovering_client, server, key
):
    """Without JWK set URL, the keys come from the jwks_uri of the RFC 8414 metadata."""
    r = discovering_client.get("/v2/Users", headers=bearer(make_token(key)))

    assert r.status_code == 200
    assert server.requested == [RFC8414_URL, JWKS_URI]


def test_the_openid_metadata_are_used_without_rfc8414_metadata(
    discovering_client, server, key
):
    """Without RFC 8414 metadata, the keys come from the OpenID Connect Discovery metadata."""
    server.metadata = {OPENID_URL: {"issuer": ISSUER, "jwks_uri": JWKS_URI}}

    r = discovering_client.get("/v2/Users", headers=bearer(make_token(key)))

    assert r.status_code == 200
    assert server.requested == [RFC8414_URL, OPENID_URL, JWKS_URI]


def test_the_metadata_are_downloaded_once(
    discovering_client, discovering_validator, server, key
):
    """A key rotation downloads the keys again, not the metadata."""
    discovering_client.get("/v2/Users", headers=bearer(make_token(key)))
    new_key = make_key("second")
    server.keys = [new_key]
    discovering_validator.downloaded_at -= discovering_validator.refresh_interval

    r = discovering_client.get("/v2/Users", headers=bearer(make_token(new_key)))

    assert r.status_code == 200
    assert server.requested == [RFC8414_URL, JWKS_URI, JWKS_URI]


@pytest.mark.parametrize(
    "metadata",
    [
        {"issuer": "https://other.example.com", "jwks_uri": JWKS_URI},
        {"jwks_uri": JWKS_URI},
        {"issuer": ISSUER},
        None,
    ],
    ids=["other issuer", "without issuer", "without jwks_uri", "no metadata"],
)
def test_unusable_metadata_are_a_server_error(
    discovering_client, server, key, metadata
):
    """Metadata of another issuer, without jwks_uri, or missing, give a 500, and are fetched again with the next token."""
    server.metadata = {RFC8414_URL: metadata} if metadata else {}

    r = discovering_client.get("/v2/Users", headers=bearer(make_token(key)))

    assert r.status_code == 500
    server.metadata = {RFC8414_URL: {"issuer": ISSUER, "jwks_uri": JWKS_URI}}
    r = discovering_client.get("/v2/Users", headers=bearer(make_token(key)))
    assert r.status_code == 200
