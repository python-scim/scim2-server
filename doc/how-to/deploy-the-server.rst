Deploy the server
=================

Use this guide to serve the WSGI or the ASGI application of scim2-server with a production
server, such as `Gunicorn <https://gunicorn.org>`_ or `Uvicorn <https://uvicorn.dev>`_. It
assumes a storage, such as the one of :doc:`write-a-storage`.

Build the application
---------------------

Build the application in a module of the project, such as ``myapp/wsgi.py`` or ``myapp/asgi.py``.
The server imports it from there:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          import sqlite3

          from scim2_server.applications.wsgi import WSGIApplication
          from scim2_server.utils import load_default_provider

          from myapp.scim import SQLiteStorage

          provider = load_default_provider()
          storage = SQLiteStorage(sqlite3.connect("scim.sqlite"), provider)
          app = WSGIApplication(storage, provider)

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          from scim2_server.applications.asgi import ASGIApplication
          from scim2_server.utils import load_default_provider

          from myapp.scim import AsyncSQLiteStorage

          provider = load_default_provider()
          app = ASGIApplication(AsyncSQLiteStorage("scim.sqlite", provider), provider)

Serve the application
---------------------

Install the server, and start it with the module and the name of the application:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: console

          $ pip install gunicorn
          $ gunicorn --bind 0.0.0.0:8000 --workers 4 myapp.wsgi:app

   .. tab-item:: Async
      :sync: async

      .. code-block:: console

          $ pip install uvicorn
          $ uvicorn --host 0.0.0.0 --port 8000 --workers 4 myapp.asgi:app

The SCIM endpoints are served under ``/v2``, such as ``http://<HOST>:8000/v2/Users``.

Each worker is a process, with its own copy of the application. Use a storage over a shared
database: the workers then serve the same resources. With
:class:`~scim2_server.memory.InMemoryStorage`, each worker keeps its own resources, so run a
single worker.

Serve the application behind a reverse proxy
--------------------------------------------

Behind a reverse proxy, the server receives the requests from the proxy, not from the client. The
locations of the resources must still use the URL that the client used: its scheme, its host and
its path prefix. The proxy sends them in the ``X-Forwarded-*`` headers.

With a WSGI server, wrap the application in :class:`~scim2_server.applications.wsgi.ForwardedHeaders`. It reads
:mdn:`X-Forwarded-Proto`, :mdn:`X-Forwarded-Host`, ``X-Forwarded-Port``, ``X-Forwarded-Prefix`` and
:mdn:`X-Forwarded-For`. With Uvicorn, the server reads ``X-Forwarded-Proto`` and ``X-Forwarded-For``
itself. The proxy passes the ``Host`` header of the client, and ``--root-path`` gives the path
prefix:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          from scim2_server.applications.wsgi import ForwardedHeaders

          app = ForwardedHeaders(WSGIApplication(storage, provider))

   .. tab-item:: Async
      :sync: async

      .. code-block:: console

          $ uvicorn --forwarded-allow-ips 10.0.0.1 --root-path /scim myapp.asgi:app

Only the proxy may send these headers: a client could otherwise choose the URLs of the resources.
Uvicorn only reads them from the addresses of ``--forwarded-allow-ips``.
:class:`~scim2_server.applications.wsgi.ForwardedHeaders` reads them from any client, so only the proxy must
reach the server.

Publish the resources at other URLs
-----------------------------------

Some deployments cannot give the URL of the client in the forwarded headers: the proxy cannot be
configured, or an API gateway publishes the resources under another path or another domain. Set
the URL of the resources in the service then.

The service builds every URL of a resource in one method:
:meth:`~scim2_server.service.ScimService.resource_location`. The ``meta.location`` attribute,
the :mdn:`Location` header of a creation, and the ``location`` of the bulk results all come from
it. Subclass :class:`~scim2_server.service.ScimService`, and override the method. It receives the
root URL of the SCIM endpoints, the resource type, and the identifier of the resource:

.. doctest::

    >>> from scim2_server.service import ScimService

    >>> class PublicService(ScimService):
    ...     def resource_location(self, base_url, resource_type, resource_id):
    ...         return f"https://scim.example{resource_type.endpoint}/{resource_id}"

Pass the service to the application, built upon the same provider:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. doctest::

          >>> from scim2_server.applications.wsgi import WSGIApplication
          >>> from scim2_server.memory import InMemoryStorage
          >>> from scim2_server.utils import load_default_provider

          >>> provider = load_default_provider()
          >>> app = WSGIApplication(
          ...     InMemoryStorage(), provider, service=PublicService(provider)
          ... )

   .. tab-item:: Async
      :sync: async

      .. doctest::

          >>> from scim2_server.applications.asgi import ASGIApplication
          >>> from scim2_server.memory import AsyncInMemoryStorage

          >>> async_app = ASGIApplication(
          ...     AsyncInMemoryStorage(), provider, service=PublicService(provider)
          ... )

A created resource then carries the URL of the service:

.. doctest::

    >>> from werkzeug.test import Client

    >>> response = Client(app).post(
    ...     "/v2/Users", json={"userName": "bjensen"}, content_type="application/scim+json"
    ... )
    >>> response.headers["Location"] == f"https://scim.example/Users/{response.json['id']}"
    True
    >>> response.json["meta"]["location"] == response.headers["Location"]
    True

The routes of the server do not change: they stay those of
:rfc:`RFC 7644 §3.2 <7644#section-3.2>`.
