import pytest
from scim2_models import ETag

from scim2_server.utils import load_default_service_provider_config


@pytest.fixture(params=[None, ETag(supported=False)], ids=["undeclared", "unsupported"])
def unversioned(request, wsgi_with):
    """Build a client of a service that does not support ETags."""
    config = load_default_service_provider_config()
    config.etag = request.param
    return wsgi_with(config)


@pytest.fixture
def unversioned_user(unversioned, fake_user_data):
    return unversioned.post("/v2/Users", json=fake_user_data[0]).json()["id"]


class TestSCIMApplicationETags:
    def test_resource_get_etag_match(self, wsgi, first_fake_user):
        r = wsgi.get(
            f"/v2/Users/{first_fake_user}",
        )
        assert r.status_code == 200
        j = r.json()
        initial_version = j["meta"]["version"]
        assert r.headers["etag"] == initial_version

        r = wsgi.get(
            f"/v2/Users/{first_fake_user}", headers={"If-None-Match": initial_version}
        )
        assert r.status_code == 304

        r = wsgi.get(
            f"/v2/Users/{first_fake_user}", headers={"If-None-Match": 'W/"abc", *'}
        )
        assert r.status_code == 304

        r = wsgi.get(
            f"/v2/Users/{first_fake_user}", headers={"If-None-Match": 'W/"abc"'}
        )
        assert r.status_code == 200

        r = wsgi.get(f"/v2/Users/{first_fake_user}", headers={"If-Match": 'W/"abc"'})
        assert r.status_code == 412

        r = wsgi.get(f"/v2/Users/{first_fake_user}", headers={"If-Match": "*"})
        assert r.status_code == 200

        r = wsgi.get(
            f"/v2/Users/{first_fake_user}",
            headers={
                "If-Match": "*",
                "If-None-Match": "*",
            },
        )
        assert r.status_code == 304

        r = wsgi.get(
            f"/v2/Users/{first_fake_user}",
            headers={
                "If-Match": initial_version,
                "If-None-Match": initial_version,
            },
        )
        assert r.status_code == 304

    def test_resource_put_etag_match(self, wsgi, first_fake_user):
        r = wsgi.get(
            f"/v2/Users/{first_fake_user}",
        )
        assert r.status_code == 200
        j = r.json()
        initial_version = j["meta"]["version"]
        assert r.headers["etag"] == initial_version

        r = wsgi.put(
            f"/v2/Users/{first_fake_user}",
            json={
                "userName": "Foo",
            },
            headers={
                "If-Match": initial_version,
            },
        )
        assert r.status_code == 200
        j = r.json()
        assert j["userName"] == "Foo"
        new_version = r.headers["etag"]
        assert j["meta"]["version"] == new_version
        assert new_version != initial_version

        r = wsgi.get(
            f"/v2/Users/{first_fake_user}",
        )
        assert r.status_code == 200
        j = r.json()
        assert new_version == j["meta"]["version"]

        r = wsgi.put(
            f"/v2/Users/{first_fake_user}",
            json={
                "userName": "Bar",
            },
            headers={
                "If-Match": initial_version,
            },
        )
        assert r.status_code == 412

        r = wsgi.get(
            f"/v2/Users/{first_fake_user}",
        )
        assert r.json()["userName"] == "Foo"

    def test_resource_patch_etag_match(self, wsgi, first_fake_user):
        r = wsgi.get(
            f"/v2/Users/{first_fake_user}",
        )
        assert r.status_code == 200
        j = r.json()
        initial_version = j["meta"]["version"]
        assert r.headers["etag"] == initial_version

        r = wsgi.patch(
            f"/v2/Users/{first_fake_user}",
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "add", "path": "userName", "value": "Foo"}],
            },
            headers={
                "If-Match": initial_version,
            },
        )
        assert r.status_code == 204
        new_version = r.headers["etag"]
        assert new_version != initial_version

        r = wsgi.get(
            f"/v2/Users/{first_fake_user}",
        )
        assert r.status_code == 200
        j = r.json()
        assert new_version == j["meta"]["version"]

        r = wsgi.patch(
            f"/v2/Users/{first_fake_user}",
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "add", "path": "userName", "value": "Bar"}],
            },
            headers={
                "If-Match": initial_version,
            },
        )
        assert r.status_code == 412

        r = wsgi.get(
            f"/v2/Users/{first_fake_user}",
        )
        assert r.json()["userName"] == "Foo"

    def test_resource_get_not_modified_carries_the_etag(self, wsgi, first_fake_user):
        """RFC 7232 §4.1: a 304 carries the ETag a 200 would have carried."""
        version = wsgi.get(f"/v2/Users/{first_fake_user}").headers["etag"]
        r = wsgi.get(f"/v2/Users/{first_fake_user}", headers={"If-None-Match": version})
        assert r.status_code == 304
        assert r.headers["etag"] == version

    def test_resource_get_failed_if_match_wins_over_if_none_match(
        self, wsgi, first_fake_user
    ):
        """RFC 7232 §6: If-Match is evaluated first, and its failure answers 412."""
        version = wsgi.get(f"/v2/Users/{first_fake_user}").headers["etag"]
        r = wsgi.get(
            f"/v2/Users/{first_fake_user}",
            headers={"If-Match": 'W/"abc"', "If-None-Match": version},
        )
        assert r.status_code == 412

    def test_resource_delete_etag_match(self, wsgi, first_fake_user):
        """A DELETE only removes the version the client names."""
        version = wsgi.get(f"/v2/Users/{first_fake_user}").headers["etag"]

        r = wsgi.delete(f"/v2/Users/{first_fake_user}", headers={"If-Match": 'W/"abc"'})
        assert r.status_code == 412
        r = wsgi.delete(f"/v2/Users/{first_fake_user}", headers={"If-None-Match": "*"})
        assert r.status_code == 412
        assert wsgi.get(f"/v2/Users/{first_fake_user}").status_code == 200

        r = wsgi.delete(f"/v2/Users/{first_fake_user}", headers={"If-Match": version})
        assert r.status_code == 204
        assert wsgi.get(f"/v2/Users/{first_fake_user}").status_code == 404

    def test_resource_delete_unknown(self, wsgi):
        """Deleting a resource that does not exist answers 404."""
        assert wsgi.delete("/v2/Users/unknown").status_code == 404

    def test_resource_put_if_none_match(self, wsgi, first_fake_user):
        """RFC 7232 §3.2: a failed If-None-Match on a PUT answers 412, not 304."""
        version = wsgi.get(f"/v2/Users/{first_fake_user}").headers["etag"]
        r = wsgi.put(
            f"/v2/Users/{first_fake_user}",
            json={"userName": "Foo"},
            headers={"If-None-Match": version},
        )
        assert r.status_code == 412


class TestSCIMApplicationWithoutETags:
    def test_responses_carry_no_version(self, unversioned, fake_user_data):
        """RFC 7644 §3.14: a service without ETags sends neither the header nor meta.version."""
        r = unversioned.post("/v2/Users", json=fake_user_data[0])
        assert "etag" not in r.headers
        assert "version" not in r.json()["meta"]
        user_id = r.json()["id"]

        r = unversioned.get(f"/v2/Users/{user_id}")
        assert "etag" not in r.headers
        assert "version" not in r.json()["meta"]

        r = unversioned.get("/v2/Users")
        assert "version" not in r.json()["Resources"][0]["meta"]

        r = unversioned.put(f"/v2/Users/{user_id}", json={"userName": "Foo"})
        assert "etag" not in r.headers
        assert "version" not in r.json()["meta"]

        r = unversioned.patch(
            f"/v2/Users/{user_id}",
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "replace", "path": "userName", "value": "Bar"}],
            },
        )
        assert r.status_code == 204
        assert "etag" not in r.headers

    @pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
    def test_if_match_listing_tags_fails(self, unversioned, unversioned_user, method):
        """RFC 7232 §3.1: no listed tag matches a resource that has none."""
        r = unversioned.request(
            method,
            f"/v2/Users/{unversioned_user}",
            json={"userName": "Foo"} if method == "PUT" else None,
            headers={"If-Match": 'W/"abc"'},
        )
        assert r.status_code == 412

    def test_if_match_any_passes(self, unversioned, unversioned_user):
        """RFC 7232 §3.1: "*" only requires the resource to exist."""
        r = unversioned.get(f"/v2/Users/{unversioned_user}", headers={"If-Match": "*"})
        assert r.status_code == 200

    def test_if_none_match(self, unversioned, unversioned_user):
        """A listed tag never matches, "*" does, and its 304 carries no ETag."""
        url = f"/v2/Users/{unversioned_user}"
        assert (
            unversioned.get(url, headers={"If-None-Match": 'W/"abc"'}).status_code
            == 200
        )

        r = unversioned.get(url, headers={"If-None-Match": "*"})
        assert r.status_code == 304
        assert "etag" not in r.headers
