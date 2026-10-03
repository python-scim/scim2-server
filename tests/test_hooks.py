from http import HTTPStatus

import httpx2
import pytest
from scim2_models import Error
from scim2_models import NotFoundException
from scim2_models import UnauthorizedException

from scim2_server.responses import ScimResponse
from scim2_server.wsgi import WSGIApplication

BULK_REQUEST = "urn:ietf:params:scim:api:messages:2.0:BulkRequest"


class RecordingApplication(WSGIApplication):
    """An application that records what its hooks receive."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dispatched = []
        self.exceptions = []
        self.responses = []

    def dispatch_request(self, request):
        self.dispatched.append((request.method, request.path))
        return super().dispatch_request(request)

    def handle_exception(self, request, exception):
        self.exceptions.append(exception)
        return super().handle_exception(request, exception)

    def serve(self, request):
        response = super().serve(request)
        self.responses.append(response)
        return response


@pytest.fixture
def recording_app(storage, scim_provider):
    return RecordingApplication(storage, scim_provider)


def client_of(app):
    return httpx2.Client(
        transport=httpx2.WSGITransport(app=app), base_url="https://scim.example.com"
    )


@pytest.fixture
def client(recording_app):
    with client_of(recording_app) as client:
        yield client


def test_dispatch_request_receives_the_scim_request(client, recording_app):
    """The method and the path of a request, relative to the SCIM root, are passed to dispatch_request."""
    client.get("/v2/Users/unknown")

    assert recording_app.dispatched == [("GET", "/Users/unknown")]


def test_dispatch_request_can_answer_in_place_of_the_endpoint(storage, scim_provider):
    """A response returned by dispatch_request is sent instead of the endpoint response."""

    class ThrottledApplication(WSGIApplication):
        def dispatch_request(self, request):
            error = Error(status=429, detail="Too many requests")
            return ScimResponse(HTTPStatus.TOO_MANY_REQUESTS, error.model_dump())

    with client_of(ThrottledApplication(storage, scim_provider)) as client:
        r = client.get("/v2/Users")

    assert r.status_code == 429
    assert r.json()["detail"] == "Too many requests"


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


def test_serve_returns_the_response_sent_to_the_client(storage, scim_provider):
    """The response returned by serve carries the headers added to every response."""

    class RefusingApplication(RecordingApplication):
        def check_auth(self, request):
            raise UnauthorizedException

    app = RefusingApplication(storage, scim_provider)
    with client_of(app) as client:
        client.get("/v2/Users")

    (response,) = app.responses
    assert response.status == 401
    assert response.headers["Location"] == "https://scim.example.com/v2/Users"


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/v2/ServiceProviderConfig", 200),
        ("/v2/Users/unknown", 404),
        ("/v2/Unknown/path/to/nothing", 404),
    ],
)
def test_finalize_response_receives_every_response(
    storage, scim_provider, path, status
):
    """Successful and error responses all go through finalize_response."""

    class TaggingApplication(WSGIApplication):
        def finalize_response(self, request, response):
            response.headers["X-Finalized"] = "yes"
            return super().finalize_response(request, response)

    with client_of(TaggingApplication(storage, scim_provider)) as client:
        r = client.get(path)

    assert r.status_code == status
    assert r.headers["X-Finalized"] == "yes"
