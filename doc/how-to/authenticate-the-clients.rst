Authenticate the clients
========================

Use this guide to restrict the SCIM endpoints to known clients. The application authenticates
the clients before it calls the handler, with the tools of its framework or a library such as
`Authlib <https://docs.authlib.org>`_. The guide assumes an integration built
with :doc:`integrate-a-web-framework`.

Refuse the unknown clients
--------------------------

Authenticate the client before the handler. Raise :class:`~scim2_models.UnauthorizedException`
when the credentials are missing or invalid, and :class:`~scim2_models.ForbiddenException` when
the client may not perform the operation. The error handler of
:doc:`integrate-a-web-framework` turns them into SCIM errors.

Leave ``/ServiceProviderConfig`` open: :rfc:`RFC 7643 §5 <7643#section-5>` recommends
publishing the authentication schemes without authentication.

The following sketch checks a bearer token with Flask and FastAPI. ``verify_token`` stands for
the code that validates a token and returns its client, or :data:`None` for an invalid token.
Only the clients with the ``scim:write`` scope may change the resources:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          @scim.before_request
          def authenticate():
              if request.path.endswith("/ServiceProviderConfig"):
                  return
              token = request.authorization.token if request.authorization else None
              client = verify_token(token)
              if client is None:
                  raise UnauthorizedException
              if request.method != "GET" and "scim:write" not in client.scopes:
                  raise ForbiddenException
              g.client = client

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          async def authenticate(request: Request):
              if request.url.path.endswith("/ServiceProviderConfig"):
                  return None
              scheme, _, token = request.headers.get("authorization", "").partition(" ")
              client = await verify_token(token) if scheme.lower() == "bearer" else None
              if client is None:
                  raise UnauthorizedException
              if request.method != "GET" and "scim:write" not in client.scopes:
                  raise ForbiddenException
              return client


          scim = APIRouter(dependencies=[Depends(authenticate)])

``scim`` is the blueprint of the SCIM routes with Flask, and their router with FastAPI.

Announce the schemes
--------------------

List the authentication schemes in the
:attr:`~scim2_models.ServiceProviderConfig.authentication_schemes` of the provider. The
``/ServiceProviderConfig`` endpoint publishes them, and each 401 response carries a
``WWW-Authenticate`` header with one challenge per Bearer or Basic scheme
(:rfc:`RFC 7644 §2 <7644#section-2>`):

.. doctest::

    >>> from scim2_models import AuthenticationScheme
    >>> from scim2_models import UnauthorizedException
    >>> from scim2_server.service import ScimService
    >>> from scim2_server.utils import load_default_provider

    >>> provider = load_default_provider()
    >>> provider.config.authentication_schemes = [
    ...     AuthenticationScheme(
    ...         type=AuthenticationScheme.Type.oauthbearertoken,
    ...         name="OAuth Bearer Token",
    ...         description="Authentication with an OAuth 2.0 bearer token",
    ...     )
    ... ]
    >>> service = ScimService(provider)
    >>> service.error_response(UnauthorizedException()).headers["WWW-Authenticate"]
    'Bearer realm="SCIM"'

To announce another scheme, or to add parameters to the challenge, override
:meth:`~scim2_server.service.ScimService.www_authenticate`. For instance, the
``resource_metadata`` parameter of :rfc:`RFC 9728 §5.1 <9728#section-5.1>` tells the clients
where to discover the authorization server:

.. doctest::

    >>> class ProtectedResourceService(ScimService):
    ...     def www_authenticate(self, exception):
    ...         metadata = "https://scim.example/.well-known/oauth-protected-resource/scim/v2"
    ...         return f'Bearer resource_metadata="{metadata}"'

    >>> service = ProtectedResourceService(provider)
    >>> service.error_response(UnauthorizedException()).headers["WWW-Authenticate"]
    'Bearer resource_metadata="https://scim.example/.well-known/oauth-protected-resource/scim/v2"'

The application serves the metadata document itself, outside of the SCIM endpoints.

Restrict the access to some resources
-------------------------------------

A check before the handler only sees the method and the URL. A rule that depends on the
resources belongs to the storage, such as a client that only manages the users of its own
organization (:rfc:`RFC 7644 §2 <7644#section-2>`). Build the handler for each request, with a
storage bound to the client. A handler holds no state, so building one costs nothing.
``scim_request`` is the function of :doc:`integrate-a-web-framework`:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          @scim.route("/<path:path>", methods=METHODS)
          def serve(path):
              storage = OrganizationStorage(db.session, g.client.organization)
              handler = ScimHandler(service, storage)
              return to_response(handler.handle(scim_request()))

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          @scim.api_route("/{path:path}", methods=METHODS)
          async def serve(
              request: Request,
              client=Depends(authenticate),
              session=Depends(get_session),
          ):
              storage = OrganizationStorage(session, client.organization)
              handler = AsyncScimHandler(service, storage)
              return to_response(await handler.handle(await scim_request(request)))

The storage only reads and writes the resources of the organization. A resource of another
organization raises :class:`~scim2_models.NotFoundException`, so that the client does not learn
it exists:

- a search only counts the resources the client may see in ``totalResults``;
- in a bulk request, a refused operation gets its own error, and the other operations run.

:doc:`serve-the-me-endpoint` uses the authenticated client to serve ``/Me``.
