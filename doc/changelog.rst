Changelog
=========

[0.6.2] - 2026-10-06
--------------------

Added
^^^^^
- A storage can return references to other resources relative to the SCIM root, such as
  ``Users/2819c223`` in ``members.$ref``. The server returns them as the URLs of the resources,
  with :meth:`~scim2_server.service.ScimService.reference_location`.

[0.6.1] - 2026-10-05
--------------------

Changed
^^^^^^^
- :meth:`~scim2_server.service.ScimService.bulk_outcome` takes the base URL.
- :meth:`~scim2_server.service.ScimService.locate_bulk_operation` takes the resource type, and
  :meth:`~scim2_server.service.ScimService.route_bulk_operation` returns it.
  ``ScimService.check_bulk_target`` is removed.
- :meth:`~scim2_server.service.ScimService.max_body_size` returns 0 instead of raising an
  exception, for a request that fails whatever its body, such as on an unknown path.

Fixed
^^^^^
- Every failed bulk operation has a location, except a failed POST, which has none. scim2-client
  no longer rejects these bulk responses.
- A bulk operation fails with the status of the same request on its own, such as 404 for an
  unknown endpoint or 405 for ``DELETE /Users``, instead of 400.
- A request body without :mdn:`Content-Type` is read as JSON, instead of answering 415.
- Each response carrying a resource has a :mdn:`Content-Location` header with ``meta.location``.
- A provider without ``config`` announces PATCH, and no other capability. It announced every
  capability, even those its storage does not implement. A filter or a sort now answers 501,
  until the ``config`` announces it.

[0.6.0] - 2026-10-05
--------------------

Added
^^^^^
- :meth:`~scim2_server.service.ScimService.authorize` accepts or refuses each operation of a
  client, bulk operations included.

Changed
^^^^^^^
- :meth:`~scim2_server.handler.ScimHandler.run_bulk_step` takes the bulk request instead of its
  base URL.
- The storage contract accepts a version that only changes with the content of the resource.

[0.5.0] - 2026-10-05
--------------------

scim2-server becomes a library to build SCIM servers upon, and no longer depends on Werkzeug.

Added
^^^^^
- A core independent of any web framework. :class:`~scim2_server.handler.ScimHandler` serves a
  :class:`~scim2_server.requests.ScimRequest` and returns a
  :class:`~scim2_server.responses.ScimResponse`. :class:`~scim2_server.service.ScimService`
  holds the SCIM rules, and can be subclassed to change one of them, such as the URL of the
  resources. :class:`~scim2_server.handler.AsyncScimHandler` serves the same requests in an
  asynchronous application.
- :class:`~scim2_server.storage.ScimStorage` and :class:`~scim2_server.storage.AsyncScimStorage`,
  the interface to keep the resources in any database, with
  :class:`~scim2_server.memory.InMemoryStorage` and
  :class:`~scim2_server.memory.AsyncInMemoryStorage`.
  :class:`~scim2_server.testing.ScimStorageContract` and
  :class:`~scim2_server.testing.AsyncScimStorageContract` check that a storage follows the
  interface, with the ``testing`` extra.
- :class:`~scim2_server.applications.wsgi.WSGIApplication` and
  :class:`~scim2_server.applications.asgi.ASGIApplication` serve a storage over HTTP, with no
  other dependency. Their hooks take a :class:`~scim2_server.requests.ScimRequest` and return a
  :class:`~scim2_server.responses.ScimResponse`: ``dispatch_request``,
  :meth:`~scim2_server.applications.base.BaseApplication.check_auth`,
  :meth:`~scim2_server.applications.base.BaseApplication.get_subject`,
  :meth:`~scim2_server.applications.base.BaseApplication.handle_exception` and
  :meth:`~scim2_server.applications.base.BaseApplication.finalize_response`.
- :class:`~scim2_server.applications.wsgi.ForwardedHeaders`, a WSGI middleware that builds the
  URLs of the resources from the ``X-Forwarded-*`` headers of a reverse proxy.
- :data:`~scim2_server.routing.ROUTES` lists the routes of RFC 7644 §3.2, for the integrations
  that register the routes of their framework.
- ``/Me`` serves the resource of the authenticated client, once
  :meth:`~scim2_server.service.ScimService.me_target` is overridden.
- A 401 response carries a ``WWW-Authenticate`` header built from the authentication schemes of
  the service provider configuration.
- A request body that is not JSON answers 415.
- This documentation.

Changed
^^^^^^^
- Werkzeug is no longer a dependency. The ``scim2-server`` command serves its requests with the
  WSGI server of the standard library. ``--debug`` logs the WSGI environment of each request,
  without the debugger and the reloader of Werkzeug.
- ``scim2_server.provider.SCIMApplication`` becomes
  :class:`scim2_server.applications.wsgi.WSGIApplication`, and takes a storage instead of a
  backend. ``scim2_server.tenants.TenantDispatcher`` becomes
  :class:`scim2_server.applications.wsgi.TenantDispatcher`. Its factory now decides which
  tenants exist, and returns :data:`None` for an unknown tenant: the ``tenants`` and
  ``dynamic`` parameters are removed.
- A concurrent write of a resource between its read and its write answers 412, instead of
  overwriting the other write. Two writes within the same microsecond get distinct versions.
- A method that an endpoint does not support answers 405 with its ``Allow`` header. ``HEAD`` is
  no longer accepted.
- The ``meta.location`` of the discovery resources starts with ``/v2``, like the location of the
  other resources, even for a request without this prefix.
- An exception other than a :class:`~scim2_models.SCIMException`, such as a Pydantic
  ``ValidationError`` raised by a storage, answers 500.

Removed
^^^^^^^
- ``scim2_server.backend.Backend`` and ``InMemoryBackend``. Use a storage instead. A storage
  no longer fills ``meta.location``: the server does.
- ``SCIMApplication.register_bearer_token``. The ``--bearer-token`` option of the
  ``scim2-server`` command still accepts static bearer tokens. In Python, override
  :meth:`~scim2_server.applications.base.BaseApplication.check_auth`.
- ``SCIMApplication.error_from``, ``SCIMApplication.make_error`` and the ``call_*`` methods.

Fixed
^^^^^
- A PATCH operation that misses a member RFC 7644 §3.5.2 requires, such as a ``remove`` without
  ``path`` or an ``add`` without ``value``, answers 400 and leaves the resource unchanged.
- A bulk request with a long chain of ``bulkId`` references no longer exhausts the Python stack.
- The errors of the clients, such as a 404, are logged at the INFO level without traceback.
  Only the unexpected errors keep their traceback.

[0.4.0] - 2026-10-01
--------------------

Added
^^^^^
- Multi-tenancy: the ``--tenant`` option of the ``scim2-server`` command serves one set of
  resources per URL prefix, such as ``/a/v2/Users``. With ``--dynamic-tenants``, the first
  request to an unknown tenant creates it.

Fixed
^^^^^
- The location of the resources keeps the prefix the application is mounted under.

[0.3.3] - 2026-10-01
--------------------

Fixed
^^^^^
- An unsupported method on a discovery endpoint answers 405, and every 405 response lists the
  supported methods in its ``Allow`` header.

[0.3.2] - 2026-10-01
--------------------

Added
^^^^^
- The ``scim2-server`` command serves several requests at once.
- The package ships its type hints.

Fixed
^^^^^
- A bulk request larger than ``maxPayloadSize`` is refused before it is read whole.

[0.3.1] - 2026-09-30
--------------------

Fixed
^^^^^
- A PUT that changes nothing keeps the ``ETag`` of the resource.

[0.3.0] - 2026-09-28
--------------------

Python 3.10 is no longer supported.

Added
^^^^^
- Bulk requests, with ``bulkId`` references between operations and ``failOnErrors``.
- The service is described with a :class:`~scim2_models.ScimProvider`. The ``scim2-server``
  command takes a ``--service-provider-config`` file, and serves the configuration it describes.
- Features that the configuration does not support, such as PATCH, sorting or filtering, answer
  501. Searches return at most ``filter.maxResults`` resources. Resources carry versions only
  when the configuration supports ETags.
- The payloads are read under the :class:`~scim2_models.ScimPolicy` of the provider. By default,
  an attribute that no schema declares answers 400.

Changed
^^^^^^^
- ``SCIMProvider`` is renamed ``SCIMApplication``, to avoid any confusion with the
  :class:`~scim2_models.ScimProvider` of scim2-models.
- The backend takes the provider, and only stores the resources. Its methods take a
  :class:`~scim2_models.ResourceType` instead of its id. Its methods to register and read schemas
  and resource types are removed.
- PATCH requests and filters are applied by scim2-models. The PATCH operators and the filter
  evaluation of scim2-server are removed.

Fixed
^^^^^
- A PATCH that changes nothing keeps the ``ETag`` and ``lastModified`` of the resource.
- Conditional headers are evaluated in the order of RFC 7232.
- Uniqueness is checked among the resources that share a schema, whatever their resource type.
- ``totalResults`` counts every matching resource, and ``itemsPerPage`` counts the returned ones.
- Validation errors carry a ``scimType``.
- An internal error no longer discloses its traceback to the client.
- A search request body that is not a JSON object answers 400.
- The default schemas follow the RFC errata: the password is case-exact, and the addresses of a
  user have a ``primary`` attribute. The enterprise user extension is optional in the default
  resource types.

[0.2.0] - 2026-09-25
--------------------

Added
^^^^^
- A container image is published on the GitHub container registry for each release.
- The ``--debug`` option of the ``scim2-server`` command.

[0.1.9] - 2026-03-27
--------------------

Fixed
^^^^^
- PATCH requests on multi-valued attributes.

[0.1.8] - 2026-01-25
--------------------

Python 3.14 is supported.

Fixed
^^^^^
- A PATCH on the root of an extension no longer answers ``invalidPath``.

[0.1.7] - 2025-07-25
--------------------

Fixed
^^^^^
- A PUT that adds an extension to a resource without one no longer fails.

[0.1.6] - 2025-07-23
--------------------

Fixed
^^^^^
- Compatibility with scim2-models 0.4.

[0.1.5] - 2025-03-28
--------------------

Fixed
^^^^^
- A Pydantic warning.

[0.1.4] - 2025-01-27
--------------------

No change for the users.

[0.1.3] - 2025-01-21
--------------------

No change for the users.

[0.1.2] - 2024-11-07
--------------------

Python 3.10 and 3.13 are supported.

Fixed
^^^^^
- The location of a resource created with POST or replaced with PUT.

[0.1.1] - 2024-09-22
--------------------

Added
^^^^^
- The ``/v2`` prefix of the endpoints is optional.

Fixed
^^^^^
- ``meta.resourceType`` holds the name of the resource type, not its id.

[0.1.0] - 2024-08-30
--------------------

Added
^^^^^
- Initial release: an in-memory SCIM server, with the discovery endpoints, the creation, read,
  replacement, PATCH and deletion of resources, searches with filters and sorting, ETags and
  uniqueness constraints.
