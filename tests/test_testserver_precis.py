import httpx2
import pytest
from scim2_models import Path
from scim2_models import ScimProvider
from scim2_models import User

from scim2_server.applications.wsgi import WSGIApplication
from scim2_server.memory import InMemoryStorage
from scim2_server.testserver.precis import PRECIS_POLICY
from scim2_server.testserver.precis import precis_comparison_key


@pytest.fixture
def client(scim_provider):
    provider = ScimProvider(
        models=scim_provider.models,
        resource_types=scim_provider.resource_types,
        config=scim_provider.config,
        policy=PRECIS_POLICY,
    )
    transport = httpx2.WSGITransport(app=WSGIApplication(InMemoryStorage(), provider))
    with httpx2.Client(
        transport=transport, base_url="https://scim.example.com"
    ) as client:
        yield client


def test_a_user_name_precis_refuses_is_refused(client):
    """A userName holding a space is refused with invalidValue."""
    r = client.post("/v2/Users", json={"userName": "Barbara Jensen"})

    assert r.status_code == 400
    assert r.json()["scimType"] == "invalidValue"


def test_a_fullwidth_user_name_matches_its_ascii_form(client):
    """PRECIS maps fullwidth letters to ASCII, in filters and in uniqueness checks."""
    client.post("/v2/Users", json={"userName": "bjensen"})

    r = client.get("/v2/Users", params={"filter": 'userName eq "ＢＪＥＮＳＥＮ"'})
    assert [user["userName"] for user in r.json()["Resources"]] == ["bjensen"]
    assert (
        client.post("/v2/Users", json={"userName": "ＢＪＥＮＳＥＮ"}).status_code == 409
    )


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        ("userName", "ＢＪｅｎｓｅｎ", "bjensen"),
        ("password", "Ｓｅｃｒｅｔ", "Ｓｅｃｒｅｔ"),
        ("title", "ＢＪｅｎｓｅｎ", "ｂｊｅｎｓｅｎ"),
    ],
    ids=["username", "password", "other"],
)
def test_each_attribute_gets_its_profile(path, value, expected):
    """Usernames are mapped to lowercase ASCII, passwords keep their case, other strings are compared as by default."""
    binding = Path[User](path).resolve()

    assert precis_comparison_key(binding, value) == expected
