import pytest
from scim2_models import NotFoundException

from scim2_server.service import ScimService
from scim2_server.service import is_json_media_type


@pytest.mark.parametrize(
    ("content_type", "is_json"),
    [
        ("application/scim+json", True),
        ("application/scim+json; charset=utf-8", True),
        ("Application/SCIM+JSON", True),
        ("application/json", True),
        ("application/vnd.example+json", True),
        ("text/plain", False),
        ("text/json", False),
        ("", False),
        (None, False),
    ],
)
def test_json_media_types(content_type, is_json):
    """SCIM, plain and suffixed JSON media types are accepted, whatever their parameters."""
    assert is_json_media_type(content_type) is is_json


def test_error_response(scim_provider):
    """An exception becomes the SCIM error response of its status."""
    result = ScimService(scim_provider).error_response(NotFoundException(detail="Gone"))

    assert result.status == 404
    assert result.body["detail"] == "Gone"
    assert result.headers["Content-Type"] == "application/scim+json"


def test_unexpected_error_response(scim_provider):
    """An unexpected exception becomes a 500 that discloses nothing."""
    result = ScimService(scim_provider).error_response(RuntimeError("secret"))

    assert result.status == 500
    assert result.body["detail"] == "Internal server error"
