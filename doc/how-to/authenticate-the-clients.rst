Authenticate and authorize the clients
======================================

Use this guide to secure the SCIM endpoints of an application: refuse the unknown clients, and
limit what each known client may do. It is meant for developers who serve scim2-server from a web
application. It assumes an integration built with :doc:`integrate-a-web-framework`, and a way to
validate the credentials of the clients, such as the tokens of
`Authlib <https://docs.authlib.org>`_.

The guide does not cover how to issue the tokens, nor the access to single attributes.
:doc:`serve-the-me-endpoint` covers ``/Me``.

The client gets a 401 when its credentials are missing or invalid, and a 403 when it is known but
may not perform the operation.

Refuse the unknown clients
--------------------------

Authenticate the client before the handler. The sketches of this guide describe the client with
one object. ``verify_token`` stands for the code that validates a token and returns this object,
or :data:`None` for an invalid token. Load the scopes and the permissions of the client at this
point: `Authorize each operation`_ reads them.

.. doctest::

    >>> from dataclasses import dataclass

    >>> @dataclass
    ... class Client:
    ...     user_id: str | None
    ...     scopes: set[str]
    ...     organization: str

Raise :class:`~scim2_models.UnauthorizedException` when the credentials are missing or invalid.
The error handler of :doc:`integrate-a-web-framework` turns it into a SCIM error. Leave
``/ServiceProviderConfig`` open: per :rfc:`RFC 7643 §5 <7643#section-5>`, the authentication
schemes should be readable without authentication. :meth:`~scim2_server.service.ScimService.match`
tells which operation a request asks for:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          @scim.before_request
          def authenticate():
              g.client = None
              if service.match(scim_request()).operation is Operation.service_provider_config:
                  return
              authorization = request.authorization
              if authorization is not None and authorization.type == "bearer":
                  g.client = verify_token(authorization.token)
              if g.client is None:
                  raise UnauthorizedException

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          async def authenticate(request: Request) -> Client | None:
              scim_req = await scim_request(request)
              if service.match(scim_req).operation is Operation.service_provider_config:
                  return None
              scheme, _, token = request.headers.get("authorization", "").partition(" ")
              client = await verify_token(token) if scheme.lower() == "bearer" else None
              if client is None:
                  raise UnauthorizedException
              return client


          scim = APIRouter(dependencies=[Depends(authenticate)])

``scim`` is the blueprint of the SCIM routes with Flask, and their router with FastAPI. With
:class:`~scim2_server.applications.wsgi.WSGIApplication` or
:class:`~scim2_server.applications.asgi.ASGIApplication`, override
:meth:`~scim2_server.applications.base.BaseApplication.check_auth` instead.

.. _pass-the-client:

Pass the client to the service
------------------------------

Pass the client in the :attr:`~scim2_server.requests.ScimRequest.subject` of the request. The
service does not read it. It passes the request to the methods that an application overrides,
such as :meth:`~scim2_server.service.ScimService.authorize` and
:meth:`~scim2_server.service.ScimService.me_target`:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          @scim.route("/", defaults={"path": ""}, methods=METHODS)
          @scim.route("/<path:path>", methods=METHODS)
          def serve(path):
              scim_req = scim_request()
              scim_req.subject = g.client
              return to_response(handler.handle(scim_req))

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          @scim.api_route("/{path:path}", methods=METHODS)
          async def serve(request: Request, client: Client | None = Depends(authenticate)):
              scim_req = await scim_request(request)
              scim_req.subject = client
              return to_response(await handler.handle(scim_req))

FastAPI calls ``authenticate`` once per request, for the router and for the view. With
:class:`~scim2_server.applications.wsgi.WSGIApplication` or
:class:`~scim2_server.applications.asgi.ASGIApplication`, override
:meth:`~scim2_server.applications.base.BaseApplication.get_subject` instead.

Authorize each operation
------------------------

Override :meth:`~scim2_server.service.ScimService.authorize` to check the rights of the client
on each operation. The service calls it for every operation of a request, every operation of a
bulk request, and every resource type of a search at the root. Raise
:class:`~scim2_models.ForbiddenException` to refuse the operation.

The following service maps each resource type to a scope for reading and a scope for writing,
such as ``scim:User:read``. A client may also read its own user without any scope:

.. doctest::

    >>> from scim2_models import ForbiddenException
    >>> from scim2_server.routing import Operation
    >>> from scim2_server.service import ScimService

    >>> READS = {Operation.query, Operation.search, Operation.search_with_body}

    >>> class ScopedService(ScimService):
    ...     def authorize(self, request, target, resource_type):
    ...         client = request.subject
    ...         own_user = target.resource_id == client.user_id
    ...         if target.operation is Operation.query and own_user:
    ...             return
    ...         action = "read" if target.operation in READS else "write"
    ...         if f"scim:{resource_type.name}:{action}" not in client.scopes:
    ...             raise ForbiddenException

In a bulk request, a refused operation fails with a 403, and the other operations run
(:rfc:`RFC 7644 §3.7.3 <7644#section-3.7.3>`). The following client may manage the users, but
not the groups:

.. doctest::

    >>> import json
    >>> from scim2_server.handler import ScimHandler
    >>> from scim2_server.memory import InMemoryStorage
    >>> from scim2_server.requests import ScimRequest
    >>> from scim2_server.utils import load_default_provider

    >>> handler = ScimHandler(ScopedService(load_default_provider()), InMemoryStorage())
    >>> client = Client(
    ...     user_id=None,
    ...     scopes={"scim:User:read", "scim:User:write"},
    ...     organization="example",
    ... )
    >>> body = {
    ...     "schemas": ["urn:ietf:params:scim:api:messages:2.0:BulkRequest"],
    ...     "Operations": [
    ...         {
    ...             "method": "POST",
    ...             "path": "/Users",
    ...             "bulkId": "user",
    ...             "data": {"userName": "bjensen"},
    ...         },
    ...         {
    ...             "method": "POST",
    ...             "path": "/Groups",
    ...             "bulkId": "group",
    ...             "data": {"displayName": "admins"},
    ...         },
    ...     ],
    ... }
    >>> response = handler.handle(
    ...     ScimRequest(
    ...         "POST",
    ...         "https://scim.example/v2",
    ...         "/Bulk",
    ...         headers={"Content-Type": "application/scim+json"},
    ...         body=json.dumps(body).encode(),
    ...         subject=client,
    ...     )
    ... )
    >>> [operation["status"] for operation in response.body["Operations"]]
    ['201', '403']

A search at the root leaves out the resource types that
:meth:`~scim2_server.service.ScimService.authorize` refuses. The search answers 403 when
:meth:`~scim2_server.service.ScimService.authorize` refuses every type:

.. doctest::

    >>> response = handler.handle(
    ...     ScimRequest("GET", "https://scim.example/v2", "/", subject=client)
    ... )
    >>> [resource["userName"] for resource in response.body["Resources"]]
    ['bjensen']

Read the operation in the target, whatever the URL of the request:

- in a bulk request, the method of the request is always ``POST``, and its path ``/Bulk``;
- in a request on ``/Me``, the target holds the resource of the subject, and
  :attr:`Target.me <scim2_server.routing.Target.me>` is :data:`True`.

:meth:`~scim2_server.service.ScimService.authorize` must not do any input or output: read the
rights that ``verify_token`` loaded. :doc:`../explanation/architecture` explains why.

Restrict the access to some resources
-------------------------------------

Some rules depend on the stored resources, such as a client that only manages the users of its
own organization (:rfc:`RFC 7644 §2 <7644#section-2>`).
:meth:`~scim2_server.service.ScimService.authorize` cannot apply them: it does not read the
storage. Apply them in a storage bound to the organization of the client, such as
``OrganizationStorage``. Build the handler for each request, with this storage and the service of
`Authorize each operation`_. A handler holds no state. The client is :data:`None` on
``/ServiceProviderConfig``, and the handler does not call the storage there:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          service = ScopedService(provider)


          @scim.route("/", defaults={"path": ""}, methods=METHODS)
          @scim.route("/<path:path>", methods=METHODS)
          def serve(path):
              organization = g.client.organization if g.client else None
              handler = ScimHandler(service, OrganizationStorage(db.session, organization))
              scim_req = scim_request()
              scim_req.subject = g.client
              return to_response(handler.handle(scim_req))

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          service = ScopedService(provider)


          @scim.api_route("/{path:path}", methods=METHODS)
          async def serve(
              request: Request,
              client: Client | None = Depends(authenticate),
              session=Depends(get_session),
          ):
              organization = client.organization if client else None
              handler = AsyncScimHandler(service, OrganizationStorage(session, organization))
              scim_req = await scim_request(request)
              scim_req.subject = client
              return to_response(await handler.handle(scim_req))

``OrganizationStorage`` raises :class:`~scim2_models.NotFoundException` for a resource of
another organization. The client gets a 404, as for a resource that does not exist:

- a search only counts the resources of the organization in ``totalResults``;
- in a bulk request, an operation on another organization fails with a 404, and the other
  operations run.

Announce the schemes
--------------------

List the authentication schemes in the
:attr:`~scim2_models.ServiceProviderConfig.authentication_schemes` of the provider. The
``/ServiceProviderConfig`` endpoint publishes them, and each 401 response carries a
:mdn:`WWW-Authenticate` header with one challenge per Bearer or Basic scheme
(:rfc:`RFC 7644 §2 <7644#section-2>`):

.. doctest::

    >>> from scim2_models import AuthenticationScheme
    >>> from scim2_models import UnauthorizedException

    >>> provider = load_default_provider()
    >>> provider.config.authentication_schemes = [
    ...     AuthenticationScheme(
    ...         type=AuthenticationScheme.Type.oauthbearertoken,
    ...         name="OAuth Bearer Token",
    ...         description="Authentication with an OAuth 2.0 bearer token",
    ...     )
    ... ]
    >>> service = ScopedService(provider)
    >>> service.error_response(UnauthorizedException()).headers["WWW-Authenticate"]
    'Bearer realm="SCIM"'

To announce another scheme, or to add parameters to the challenge, override
:meth:`~scim2_server.service.ScimService.www_authenticate`. For instance, the
``resource_metadata`` parameter of :rfc:`RFC 9728 §5.1 <9728#section-5.1>` tells the clients
where to discover the authorization server:

.. doctest::

    >>> class ProtectedResourceService(ScopedService):
    ...     def www_authenticate(self, exception):
    ...         metadata = "https://scim.example/.well-known/oauth-protected-resource/scim/v2"
    ...         return f'Bearer resource_metadata="{metadata}"'

    >>> service = ProtectedResourceService(provider)
    >>> service.error_response(UnauthorizedException()).headers["WWW-Authenticate"]
    'Bearer resource_metadata="https://scim.example/.well-known/oauth-protected-resource/scim/v2"'

The application serves the metadata document itself, outside of the SCIM endpoints.
