from typing import Union

from scim2_models import ListResponse
from scim2_models import PatchOp
from scim2_models import PatchOperation
from scim2_models import User


class TestSCIMProviderBasic:
    def test_user_creation(self, wsgi):
        payload = {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "userName": "bjensen@example.com",
            "name": {
                "givenName": "Barbara",
                "familyName": "Jensen",
            },
            "emails": [
                {"primary": True, "value": "bjensen@example.com", "type": "work"}
            ],
            "displayName": "Barbara Jensen",
            "active": True,
        }
        r = wsgi.post("/v2/Users", json=payload)
        assert r.status_code == 201
        j = r.json()
        assert r.headers["Location"] == f"https://scim.example.com/v2/Users/{j['id']}"
        assert r.headers["Content-Type"] == "application/scim+json"
        assert j["meta"]["location"] == f"https://scim.example.com/v2/Users/{j['id']}"
        j.pop("meta")
        j.pop("id")
        assert j == payload

    def test_unique_constraints(self, wsgi):
        payload = {
            "userName": "bjensen@example.com",
        }
        r = wsgi.post("/v2/Users", json=payload)
        assert r.status_code == 201

        r = wsgi.post("/v2/Users", json=payload)
        assert r.status_code == 409

        r = wsgi.post("/v2/Users", json={"userName": "BJENSEN@EXAMPLE.COM"})
        assert r.status_code == 409

        r = wsgi.post(
            "/v2/Users",
            json={
                "userName": "bjensen2@example.com",
            },
        )
        assert r.status_code == 201
        user_id = r.json()["id"]
        r = wsgi.put(
            f"/v2/Users/{user_id}",
            json={
                "userName": "bjensen@example.com",
            },
        )
        assert r.status_code == 409

        r = wsgi.get(f"/v2/Users/{user_id}")
        assert r.status_code == 200
        assert r.json()["userName"] == "bjensen2@example.com"

        r = wsgi.patch(
            f"/v2/Users/{user_id}",
            json=PatchOp[User](
                operations=[
                    PatchOperation(
                        op=PatchOperation.Op.replace_,
                        path="userName",
                        value="bjensen@example.com",
                    )
                ]
            ).model_dump(),
        )
        assert r.status_code == 409, r.text

        r = wsgi.get(f"/v2/Users/{user_id}")
        assert r.status_code == 200
        assert r.json()["userName"] == "bjensen2@example.com"

    def test_sort(self, provider, wsgi):
        TypedListResponse = ListResponse[Union[tuple(provider.backend.get_models())]]  # noqa: UP007

        def assert_sorted(sort_by: str, sorted: list[str], endpoint: str = "/v2/Users"):
            for order_by, inverted in (
                (None, False),
                ("ascending", False),
                ("descending", True),
            ):
                params = {
                    "sortBy": sort_by,
                }
                if order_by is not None:
                    params["sortOrder"] = order_by
                result = wsgi.get(endpoint, params=params)
                assert result.status_code == 200
                response = TypedListResponse.model_validate(result.json())
                assert response.total_results == len(sorted)
                sorted_ids = [r.id for r in response.resources]
                if inverted:
                    sorted_ids.reverse()
                assert sorted_ids == sorted

        u1_id = wsgi.post(
            "/v2/Users",
            json={
                "userName": "ajensen@example.com",
                "name": {
                    "formatted": "A",
                    "givenName": "A",
                },
                "active": True,
                "displayName": "user display name",
                "emails": [
                    {
                        "value": "a@example.com",
                    },
                    {
                        "value": "c@example.com",
                    },
                    {
                        "value": "D@example.com",
                        "primary": True,
                    },
                ],
            },
        ).json()["id"]

        u2_id = wsgi.post(
            "/v2/Users",
            json={
                "userName": "bjensen@example.com",
                "name": {
                    "givenName": "B",
                },
                "emails": [
                    {
                        "value": "b@example.com",
                    }
                ],
            },
        ).json()["id"]

        group_id = wsgi.post(
            "/v2/Groups",
            json={"displayName": "group display name"},
        ).json()["id"]

        assert_sorted("userName", [u1_id, u2_id])
        assert_sorted("name.givenName", [u1_id, u2_id])
        assert_sorted("name.formatted", [u1_id, u2_id])

        assert_sorted("emails", [u2_id, u1_id])
        assert_sorted("emails.value", [u2_id, u1_id])
        assert_sorted('emails[value ew "example.com"]', [u2_id, u1_id])
        assert_sorted('emails[value ew "example.com"].value', [u2_id, u1_id])
        assert_sorted(
            "active",
            [u1_id, u2_id],
        )
        assert_sorted("displayName", [group_id, u1_id, u2_id], "/v2/")
