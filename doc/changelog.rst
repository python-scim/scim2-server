Changelog
=========

[Unreleased]
------------

Added
^^^^^
- A core independent of any web framework. :meth:`ScimHandler.handle
  <scim2_server.handler.ScimHandler.handle>` serves a :class:`~scim2_server.requests.ScimRequest`
  and returns a :class:`~scim2_server.responses.ScimResponse`.
  :class:`~scim2_server.service.ScimService` holds the SCIM rules, and
  :class:`~scim2_server.handler.ScimHandler` and :class:`~scim2_server.handler.AsyncScimHandler`
  serve the operations over a storage.
- :data:`~scim2_server.routing.ROUTES` lists the routes of RFC 7644 §3.2, for the integrations
  that register native routes.
- ``/Me`` serves the resource of the authenticated subject, once
  :meth:`~scim2_server.service.ScimService.me_target` is overridden. A POST on ``/Me`` creates
  a resource of the type of :meth:`~scim2_server.service.ScimService.me_creation_type`.
- :class:`~scim2_server.storage.ScimStorage` and :class:`~scim2_server.storage.AsyncScimStorage`,
  the interface of the storages, with :class:`~scim2_server.memory.InMemoryStorage` and
  :class:`~scim2_server.memory.AsyncInMemoryStorage`.
- :class:`~scim2_server.testing.ScimStorageContract` and
  :class:`~scim2_server.testing.AsyncScimStorageContract` check that a storage follows the
  interface, with the ``testing`` extra.
- :class:`~scim2_server.asgi.ASGIApplication`, an ASGI application over an asynchronous
  storage, with the same hooks as :class:`~scim2_server.wsgi.WSGIApplication`.
- The WSGI and ASGI applications can be subclassed to observe or change each request, with
  ``dispatch_request``,
  :meth:`~scim2_server.application.BaseApplication.handle_exception`,
  :meth:`~scim2_server.application.BaseApplication.finalize_response` and
  :meth:`~scim2_server.application.BaseApplication.check_auth`. The hooks take a
  :class:`~scim2_server.requests.ScimRequest` and return a
  :class:`~scim2_server.responses.ScimResponse`.
- The WSGI and ASGI applications take an optional ``service``, to change a step of the service
  such as the URL of the resources.
- A request body that is not JSON answers 415.
- A 401 response carries a ``WWW-Authenticate`` header built from the authentication schemes of
  the service provider configuration. Override
  :meth:`~scim2_server.service.ScimService.www_authenticate` to change it.
- This documentation.

Changed
^^^^^^^
- Werkzeug is no longer a dependency. The ``scim2-server`` command serves its requests with the
  WSGI server of the standard library. ``--debug`` only logs the WSGI environment of the
  requests, without the debugger and the reloader of Werkzeug.
- ``scim2_server.provider.SCIMApplication`` becomes :class:`scim2_server.wsgi.WSGIApplication`,
  and takes a storage instead of a backend. ``scim2_server.tenants.TenantDispatcher`` becomes
  :class:`scim2_server.wsgi.TenantDispatcher`.
- A method that an endpoint does not support answers 405 with its ``Allow`` header, and ``HEAD``
  is no longer accepted.
- A concurrent write of a resource between its read and its write answers 412, instead of
  overwriting the other write.
- Two writes of a resource within the same microsecond get distinct versions.
- The ``meta.location`` of the discovery resources starts with ``/v2``, like the location of the
  other resources, even for a request without this prefix.
- The errors of the protocol are the exceptions of scim2-models, and their details change.
  The handlers only raise :class:`~scim2_models.SCIMException`: any other exception, such as a
  Pydantic ``ValidationError`` raised by a storage, answers 500.

Removed
^^^^^^^
- ``scim2_server.backend.Backend`` and ``scim2_server.backend.InMemoryBackend``, replaced by the
  storages. A storage no longer fills ``meta.location``.
- ``SCIMApplication.register_bearer_token``. The ``--bearer-token`` option of the
  ``scim2-server`` command still accepts static bearer tokens. In Python, override
  :meth:`~scim2_server.application.BaseApplication.check_auth` instead.
- ``SCIMApplication.error_from``, ``SCIMApplication.make_error`` and the ``call_*`` methods.

Fixed
^^^^^
- PATCH requests are validated in the context of a PATCH request.
- A bulk request with a long chain of ``bulkId`` references no longer exhausts the Python stack.
- The WSGI application logs the errors of the clients, such as a 404, at the INFO level and
  without traceback. Only the unexpected errors keep their traceback.
