Serve the /Me endpoint
======================

Use this guide to serve ``/Me``, the alias of the resource of the authenticated client
(:rfc:`RFC 7644 §3.11 <7644#section-3.11>`). It assumes an integration built with
:doc:`integrate-a-web-framework`, and clients authenticated with
:doc:`authenticate-the-clients`.

By default, ``/Me`` answers 501. A request on ``/Me`` acts on a resource that depends on the
client: a ``User`` for a person, or another type, such as a device. The application tells which
one.

Pass the subject
----------------

Pass the authenticated client in the ``subject`` of the
:class:`~scim2_server.requests.ScimRequest`, as in :ref:`pass-the-client`. The service passes it
to :meth:`~scim2_server.service.ScimService.me_target` and
:meth:`~scim2_server.service.ScimService.me_creation_type`. The subject can be the user of the
framework, the claims of a token, or any other object.

Find the resource of the subject
--------------------------------

The service needs the resource that ``/Me`` stands for. Subclass
:class:`~scim2_server.service.ScimService`, and override
:meth:`~scim2_server.service.ScimService.me_target`. It returns the resource type and the
identifier of this resource. :meth:`~scim2_server.service.ScimService.get_resource_type` gives a
resource type from its name. Raise :class:`~scim2_models.NotFoundException` when the subject has
no resource. An application that accepts anonymous requests raises
:class:`~scim2_models.UnauthorizedException` when the request has no subject:

.. doctest::

    >>> from scim2_models import NotFoundException
    >>> from scim2_models import UnauthorizedException
    >>> from scim2_server.service import ScimService

    >>> class MeService(ScimService):
    ...     def me_target(self, request):
    ...         if request.subject is None:
    ...             raise UnauthorizedException
    ...         if request.subject.get("user_id") is None:
    ...             raise NotFoundException(detail="No user for this client")
    ...         return self.get_resource_type("User"), request.subject["user_id"]

The handler then serves ``GET``, ``PUT``, ``PATCH`` and ``DELETE`` on ``/Me`` as on the URL of
the resource. :meth:`~scim2_server.service.ScimService.authorize` checks them as operations on
this resource, after :meth:`~scim2_server.service.ScimService.me_target` finds it
(:doc:`authenticate-the-clients`). Each response carries the URL of the resource in its
:mdn:`Location` header:

.. doctest::

    >>> from scim2_server.handler import ScimHandler
    >>> from scim2_server.memory import InMemoryStorage
    >>> from scim2_server.requests import ScimRequest
    >>> from scim2_server.utils import load_default_provider

    >>> handler = ScimHandler(MeService(load_default_provider()), InMemoryStorage())
    >>> user = handler.handle(
    ...     ScimRequest(
    ...         "POST",
    ...         "https://scim.example/v2",
    ...         "/Users",
    ...         headers={"Content-Type": "application/scim+json"},
    ...         body=b'{"userName": "bjensen"}',
    ...     )
    ... ).body

    >>> response = handler.handle(
    ...     ScimRequest(
    ...         "GET", "https://scim.example/v2", "/Me", subject={"user_id": user["id"]}
    ...     )
    ... )
    >>> response.body["userName"]
    'bjensen'
    >>> response.headers["Location"] == user["meta"]["location"]
    True

Let the clients register themselves
-----------------------------------

A POST on ``/Me`` creates the resource of a client that has none yet. Such requests have risks
(:rfc:`RFC 7644 §7.6 <7644#section-7.6>`). A POST on ``/Me`` answers 501 by default.

To accept it, override :meth:`~scim2_server.service.ScimService.me_creation_type`. It returns
the resource type of the resource to create, for instance from the subject or from the
``schemas`` of the body. The following service lets a client without user create one:

.. doctest::

    >>> from scim2_models import UniquenessException

    >>> class RegisteringService(MeService):
    ...     def me_creation_type(self, request):
    ...         if request.subject is None:
    ...             raise UnauthorizedException
    ...         if request.subject.get("user_id") is not None:
    ...             raise UniquenessException(detail="This client already has a user")
    ...         return self.get_resource_type("User")

    >>> handler = ScimHandler(RegisteringService(load_default_provider()), InMemoryStorage())
    >>> client = {"user_id": None}
    >>> response = handler.handle(
    ...     ScimRequest(
    ...         "POST",
    ...         "https://scim.example/v2",
    ...         "/Me",
    ...         headers={"Content-Type": "application/scim+json"},
    ...         body=b'{"userName": "jdoe"}',
    ...         subject=client,
    ...     )
    ... )
    >>> response.status
    <HTTPStatus.CREATED: 201>

After the creation, link the new resource to the subject in the application, so that
:meth:`~scim2_server.service.ScimService.me_target` finds it:

.. doctest::

    >>> client["user_id"] = response.body["id"]
    >>> response = handler.handle(
    ...     ScimRequest("GET", "https://scim.example/v2", "/Me", subject=client)
    ... )
    >>> response.body["userName"]
    'jdoe'
