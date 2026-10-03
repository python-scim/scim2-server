import httpx2

from scim2_server.wsgi import ForwardedHeaders
from scim2_server.wsgi import WSGIApplication


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
