from unittest.mock import patch

from scim2_models import Context
from werkzeug.exceptions import HTTPException


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
        ret = app.backend.create_resource(user_type, user_model)
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
            app,
            "call_service_provider_config",
            side_effect=RuntimeError("Test error"),
        ):
            response = app.wsgi_app(request, environ)

            # Should return a Response object with status 500
            assert response.status_code == 500
            assert response.json["detail"] == "Internal server error"
            assert "Traceback" not in response.get_data(as_text=True)

    def test_replace_resource_lost_by_the_backend(self, app, wsgi, first_fake_user):
        """A PUT answers 404 when the backend no longer has the resource to update."""
        with patch.object(app.backend, "update_resource", return_value=None):
            r = wsgi.put(
                f"/v2/Users/{first_fake_user}",
                json={
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                    "userName": "replaced",
                },
            )

        assert r.status_code == 404

    def test_patch_resource_lost_by_the_backend(self, app, wsgi, first_fake_user):
        """A PATCH answers 404 when the backend no longer has the resource to update."""
        with patch.object(app.backend, "update_resource", return_value=None):
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
            app,
            "call_service_provider_config",
            side_effect=HTTPException("Something went wrong"),
        ):
            r = wsgi.get("/v2/ServiceProviderConfig")

        assert r.status_code == 500
