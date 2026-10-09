Integrate a web framework
=========================

Use this guide to serve SCIM from a web application built with a framework, such as Flask,
Django or FastAPI. It assumes a storage, such as the SQLite storage of :doc:`write-a-storage`.
It does not cover authentication: :doc:`authenticate-the-clients` does.

The integration turns the request of the framework into a
:class:`~scim2_server.requests.ScimRequest`. scim2-server does everything else: it routes the
request, validates it, calls the storage, and returns a
:class:`~scim2_server.responses.ScimResponse`. The integration turns it into a response of the
framework.

Build the handler
-----------------

The views of the framework call a handler. Build it once, when the application starts. An
asynchronous framework, such as FastAPI, uses an :class:`~scim2_server.handler.AsyncScimHandler`
over an :class:`~scim2_server.storage.AsyncScimStorage`:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. doctest::

          >>> import os
          >>> from scim2_server.handler import ScimHandler
          >>> from scim2_server.memory import InMemoryStorage
          >>> from scim2_server.service import ScimService
          >>> from scim2_server.utils import load_default_provider

          >>> service = ScimService(
          ...     load_default_provider(), secret=os.environ["SCIM_SECRET"]
          ... )
          >>> handler = ScimHandler(service, InMemoryStorage())

   .. tab-item:: Async
      :sync: async

      .. doctest::

          >>> import os
          >>> from scim2_server.handler import AsyncScimHandler
          >>> from scim2_server.memory import AsyncInMemoryStorage
          >>> from scim2_server.service import ScimService
          >>> from scim2_server.utils import load_default_provider

          >>> service = ScimService(
          ...     load_default_provider(), secret=os.environ["SCIM_SECRET"]
          ... )
          >>> handler = AsyncScimHandler(service, AsyncInMemoryStorage())

The handler needs two objects:

- the :class:`~scim2_server.service.ScimService` applies the SCIM rules to the resources of a
  :class:`~scim2_models.ScimProvider`. This provider describes the users and the groups of
  :rfc:`RFC 7643 <7643>`. The secret protects the cursors of :doc:`page-with-cursors`. Read it from the
  configuration of the deployment. Keep a reference to the service, because the error handler of
  `Return the response`_ uses it.
- the storage reads and writes the resources. Replace the in-memory storage with the storage of
  the application.

Read the request
----------------

The handler serves generic SCIM requests, independent of any framework. Turn each request of the
framework into a :class:`~scim2_server.requests.ScimRequest`, with these values:

- ``method``: the HTTP method;
- ``base_url``: the root URL of the SCIM endpoints, as the client sees it, such as
  ``https://example.com/scim/v2``. The handler builds the ``meta.location`` of the resources
  from it;
- ``path``: the path of the request, relative to ``base_url``, such as ``/Users/2819c223``;
- ``query``: the query parameters, as a mapping;
- ``headers``: the headers, as a mapping or as pairs. The handler reads :mdn:`Content-Type`,
  :mdn:`If-Match` and :mdn:`If-None-Match`;
- ``body``: the raw body, as :class:`bytes`;
- ``subject``: the authenticated client, if any. :doc:`authenticate-the-clients` sets it.

The following sketch builds the request with Flask and FastAPI, for SCIM endpoints served under
``/scim/v2``:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          def scim_request():
              return ScimRequest(
                  request.method,
                  request.url_root + "scim/v2",
                  request.path.removeprefix("/scim/v2"),
                  request.args,
                  request.headers,
                  request.get_data(),
              )

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          async def scim_request(request: Request):
              return ScimRequest(
                  request.method,
                  str(request.base_url) + "scim/v2",
                  request.url.path.removeprefix("/scim/v2"),
                  request.query_params,
                  request.headers.items(),
                  await request.body(),
              )

The sketch reads the whole body. To stop reading a large body early, read at most one byte more
than :meth:`~scim2_server.service.ScimService.max_body_size`, and pass this body to the handler.
The service answers: a 413 for a bulk request larger than ``maxPayloadSize``, or an error that
comes first, such as a 501 when the service does not support bulk.

Do not refuse a large body with the framework, such as with the
:data:`~flask:MAX_CONTENT_LENGTH` setting of Flask. The framework would answer 413 before the
service, without a SCIM error.

:meth:`~scim2_server.service.ScimService.max_body_size` only limits the bulk requests
(:rfc:`RFC 7644 §3.7.4 <7644#section-3.7.4>`). To protect the memory of the server from the other
requests, limit the size of the bodies in the reverse proxy, above ``maxPayloadSize``.

Route the requests
------------------

The framework chooses the view that serves a request. The SCIM requests must reach the handler,
by one of two ways: a single route for every request, or one route per SCIM operation.

:meth:`~scim2_server.handler.ScimHandler.handle` serves any SCIM request. It raises a
:class:`~scim2_models.NotFoundException` for an unknown path, and a
:class:`~scim2_server.errors.MethodNotAllowedException` for a method that the endpoint does not
support. Route every request under
the SCIM root to it:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


          @scim.route("/", defaults={"path": ""}, methods=METHODS)
          @scim.route("/<path:path>", methods=METHODS)
          def serve(path):
              return to_response(handler.handle(scim_request()))

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          @scim.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
          async def serve(request: Request):
              return to_response(await handler.handle(await scim_request(request)))

An integration can also keep the routing of the framework, for the OpenAPI schema of FastAPI or
for a decorator on a single route. It then registers one route per SCIM operation. Each view calls
the handler method of its operation, such as :meth:`~scim2_server.handler.ScimHandler.query` for a
read. A last route sends the other requests to :meth:`~scim2_server.handler.ScimHandler.handle`,
and the handler raises a SCIM error for them:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          @scim.get("/<endpoint>/<resource_id>")
          def query(endpoint, resource_id):
              return to_response(handler.query(scim_request()))


          @scim.post("/<endpoint>")
          def create(endpoint):
              return to_response(handler.create(scim_request()))


          # One view per SCIM operation, then:


          @scim.route("/<path:path>", methods=METHODS)
          def other(path):
              return to_response(handler.handle(scim_request()))

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          @scim.get("/{endpoint}/{resource_id}")
          async def query(request: Request):
              return to_response(await handler.query(await scim_request(request)))


          @scim.post("/{endpoint}")
          async def create(request: Request):
              return to_response(await handler.create(await scim_request(request)))


          # One view per SCIM operation, then:


          @scim.api_route("/{path:path}", methods=METHODS)
          async def other(request: Request):
              return to_response(await handler.handle(await scim_request(request)))

:data:`~scim2_server.routing.ROUTES` lists the routes of the SCIM operations. Each
:class:`~scim2_server.routing.Route` has a method, a pattern such as ``/{endpoint}/{resource_id}``, and an operation. An integration can
register its views from it. Register the routes in the order of the list: the routes with a fixed
path, such as ``/Schemas``, come first.

The handler reads the path itself: the routes of the framework only select the view. A handler
method refuses a request of another operation. Such a refusal reveals a route registered in the
wrong order.

Return the response
-------------------

The handler returns generic SCIM responses, independent of any framework. Turn each
:class:`~scim2_server.responses.ScimResponse` into a response of the framework, with these values:

- the status from :attr:`~scim2_server.responses.ScimResponse.status`;
- the headers from :attr:`~scim2_server.responses.ScimResponse.headers`, which hold the
  ``Content-Type``, and the :mdn:`ETag`, :mdn:`Location` and :mdn:`Content-Location` when the
  response has them;
- the body from :attr:`~scim2_server.responses.ScimResponse.body`, serialized as JSON, or no body
  when it is :data:`None`.

The handler does not return a response when a request fails: it raises a
:class:`~scim2_models.SCIMException`. Catch it in an error handler of the framework, and turn it
into a response with :meth:`~scim2_server.service.ScimService.error_response`. Any other
exception is a bug, and the framework answers it with a 500.

The following sketch catches the SCIM exceptions with Flask and FastAPI:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          @app.errorhandler(SCIMException)
          def handle_scim_exception(exception):
              return to_response(service.error_response(exception))

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          @app.exception_handler(SCIMException)
          async def handle_scim_exception(request: Request, exception: SCIMException):
              return to_response(service.error_response(exception))

Serve SCIM without a framework
------------------------------

The following applications put the previous sections together, with no framework: as a WSGI
application with a synchronous handler, and as an ASGI application with an asynchronous one:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/wsgi_integration.py
         :language: python

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/asgi_integration.py
         :language: python

scim2-server provides the complete version of these two applications:
:class:`~scim2_server.applications.wsgi.WSGIApplication` and :class:`~scim2_server.applications.asgi.ASGIApplication`. Their
hooks observe or change each request, without a framework.
