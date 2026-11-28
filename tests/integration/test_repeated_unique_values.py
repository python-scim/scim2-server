import httpx2
import pytest
from scim2_models import Meta
from scim2_models import ScimProvider

from scim2_server.applications.wsgi import WSGIApplication
from scim2_server.handler import AsyncScimHandler
from scim2_server.memory import AsyncInMemoryStorage
from scim2_server.testing import MAILBOX_TYPE
from scim2_server.testing import Mailbox
from scim2_server.utils import load_default_service_provider_config

from ..conftest import BlockingHandler

MAILBOX_SCHEMA = str(Mailbox.__schema__)
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
BULK_REQUEST = "urn:ietf:params:scim:api:messages:2.0:BulkRequest"


@pytest.fixture(params=["sync", "async"])
def client(request, storage):
    """Build a client of a service serving mailboxes, through the synchronous handler, then the asynchronous one."""
    provider = ScimProvider(
        models=[Mailbox],
        resource_types=[MAILBOX_TYPE],
        config=load_default_service_provider_config(),
    )
    app = WSGIApplication(storage, provider)
    if request.param == "async":
        app.handler = BlockingHandler(
            AsyncScimHandler(app.service, AsyncInMemoryStorage(storage))
        )
    transport = httpx2.WSGITransport(app=app)
    with httpx2.Client(
        transport=transport, base_url="https://scim.example.com"
    ) as client:
        yield client


def mailbox(**attributes):
    return {"schemas": [MAILBOX_SCHEMA], **attributes}


def addresses(*values):
    return [{"value": value} for value in values]


def assert_invalid_value(response):
    assert response.status_code == 400
    assert response.json()["scimType"] == "invalidValue"


def test_a_creation_holding_a_unique_value_twice_is_refused(client):
    """Two addresses equal but for their case make a 400, and no mailbox is created."""
    body = mailbox(addresses=addresses("babs@example.com", "BABS@example.com"))
    r = client.post("/v2/Mailboxes", json=body)

    assert_invalid_value(r)
    assert r.json()["detail"] == "The attribute addresses.value holds a value twice"
    assert client.get("/v2/Mailboxes").json()["totalResults"] == 0


def test_case_exact_unique_values_differing_by_their_case_are_accepted(client):
    """Two aliases differing by their case are two values."""
    r = client.post("/v2/Mailboxes", json=mailbox(aliases=["babs", "Babs"]))

    assert r.status_code == 201
    assert r.json()["aliases"] == ["babs", "Babs"]


def test_a_replacement_holding_a_unique_value_twice_is_refused(client):
    """A PUT repeating an address makes a 400, and the mailbox keeps its addresses."""
    body = mailbox(addresses=addresses("babs@example.com"))
    location = client.post("/v2/Mailboxes", json=body).headers["Location"]
    body["addresses"] = addresses("babs@example.com", "babs@example.com")

    assert_invalid_value(client.put(location, json=body))
    assert client.get(location).json()["addresses"] == addresses("babs@example.com")


def test_a_patch_repeating_a_unique_value_is_refused(client):
    """A PATCH replacing the addresses by two equal ones makes a 400."""
    body = mailbox(addresses=addresses("babs@example.com"))
    location = client.post("/v2/Mailboxes", json=body).headers["Location"]
    patch = {
        "schemas": [PATCH_OP],
        "Operations": [
            {
                "op": "replace",
                "path": "addresses",
                "value": addresses("bjensen@example.com", "BJensen@example.com"),
            }
        ],
    }

    assert_invalid_value(client.patch(location, json=patch))
    assert client.get(location).json()["addresses"] == addresses("babs@example.com")


def test_a_bulk_creation_holding_a_unique_value_twice_fails_alone(client):
    """The bulk operation repeating a value fails with a 400, and the next one runs."""
    operations = [
        {
            "method": "POST",
            "path": "/Mailboxes",
            "bulkId": bulk_id,
            "data": mailbox(aliases=aliases),
        }
        for bulk_id, aliases in (("twice", ["babs", "babs"]), ("once", ["babs"]))
    ]
    r = client.post(
        "/v2/Bulk", json={"schemas": [BULK_REQUEST], "Operations": operations}
    )

    statuses = [operation["status"] for operation in r.json()["Operations"]]
    assert statuses == ["400", "201"]


@pytest.fixture
def repeating(storage):
    """Store a mailbox holding an alias twice, as a storage of scim2-server 0.10 could."""
    storage.resources = [
        Mailbox(
            id="1",
            aliases=["babs", "babs"],
            meta=Meta(resource_type="Mailbox", version='W/"1"'),
        )
    ]
    return "/v2/Mailboxes/1"


def test_a_value_the_resource_already_repeats_is_kept(client, repeating):
    """A PUT or a PATCH may keep an alias the mailbox already holds twice."""
    body = mailbox(display_name="Babs", aliases=["babs", "babs"])
    patch = {
        "schemas": [PATCH_OP],
        "Operations": [{"op": "replace", "path": "displayName", "value": "Barbara"}],
    }

    assert client.put(repeating, json=body).status_code == 200
    assert client.patch(repeating, json=patch).status_code == 204
    assert client.get(repeating).json()["aliases"] == ["babs", "babs"]


def test_a_new_repeated_value_is_refused_beside_one_already_repeated(client, repeating):
    """A PUT repeating an address makes a 400, even when the mailbox already repeats an alias."""
    body = mailbox(
        aliases=["babs", "babs"],
        addresses=addresses("babs@example.com", "babs@example.com"),
    )

    assert_invalid_value(client.put(repeating, json=body))


def test_the_first_repeating_attribute_is_told(client):
    """When several attributes repeat a value, the detail tells the first one the schema declares."""
    body = mailbox(aliases=["babs", "babs"], addresses=addresses("x", "x"))

    r = client.post("/v2/Mailboxes", json=body)

    assert r.json()["detail"] == "The attribute addresses.value holds a value twice"
