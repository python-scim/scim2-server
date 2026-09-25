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
