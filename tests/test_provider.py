from unittest.mock import patch

from scim2_models import Context


class TestProvider:
    def test_user_creation(self, provider):
        user_model = provider.backend.get_model("User").model_validate(
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
        ret = provider.backend.create_resource("User", user_model)
        assert ret.id is not None

    def test_generic_exception_handling(self, provider):
        """Test that generic exceptions are properly handled and return 500 status."""
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
            provider,
            "call_service_provider_config",
            side_effect=RuntimeError("Test error"),
        ):
            response = provider.wsgi_app(request, environ)

            # Should return a Response object with status 500
            assert response.status_code == 500
            # The response should contain error details
            response_data = response.get_data(as_text=True)
            assert "Test error" in response_data
