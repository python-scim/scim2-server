from unittest.mock import patch

import httpx2
import pytest
from scim2_models import Context
from scim2_models import NotFoundException
from werkzeug.exceptions import HTTPException

from scim2_server.service import ScimService
from scim2_server.werkzeug import SCIMApplication


class TestProvider:
    def test_user_creation(self, app, user_type):
        user_model = app.provider.model_for("User").model_validate(
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
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
            },
            scim_ctx=Context.RESOURCE_CREATION_REQUEST,
        )
        ret = app.storage.create(user_type, user_model)
        assert ret.id is not None

    def test_generic_exception_handling(self, app):
        """An unexpected error answers 500 without disclosing its message or traceback."""
        from werkzeug import Request

        # Create a mock WSGI environ
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/v2/ServiceProviderConfig",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8000",
            "wsgi.url_scheme": "http",
        }

        request = Request(environ)

        # Mock to force a generic exception during request processing
        with patch.object(
            app.service,
            "service_provider_config",
            side_effect=RuntimeError("Test error"),
        ):
            response = app.wsgi_app(request, environ)

            # Should return a Response object with status 500
            assert response.status_code == 500
            assert response.json["detail"] == "Internal server error"
            assert "Traceback" not in response.get_data(as_text=True)

    def test_replace_resource_lost_by_the_storage(self, app, wsgi, first_fake_user):
        """A PUT answers 404 when the storage no longer has the resource to update."""
        with patch.object(app.storage, "update", side_effect=NotFoundException()):
            r = wsgi.put(
                f"/v2/Users/{first_fake_user}",
                json={
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                    "userName": "replaced",
                },
            )

        assert r.status_code == 404

    def test_patch_resource_lost_by_the_storage(self, app, wsgi, first_fake_user):
        """A PATCH answers 404 when the storage no longer has the resource to update."""
        with patch.object(app.storage, "update", side_effect=NotFoundException()):
            r = wsgi.patch(
                f"/v2/Users/{first_fake_user}",
                json={
                    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                    "Operations": [
                        {"op": "replace", "path": "displayName", "value": "patched"}
                    ],
                },
            )

        assert r.status_code == 404

    def test_http_exception_without_status_code(self, app, wsgi):
        """An HTTP exception without status code answers 500."""
        with patch.object(
            app.service,
            "service_provider_config",
            side_effect=HTTPException("Something went wrong"),
        ):
            r = wsgi.get("/v2/ServiceProviderConfig")

        assert r.status_code == 500

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/v2/ServiceProviderConfig"),
            ("POST", "/v2/ResourceTypes"),
            ("POST", "/v2/Schemas"),
            ("PUT", "/v2/Schemas"),
            ("POST", "/Schemas"),
            ("PUT", "/v2/Schemas/urn:ietf:params:scim:schemas:core:2.0:User"),
            ("DELETE", "/v2/ResourceTypes/User"),
            ("GET", "/v2/Bulk"),
        ],
    )
    def test_unsupported_method_on_a_reserved_endpoint(self, wsgi, method, path):
        """A method a reserved endpoint does not support answers 405."""
        r = wsgi.request(method, path)

        assert r.status_code == 405

    @pytest.mark.parametrize(
        ("method", "path", "allowed"),
        [
            ("POST", "/v2/Schemas", "GET, HEAD"),
            ("GET", "/v2/Bulk", "POST"),
        ],
    )
    def test_method_not_allowed_lists_the_supported_methods(
        self, wsgi, method, path, allowed
    ):
        """A 405 answer tells the methods the endpoint supports."""
        r = wsgi.request(method, path)

        assert r.headers["Allow"] == allowed

    def test_unknown_resource_endpoint(self, wsgi):
        """An endpoint that is neither reserved nor served answers 404."""
        r = wsgi.get("/v2/SchemasArchive")

        assert r.status_code == 404


def test_a_resource_of_an_unknown_type_from_the_storage(app, wsgi, first_fake_user):
    """A resource whose meta.resourceType the provider does not serve answers 500."""
    (user,) = app.storage.resources
    user.meta.resource_type = "Unknown"

    with patch.object(app.storage, "search", return_value=(1, [user])):
        r = wsgi.get("/v2/Users")

    assert r.status_code == 500


def test_a_given_service_serves_the_requests(storage, scim_provider):
    """The service passed to the application builds its responses."""

    class ElsewhereService(ScimService):
        def resource_location(self, base_url, resource_type, resource_id):
            return f"https://ids.example/{resource_type.id}/{resource_id}"

    app = SCIMApplication(
        storage, scim_provider, service=ElsewhereService(scim_provider)
    )
    with httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="https://scim.example.com"
    ) as client:
        r = client.post("/v2/Users", json={"userName": "bjensen"})

    assert r.headers["Location"] == f"https://ids.example/User/{r.json()['id']}"
    assert r.json()["meta"]["location"] == r.headers["Location"]
