import httpx2
import pytest
from scim2_models import Error
from scim2_models import NotFoundException
from werkzeug.exceptions import Unauthorized

from scim2_server.werkzeug import SCIMApplication

BULK_REQUEST = "urn:ietf:params:scim:api:messages:2.0:BulkRequest"


class RecordingApplication(SCIMApplication):
    """An application that records what its hooks receive."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dispatched = []
        self.exceptions = []
        self.responses = []

    def dispatch_request(self, request, endpoint, args):
        self.dispatched.append((endpoint, dict(args)))
        return super().dispatch_request(request, endpoint, args)

    def handle_exception(self, request, exception):
        self.exceptions.append(exception)
        return super().handle_exception(request, exception)

    def wsgi_app(self, request, environ):
        response = super().wsgi_app(request, environ)
        self.responses.append(response)
        return response


@pytest.fixture
def recording_app(storage, scim_provider):
    return RecordingApplication(storage, scim_provider)


@pytest.fixture
def client(recording_app):
    transport = httpx2.WSGITransport(app=recording_app)
    with httpx2.Client(
        transport=transport, base_url="https://scim.example.com"
    ) as client:
        yield client


def test_dispatch_request_receives_the_endpoint_and_the_path_arguments(
    client, recording_app
):
    """The endpoint and the path arguments of a request are passed to dispatch_request."""
    client.get("/v2/Users/unknown")

    assert recording_app.dispatched == [
        ("single_resource", {"resource_endpoint": "Users", "resource_id": "unknown"})
    ]


def test_dispatch_request_can_answer_in_place_of_the_endpoint(storage, scim_provider):
    """A response returned by dispatch_request is sent instead of the endpoint response."""

    class ThrottledApplication(SCIMApplication):
        def dispatch_request(self, request, endpoint, args):
            return self.make_error(Error(status=429, detail="Too many requests"))

    app = ThrottledApplication(storage, scim_provider)
    with httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="https://scim.example.com"
    ) as client:
        r = client.get("/v2/Users")

    assert r.status_code == 429
    assert r.headers["Location"] == "https://scim.example.com/v2/Users"


def test_dispatch_request_is_not_called_for_an_unrouted_request(client, recording_app):
    """A request matching no endpoint is answered without dispatch_request."""
    r = client.delete("/v2/Schemas")

    assert r.status_code == 405
    assert recording_app.dispatched == []


def test_handle_exception_receives_the_exception_of_the_request(client, recording_app):
    """The exception raised while serving a request is passed to handle_exception."""
    r = client.get("/v2/Users/unknown")

    assert r.status_code == 404
    (exception,) = recording_app.exceptions
    assert isinstance(exception, NotFoundException)


def test_handle_exception_is_not_called_for_a_failed_bulk_operation(
    client, recording_app
):
    """The error of a bulk operation stays in the bulk response."""
    r = client.post(
        "/v2/Bulk",
        json={
            "schemas": [BULK_REQUEST],
            "Operations": [{"method": "DELETE", "path": "/Users/unknown"}],
        },
    )

    assert r.status_code == 200
    assert r.json()["Operations"][0]["status"] == "404"
    assert recording_app.exceptions == []


def test_wsgi_app_returns_the_response_sent_to_the_client(storage, scim_provider):
    """The response returned by wsgi_app carries the headers added to every response."""

    class RefusingApplication(RecordingApplication):
        def check_auth(self, request):
            raise Unauthorized

    app = RefusingApplication(storage, scim_provider)
    transport = httpx2.WSGITransport(app=app)
    with httpx2.Client(transport=transport, base_url="https://scim.example.com") as c:
        c.get("/v2/Users")

    (response,) = app.responses
    assert response.status_code == 401
    assert response.headers["Location"] == "https://scim.example.com/v2/Users"


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/v2/ServiceProviderConfig", 200),
        ("/v2/Users/unknown", 404),
        ("/v2", 308),
    ],
)
def test_finalize_response_receives_every_response(
    storage, scim_provider, path, status
):
    """Successful, error and redirect responses all go through finalize_response."""

    class TaggingApplication(SCIMApplication):
        def finalize_response(self, request, response):
            response.headers["X-Finalized"] = "yes"
            return super().finalize_response(request, response)

    app = TaggingApplication(storage, scim_provider)
    with httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="https://scim.example.com"
    ) as client:
        r = client.get(path, follow_redirects=False)

    assert r.status_code == status
    assert r.headers["X-Finalized"] == "yes"
