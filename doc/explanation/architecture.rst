The layers of the server
========================

This page explains why scim2-server is split into a service, a handler, a storage and an
adapter, and what each one does. It does not describe the API: the :doc:`../reference/index` does.

Four layers
-----------

A SCIM server does four kinds of work. scim2-server gives each one to a separate object:

.. list-table::
   :header-rows: 1

   * - Layer
     - Does
     - Does not
   * - :class:`~scim2_server.service.ScimService`
     - Routes and validates the requests, applies PUT and PATCH, evaluates the ETags, and builds
       the responses and the URLs of the resources.
     - No input or output, no web framework.
   * - :class:`~scim2_server.handler.ScimHandler` and
       :class:`~scim2_server.handler.AsyncScimHandler`
     - Run the steps of the service, and call the storage between them, one method per SCIM
       operation.
     - No SCIM rule of their own.
   * - :class:`~scim2_server.storage.ScimStorage` and
       :class:`~scim2_server.storage.AsyncScimStorage`
     - Read, search and write the resources, and fill their identifiers, dates and versions.
     - No rule of the SCIM protocol.
   * - The integration, such as :class:`~scim2_server.applications.wsgi.WSGIApplication`
     - Turns the requests of the framework into SCIM requests, authenticates the clients, and
       turns the SCIM responses into responses of the framework.
     - No SCIM rule of its own.

The SCIM rules live in one place. Every integration gets the same behavior: the same
validation, the same error responses, the same ETag comparison.

Why the service does no input or output
---------------------------------------

A method that calls the storage must choose between ``def`` and ``async def``. The service never
calls the storage, so its SCIM rules exist once, for the synchronous and the asynchronous
frameworks.

The service only has pure steps: read a request, apply a PATCH, build a response. The
handlers chain these steps with the calls to the storage. Only this chaining exists twice, in
:class:`~scim2_server.handler.ScimHandler` and :class:`~scim2_server.handler.AsyncScimHandler`,
and it holds no SCIM rule.

Why the handler receives a service
----------------------------------

The handler holds a service. An application that changes one step, such as the URL of the
resources, subclasses :class:`~scim2_server.service.ScimService` once, and passes it to either
handler.

Why the service routes the requests
-----------------------------------

The integration passes the whole request to the handler, as a
:class:`~scim2_server.requests.ScimRequest`, and the service finds its operation from the method
and the path. The routes of :rfc:`RFC 7644 §3.2 <7644#section-3.2>` have rules that a framework
does not know: the names that no resource type can take, such as ``Schemas`` or ``Bulk``, the
searches at the root, the 405 responses with their :mdn:`Allow` header, and ``/Me``. The service
applies these rules once, for every integration.

An integration that keeps the routing of its framework, for its OpenAPI schema or for a
decorator on a route, still passes the request to the handler. The routes of the framework only
select the view, and the service interprets the path. A route registered in the wrong order
calls a handler method with a request of another operation. The method refuses it.

Why the service reads the raw headers
-------------------------------------

The integration passes the raw headers to the handler, and the raw body. The service reads them
itself. Each web framework parses these headers in its own way, and a slight difference changes
the response. For instance, per :rfc:`RFC 9110 §13.1.1 <9110#section-13.1.1>`, :mdn:`If-Match`
uses the strong comparison, which never matches the weak ETags of
:rfc:`RFC 7644 §3.14 <7644#section-3.14>`. The service compares both headers weakly, whatever the
framework.

Where scim2-models stops
------------------------

scim2-models validates and serializes the payloads. It has no notion of a storage, of a URL, or
of a server. scim2-server builds the server on top of it: the parts that need to know where the
resources live and how they are served.

The :doc:`storage` page explains the rules of the storage, and :doc:`bulk` how the handlers run
a bulk request.
