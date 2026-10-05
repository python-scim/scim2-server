from typing import Union

import pytest
from scim2_models import ListResponse
from scim2_models import PatchOp
from scim2_models import PatchOperation
from scim2_models import User

PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"


class TestSCIMApplicationBasic:
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
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "bjensen@example.com",
        }
        r = wsgi.post("/v2/Users", json=payload)
        assert r.status_code == 201

        r = wsgi.post("/v2/Users", json=payload)
        assert r.status_code == 409

        r = wsgi.post(
            "/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "BJENSEN@EXAMPLE.COM",
            },
        )
        assert r.status_code == 409

        r = wsgi.post(
            "/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "bjensen2@example.com",
            },
        )
        assert r.status_code == 201
        user_id = r.json()["id"]
        r = wsgi.put(
            f"/v2/Users/{user_id}",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
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

    def test_sort(self, app, wsgi):
        TypedListResponse = ListResponse[Union[tuple(app.service.get_models())]]  # noqa: UP007

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
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
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
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
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
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                "displayName": "group display name",
            },
        ).json()["id"]

        assert_sorted("userName", [u1_id, u2_id])
        assert_sorted("name.givenName", [u1_id, u2_id])
        assert_sorted("name.formatted", [u1_id, u2_id])

        assert_sorted("emails", [u2_id, u1_id])
        assert_sorted("emails.value", [u2_id, u1_id])
        assert_sorted(
            "active",
            [u1_id, u2_id],
        )
        assert_sorted("displayName", [group_id, u1_id, u2_id], "/v2/")

    @pytest.mark.parametrize(
        "sort_by",
        ['emails[value ew "example.com"]', 'emails[value ew "example.com"].value'],
    )
    def test_sort_by_value_filter_is_refused(self, wsgi, sort_by):
        """RFC 7644 §3.4.2.3 requires sortBy in the attribute notation of §3.10."""
        result = wsgi.get("/v2/Users", params={"sortBy": sort_by})
        assert result.status_code == 400
        assert result.json()["scimType"] == "invalidPath"

    def test_sort_on_the_root_puts_undeclared_attributes_last(self, wsgi):
        """RFC 7644 §3.4.2.1 treats an attribute a resource type lacks as having no value."""
        user_id = wsgi.post(
            "/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "bjensen",
            },
        ).json()["id"]
        group_id = wsgi.post(
            "/v2/Groups",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                "displayName": "admins",
            },
        ).json()["id"]

        r = wsgi.get("/v2/", params={"sortBy": "userName"})
        assert [resource["id"] for resource in r.json()["Resources"]] == [
            user_id,
            group_id,
        ]
        r = wsgi.get("/v2/", params={"sortBy": "userName", "sortOrder": "descending"})
        assert [resource["id"] for resource in r.json()["Resources"]] == [
            group_id,
            user_id,
        ]

    @pytest.mark.parametrize(
        ("sort_by", "first"),
        [("externalId", "upper"), ("userName", "lower")],
    )
    def test_sort_follows_the_case_exactness_of_the_attribute(
        self, wsgi, sort_by, first
    ):
        """A case-exact attribute sorts upper case first, another one ignores the case."""
        ids = {}
        for name, value in (("lower", "a"), ("upper", "B")):
            ids[name] = wsgi.post(
                "/v2/Users",
                json={
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                    "userName": value,
                    "externalId": value,
                },
            ).json()["id"]

        r = wsgi.get("/v2/Users", params={"sortBy": sort_by})
        assert r.json()["Resources"][0]["id"] == ids[first]

    def test_sort_on_an_extension_attribute(self, wsgi):
        """An attribute qualified by an extension URN is read from the extension."""
        enterprise = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
        ids = [
            wsgi.post(
                "/v2/Users",
                json={
                    "schemas": [
                        "urn:ietf:params:scim:schemas:core:2.0:User",
                        enterprise,
                    ],
                    "userName": user_name,
                    enterprise: {"employeeNumber": employee_number},
                },
            ).json()["id"]
            for user_name, employee_number in (("a", "2"), ("b", "1"))
        ]

        r = wsgi.get("/v2/Users", params={"sortBy": f"{enterprise}:employeeNumber"})
        assert [resource["id"] for resource in r.json()["Resources"]] == ids[::-1]

    def test_sort_reads_a_sub_attribute_from_the_primary_entry(self, wsgi):
        """A sub-attribute of a multi-valued attribute is read from its primary entry."""
        ids = [
            wsgi.post(
                "/v2/Users",
                json={
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                    "userName": user_name,
                    "emails": emails,
                },
            ).json()["id"]
            for user_name, emails in (
                (
                    "a",
                    [
                        {"value": "a@example.com", "type": "other"},
                        {"value": "b@example.com", "type": "work", "primary": True},
                    ],
                ),
                ("b", [{"value": "c@example.com", "type": "home"}]),
            )
        ]

        r = wsgi.get("/v2/Users", params={"sortBy": "emails.type"})
        assert [resource["id"] for resource in r.json()["Resources"]] == ids[::-1]


def test_a_body_that_is_not_json_answers_415(wsgi):
    """A request body with another media type than JSON is refused with a 415."""
    headers = {"Content-Type": "text/plain"}
    r = wsgi.post("/v2/Users", content=b'{"userName": "bjensen"}', headers=headers)

    assert r.status_code == 415
    assert r.json()["status"] == "415"


@pytest.mark.parametrize("headers", [{}, {"Content-Type": ""}])
def test_a_body_without_media_type_is_read_as_json(wsgi, headers):
    """RFC 7644 §3.8: JSON is the default format of a request body."""
    r = wsgi.post("/v2/Users", content=b'{"userName": "bjensen"}', headers=headers)

    assert r.status_code == 201
    assert r.json()["userName"] == "bjensen"


def test_a_json_body_is_accepted(wsgi):
    """A request body sent as application/json is accepted."""
    r = wsgi.post(
        "/v2/Users",
        content=b'{"userName": "bjensen"}',
        headers={"Content-Type": "application/json"},
    )

    assert r.status_code == 201


def test_a_search_body_that_is_not_valid_json_answers_400(wsgi):
    """A search body that cannot be read as JSON answers 400 invalidSyntax."""
    r = wsgi.post(
        "/v2/Users/.search",
        content=b"{not json",
        headers={"Content-Type": "application/scim+json"},
    )

    assert r.status_code == 400
    assert r.json()["scimType"] == "invalidSyntax"


class TestContentLocation:
    """RFC 7643 §3.1: meta.location is the same as the Content-Location header."""

    def test_creation(self, wsgi):
        """A creation gives the location of the resource in Location and Content-Location."""
        r = wsgi.post("/v2/Users", json={"userName": "bjensen"})
        assert r.status_code == 201
        assert r.headers["Content-Location"] == r.json()["meta"]["location"]
        assert r.headers["Content-Location"] == r.headers["Location"]

    @pytest.mark.parametrize("method", ["GET", "PUT", "PATCH"])
    def test_response_carrying_a_resource(self, wsgi, first_fake_user, method):
        """A response carrying a resource gives its location in Content-Location."""
        url = f"/v2/Users/{first_fake_user}"
        bodies = {
            "PUT": {"userName": "bjensen"},
            "PATCH": {
                "schemas": [PATCH_OP],
                "Operations": [{"op": "replace", "path": "nickName", "value": "Babs"}],
            },
        }
        r = wsgi.request(
            method, url, params={"attributes": "userName"}, json=bodies.get(method)
        )
        assert r.status_code == 200
        location = f"https://scim.example.com{url}"
        assert r.headers["Content-Location"] == location
        assert wsgi.get(url).json()["meta"]["location"] == location

    def test_patch_without_content(self, wsgi, first_fake_user):
        """A PATCH that answers 204 carries no representation, and no Content-Location."""
        r = wsgi.patch(
            f"/v2/Users/{first_fake_user}",
            json={
                "schemas": [PATCH_OP],
                "Operations": [{"op": "replace", "path": "nickName", "value": "Babs"}],
            },
        )
        assert r.status_code == 204
        assert "Content-Location" not in r.headers

    def test_list(self, wsgi):
        """A list of resources has no Content-Location."""
        r = wsgi.get("/v2/Users")
        assert "Content-Location" not in r.headers

    @pytest.mark.parametrize(
        "path",
        ["/ServiceProviderConfig", "/ResourceTypes/User", "/Schemas/" + USER_SCHEMA],
    )
    def test_discovery_resource(self, wsgi, path):
        """A discovery resource gives its location in Content-Location."""
        r = wsgi.get(f"/v2{path}")
        assert r.headers["Content-Location"] == r.json()["meta"]["location"]
        assert r.headers["Content-Location"] == f"https://scim.example.com/v2{path}"
