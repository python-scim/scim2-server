import pytest
from scim2_models import Bulk
from scim2_models import ETag
from scim2_models import Patch

from scim2_server.utils import load_default_service_provider_config

BULK_REQUEST = "urn:ietf:params:scim:api:messages:2.0:BulkRequest"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def bulk(client, operations, **attributes):
    return client.post(
        "/v2/Bulk",
        json={"schemas": [BULK_REQUEST], "Operations": operations, **attributes},
    )


def create_user(user_name, bulk_id="u"):
    return {
        "method": "POST",
        "path": "/Users",
        "bulkId": bulk_id,
        "data": {"userName": user_name},
    }


def configured(wsgi_with, **settings):
    config = load_default_service_provider_config()
    for name, value in settings.items():
        setattr(config, name, value)
    return wsgi_with(config)


class TestBulkOperations:
    def test_post(self, wsgi):
        """RFC 7644 §3.7: a creation answers 201 with the location, the version and the bulkId."""
        r = bulk(wsgi, [create_user("alice", "qwerty")])
        assert r.status_code == 200
        assert r.json()["schemas"] == [
            "urn:ietf:params:scim:api:messages:2.0:BulkResponse"
        ]
        (result,) = r.json()["Operations"]
        assert result["method"] == "POST"
        assert result["bulkId"] == "qwerty"
        assert result["status"] == "201"
        assert "response" not in result

        user = wsgi.get(result["location"]).json()
        assert user["userName"] == "alice"
        assert result["version"] == user["meta"]["version"]

    def test_put(self, wsgi, first_fake_user):
        """A replacement answers 200 and replaces the resource."""
        r = bulk(
            wsgi,
            [
                {
                    "method": "PUT",
                    "path": f"/Users/{first_fake_user}",
                    "data": {"userName": "bob"},
                }
            ],
        )
        (result,) = r.json()["Operations"]
        assert result["status"] == "200"
        assert (
            result["location"] == f"https://scim.example.com/v2/Users/{first_fake_user}"
        )
        user = wsgi.get(f"/v2/Users/{first_fake_user}").json()
        assert user["userName"] == "bob"
        assert result["version"] == user["meta"]["version"]

    def test_patch(self, wsgi, first_fake_user):
        """A PATCH answers 200 and edits the resource."""
        r = bulk(
            wsgi,
            [
                {
                    "method": "PATCH",
                    "path": f"/Users/{first_fake_user}",
                    "data": {
                        "schemas": [PATCH_OP],
                        "Operations": [
                            {"op": "replace", "path": "userName", "value": "carol"}
                        ],
                    },
                }
            ],
        )
        (result,) = r.json()["Operations"]
        assert result["status"] == "200"
        user = wsgi.get(f"/v2/Users/{first_fake_user}").json()
        assert user["userName"] == "carol"
        assert result["version"] == user["meta"]["version"]

    def test_delete(self, wsgi, first_fake_user):
        """A deletion answers 204 with the location of the deleted resource."""
        r = bulk(wsgi, [{"method": "DELETE", "path": f"/Users/{first_fake_user}"}])
        (result,) = r.json()["Operations"]
        assert result == {
            "method": "DELETE",
            "location": f"https://scim.example.com/v2/Users/{first_fake_user}",
            "status": "204",
        }
        assert wsgi.get(f"/v2/Users/{first_fake_user}").status_code == 404

    def test_operations_run_in_order(self, wsgi):
        """Each operation sees the changes of the previous ones."""
        r = bulk(wsgi, [create_user("dave"), create_user("dave", "other")])
        first, second = r.json()["Operations"]
        assert first["status"] == "201"
        assert second["status"] == "409"
        assert second["response"]["scimType"] == "uniqueness"
        assert "location" not in second

    def test_paths_are_case_insensitive_and_may_omit_the_leading_slash(self, wsgi):
        """The endpoint of a path is compared as the HTTP endpoints are."""
        operation = {**create_user("erin"), "path": "users"}
        (result,) = bulk(wsgi, [operation]).json()["Operations"]
        assert result["status"] == "201"


class TestBulkOperationErrors:
    def test_invalid_data_only_fails_its_operation(self, wsgi):
        """RFC 7644 §3.7.3: an invalid operation answers 400 inside a job that answers 200."""
        r = bulk(
            wsgi,
            [
                {"method": "POST", "path": "/Users", "bulkId": "a", "data": {}},
                create_user("frank"),
            ],
        )
        assert r.status_code == 200
        first, second = r.json()["Operations"]
        assert first["method"] == "POST"
        assert first["bulkId"] == "a"
        assert first["status"] == "400"
        assert first["response"]["status"] == "400"
        assert "location" not in first
        assert second["status"] == "201"

    def test_unknown_resource(self, wsgi):
        """An operation on a missing resource answers 404 with its location."""
        (result,) = bulk(wsgi, [{"method": "DELETE", "path": "/Users/unknown"}]).json()[
            "Operations"
        ]
        assert result["status"] == "404"
        assert result["location"] == "https://scim.example.com/v2/Users/unknown"

    def test_unknown_endpoint(self, wsgi):
        """An operation on an endpoint that serves no resource type answers 404."""
        (result,) = bulk(wsgi, [{"method": "DELETE", "path": "/Unknown/x"}]).json()[
            "Operations"
        ]
        assert result["status"] == "404"

    @pytest.mark.parametrize(
        "operation",
        [
            {"method": "POST", "path": "/Users/x", "bulkId": "a", "data": {}},
            {"method": "DELETE", "path": "/Users"},
        ],
        ids=["post-to-a-resource", "delete-an-endpoint"],
    )
    def test_path_does_not_fit_the_method(self, wsgi, operation):
        """RFC 7644 §3.7: a POST targets a resource type endpoint, other methods a resource."""
        (result,) = bulk(wsgi, [operation]).json()["Operations"]
        assert result["status"] == "400"
        assert result["response"]["scimType"] == "invalidValue"

    @pytest.mark.parametrize(
        "operation",
        [{"method": "DELETE"}, {"method": "DELETE", "path": 1}, "DELETE"],
        ids=["missing", "not-a-string", "operation-not-an-object"],
    )
    def test_path_is_required(self, wsgi, operation):
        """RFC 7644 §3.7: the path of an operation is required."""
        (result,) = bulk(wsgi, [operation]).json()["Operations"]
        assert result["status"] == "400"
        assert result["response"]["detail"] == "path is required for request operations"

    @pytest.mark.parametrize("method", ["GET", "post", ["POST"]])
    def test_invalid_method(self, wsgi, method):
        """An operation with an invalid method answers 400, and does not repeat the method."""
        (result,) = bulk(wsgi, [{"method": method, "path": "/Users/x"}]).json()[
            "Operations"
        ]
        assert result["status"] == "400"
        assert "method" not in result

    def test_version_mismatch(self, wsgi, first_fake_user):
        """RFC 7644 §3.7: an operation carrying another version answers 412."""
        r = bulk(
            wsgi,
            [
                {
                    "method": "DELETE",
                    "path": f"/Users/{first_fake_user}",
                    "version": 'W/"abc"',
                }
            ],
        )
        (result,) = r.json()["Operations"]
        assert result["status"] == "412"
        assert wsgi.get(f"/v2/Users/{first_fake_user}").status_code == 200

    def test_version_match(self, wsgi, first_fake_user):
        """An operation carrying the current version is applied."""
        version = wsgi.get(f"/v2/Users/{first_fake_user}").headers["etag"]
        r = bulk(
            wsgi,
            [
                {
                    "method": "DELETE",
                    "path": f"/Users/{first_fake_user}",
                    "version": version,
                }
            ],
        )
        (result,) = r.json()["Operations"]
        assert result["status"] == "204"

    def test_patch_not_supported(self, wsgi_with, first_fake_user):
        """A PATCH operation answers 501 when the service does not support PATCH."""
        client = configured(wsgi_with, patch=Patch(supported=False))
        r = bulk(
            client,
            [
                {
                    "method": "PATCH",
                    "path": f"/Users/{first_fake_user}",
                    "data": {
                        "schemas": [PATCH_OP],
                        "Operations": [
                            {"op": "replace", "path": "userName", "value": "x"}
                        ],
                    },
                }
            ],
        )
        (result,) = r.json()["Operations"]
        assert result["status"] == "501"


class TestBulkFailOnErrors:
    def test_without_fail_on_errors_every_operation_runs(self, wsgi):
        """RFC 7644 §3.7.3: the job performs as many changes as possible."""
        operations = [
            {"method": "DELETE", "path": "/Users/a"},
            {"method": "DELETE", "path": "/Users/b"},
            create_user("grace"),
        ]
        results = bulk(wsgi, operations).json()["Operations"]
        assert [result["status"] for result in results] == ["404", "404", "201"]

    @pytest.mark.parametrize("fail_on_errors", [0, 1])
    def test_fail_on_errors_stops_the_job(self, wsgi, fail_on_errors):
        """RFC 7644 §3.7.3: the job stops at the first error, and later operations are left out."""
        operations = [
            {"method": "DELETE", "path": "/Users/a"},
            create_user("heidi"),
        ]
        r = bulk(wsgi, operations, failOnErrors=fail_on_errors)
        assert r.status_code == 200
        assert [result["status"] for result in r.json()["Operations"]] == ["404"]
        assert wsgi.get("/v2/Users").json()["totalResults"] == 0

    def test_fail_on_errors_accepts_errors_below_the_limit(self, wsgi):
        """The job goes on until the number of errors reaches failOnErrors."""
        operations = [
            {"method": "DELETE", "path": "/Users/a"},
            create_user("ivan"),
            {"method": "DELETE", "path": "/Users/b"},
            create_user("judy", "j"),
        ]
        results = bulk(wsgi, operations, failOnErrors=2).json()["Operations"]
        assert [result["status"] for result in results] == ["404", "201", "404"]


class TestBulkRequest:
    def test_not_supported(self, wsgi_with):
        """RFC 7644 §3.7: bulk is optional, and a service that does not support it answers 501."""
        client = configured(wsgi_with, bulk=Bulk(supported=False))
        r = bulk(client, [])
        assert r.status_code == 501
        assert r.json()["detail"] == "Bulk is not supported"

    def test_empty(self, wsgi):
        """A job without operations answers an empty list of results."""
        r = bulk(wsgi, [])
        assert r.status_code == 200
        assert r.json()["Operations"] == []

    def test_operations_key_is_case_insensitive(self, wsgi):
        """The attribute names of the envelope are case insensitive."""
        r = wsgi.post(
            "/v2/Bulk",
            json={"schemas": [BULK_REQUEST], "operations": [create_user("kim")]},
        )
        assert [result["status"] for result in r.json()["Operations"]] == ["201"]

    @pytest.mark.parametrize(
        "payload",
        [
            {"schemas": [BULK_REQUEST]},
            {"schemas": [BULK_REQUEST], "Operations": "x"},
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [],
            },
            {"schemas": [BULK_REQUEST], "failOnErrors": "x", "Operations": []},
            [],
        ],
        ids=[
            "missing-operations",
            "operations-not-a-list",
            "wrong-schema",
            "invalid-fail-on-errors",
            "not-an-object",
        ],
    )
    def test_invalid_envelope(self, wsgi, payload):
        """An invalid envelope fails the whole request with a 400."""
        r = wsgi.post("/v2/Bulk", json=payload)
        assert r.status_code == 400

    def test_too_many_operations(self, wsgi_with):
        """RFC 7644 §3.7.4: a job beyond maxOperations answers 413 and names the limit."""
        client = configured(
            wsgi_with,
            bulk=Bulk(supported=True, max_operations=1, max_payload_size=1048576),
        )
        r = bulk(client, [create_user("leo"), create_user("mia", "m")])
        assert r.status_code == 413
        assert (
            r.json()["detail"]
            == "The number of operations exceeds the maxOperations (1)"
        )
        assert client.get("/v2/Users").json()["totalResults"] == 0

    def test_payload_too_large(self, wsgi_with):
        """RFC 7644 §3.7.4: a payload beyond maxPayloadSize answers 413 and names the limit."""
        client = configured(
            wsgi_with,
            bulk=Bulk(supported=True, max_operations=1000, max_payload_size=10),
        )
        r = bulk(client, [create_user("nina")])
        assert r.status_code == 413
        assert r.json()["detail"] == "The payload exceeds the maxPayloadSize (10 bytes)"

    def test_limits_are_optional(self, wsgi_with):
        """A configuration without limits accepts any job."""
        client = configured(wsgi_with, bulk=Bulk(supported=True))
        r = bulk(client, [create_user("olga")])
        assert [result["status"] for result in r.json()["Operations"]] == ["201"]

    def test_without_etags_results_carry_no_version(self, wsgi_with):
        """RFC 7644 §3.14: a service without ETags sends no version."""
        client = configured(wsgi_with, etag=ETag(supported=False))
        (result,) = bulk(client, [create_user("paul")]).json()["Operations"]
        assert result["status"] == "201"
        assert "version" not in result
