import logging
from unittest.mock import patch

import httpx2
import pytest
from scim2_models import Context
from scim2_models import NotFoundException

from scim2_server.applications.wsgi import WSGIApplication
from scim2_server.service import ScimService
from scim2_server.storage import SearchPage

from .conftest import SECRET


class TestApplication:
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

    def test_generic_exception_handling(self, app, wsgi):
        """An unexpected error answers 500 without disclosing its message or traceback."""
        with patch.object(
            app.service,
            "service_provider_config",
            side_effect=RuntimeError("Test error"),
        ):
            r = wsgi.get("/v2/ServiceProviderConfig")

        assert r.status_code == 500
        assert r.json()["detail"] == "Internal server error"
        assert "Traceback" not in r.text

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
            ("POST", "/v2/Schemas", "GET"),
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

    with patch.object(app.storage, "search", return_value=SearchPage(1, [user])):
        r = wsgi.get("/v2/Users")

    assert r.status_code == 500


def test_a_given_service_serves_the_requests(storage, scim_provider):
    """The service passed to the application builds its responses."""

    class ElsewhereService(ScimService):
        def resource_location(self, base_url, resource_type, resource_id):
            return f"https://ids.example/{resource_type.id}/{resource_id}"

    app = WSGIApplication(
        storage, scim_provider, service=ElsewhereService(scim_provider, secret=SECRET)
    )
    with httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="https://scim.example.com"
    ) as client:
        r = client.post("/v2/Users", json={"userName": "bjensen"})

    assert r.headers["Location"] == f"https://ids.example/User/{r.json()['id']}"
    assert r.json()["meta"]["location"] == r.headers["Location"]


def test_a_client_error_is_logged_without_traceback(wsgi, caplog):
    """An error of the client is logged at the INFO level, without traceback."""
    with caplog.at_level(logging.INFO, logger="scim2_server"):
        wsgi.get("/v2/Users/unknown")

    (record,) = caplog.records
    assert record.levelno == logging.INFO
    assert record.exc_info is None
    assert "404" in record.getMessage()


def test_an_http_error_is_logged_without_traceback(wsgi, caplog):
    """A werkzeug HTTP error, such as an unknown URL, is logged at the INFO level, without traceback."""
    with caplog.at_level(logging.INFO, logger="scim2_server"):
        wsgi.get("/unknown/path/to/nothing")

    (record,) = caplog.records
    assert record.levelno == logging.INFO
    assert record.exc_info is None


def test_an_unexpected_error_is_logged_with_its_traceback(app, wsgi, caplog):
    """An unexpected exception is logged at the ERROR level, with its traceback."""
    with patch.object(
        app.service, "service_provider_config", side_effect=RuntimeError("boom")
    ):
        wsgi.get("/v2/ServiceProviderConfig")

    (record,) = caplog.records
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
