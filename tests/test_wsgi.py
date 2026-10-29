from unittest.mock import patch

import httpx2
import pytest

from scim2_server.applications.wsgi import ForwardedHeaders
from scim2_server.applications.wsgi import WSGIApplication


def test_an_unexpected_error_reaches_the_caller_when_exceptions_propagate(
    storage, scim_provider
):
    """With propagate_exceptions, an unexpected exception is raised to the caller instead of answering 500."""
    app = WSGIApplication(storage, scim_provider, propagate_exceptions=True)
    with (
        patch.object(
            app.service, "service_provider_config", side_effect=RuntimeError("boom")
        ),
        httpx2.Client(
            transport=httpx2.WSGITransport(app=app), base_url="https://scim.example.com"
        ) as client,
        pytest.raises(RuntimeError, match="boom"),
    ):
        client.get("/v2/ServiceProviderConfig")


def test_a_scim_error_is_answered_when_exceptions_propagate(storage, scim_provider):
    """With propagate_exceptions, a SCIM error still answers a SCIM error response."""
    app = WSGIApplication(storage, scim_provider, propagate_exceptions=True)
    with httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="https://scim.example.com"
    ) as client:
        r = client.get("/v2/Users/unknown")

    assert r.status_code == 404
    assert r.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]


def test_forwarded_headers_give_the_urls_of_the_client(storage, scim_provider):
    """Behind a reverse proxy, the locations use the scheme, host, port and prefix the client used."""
    app = ForwardedHeaders(WSGIApplication(storage, scim_provider))
    with httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="http://internal:8080"
    ) as client:
        r = client.post(
            "/v2/Users",
            json={"userName": "bjensen"},
            headers={
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "scim.example.com, proxy",
                "X-Forwarded-Port": "8443",
                "X-Forwarded-Prefix": "/directory/",
                "X-Forwarded-For": "203.0.113.7",
            },
        )

    assert r.headers["Location"].startswith(
        "https://scim.example.com:8443/directory/v2/Users/"
    )


def test_without_forwarded_headers_nothing_changes(storage, scim_provider):
    """Without forwarded headers, the locations use the URL of the request."""
    app = ForwardedHeaders(WSGIApplication(storage, scim_provider))
    with httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="http://internal:8080"
    ) as client:
        r = client.post("/v2/Users", json={"userName": "bjensen"})

    assert r.headers["Location"].startswith("http://internal:8080/v2/Users/")


def test_an_unknown_path_with_a_body_answers_404(storage, scim_provider):
    """A request with a body on an unknown path answers 404."""
    app = WSGIApplication(storage, scim_provider)
    with httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="http://internal:8080"
    ) as client:
        r = client.post("/v2/Unknown/path/more", json={"userName": "bjensen"})

    assert r.status_code == 404
