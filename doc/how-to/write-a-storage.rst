Write a storage
===============

Use this guide to serve the resources an application already keeps, such as the rows of a
database table. It assumes the :doc:`../tutorial`. It does not cover the mapping between SCIM
attributes and database columns.

A storage is a subclass of :class:`~scim2_server.storage.ScimStorage`, or of
:class:`~scim2_server.storage.AsyncScimStorage` for an asynchronous server. The methods of an
asynchronous storage are coroutines, and follow the same rules. The server validates the
requests and applies them before it calls the storage: the storage only reads and writes
resources.

Create the table
----------------

This guide keeps the resources in a SQLite database. The synchronous storage uses :mod:`sqlite3`,
and the asynchronous one uses `aiosqlite <https://aiosqlite.omnilib.dev>`_. A row holds a resource:
its resource type, its identifier, the ``userName`` of a user, the other attributes as JSON, its
dates and its version. A ``UNIQUE`` constraint refuses two users with the same ``userName``,
whatever its case:

.. literalinclude:: ../_examples/sqlite_storage.py
   :language: python
   :start-at: TABLE =
   :end-before: class SQLiteStorage

Keep the resources
------------------

Subclass :class:`~scim2_server.storage.ScimStorage` or
:class:`~scim2_server.storage.AsyncScimStorage`. The storage receives the
:class:`~scim2_models.ScimProvider` of the server. The provider gives the model of each resource
type.
The synchronous storage receives an open connection. The asynchronous one opens its connection
on its first call, from the event loop that serves the requests:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/sqlite_storage.py
         :language: python
         :start-at: class SQLiteStorage
         :end-before: def row_to_resource

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/async_sqlite_storage.py
         :language: python
         :start-at: class AsyncSQLiteStorage
         :end-before: def row_to_resource

Turn a row into a resource with the model of its resource type. Fill its ``id`` and its
``meta`` attribute: ``resourceType``, ``created``, ``lastModified`` and ``version``. Any value
works as a version, as long as it changes on every write. The example counts the writes, in the
format of a weak ETag. Leave ``meta.location`` empty: the server builds it from its own URL.

.. literalinclude:: ../_examples/sqlite_storage.py
   :language: python
   :pyobject: SQLiteStorage.row_to_resource
   :dedent: 4

The complete storage passes the checks of :doc:`check-a-storage`. Each of the following
sections adds one of its methods.

Read a resource
---------------

:meth:`~scim2_server.storage.ScimStorage.get` returns a resource, from its resource type and its
identifier. Raise :class:`~scim2_models.NotFoundException` when it does not exist:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/sqlite_storage.py
         :language: python
         :pyobject: SQLiteStorage.get
         :dedent: 4

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/async_sqlite_storage.py
         :language: python
         :pyobject: AsyncSQLiteStorage.get
         :dedent: 4

Create a resource
-----------------

:meth:`~scim2_server.storage.ScimStorage.create` stores a new resource. Choose its identifier,
and start its version at 1:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/sqlite_storage.py
         :language: python
         :pyobject: SQLiteStorage.create
         :dedent: 4

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/async_sqlite_storage.py
         :language: python
         :pyobject: AsyncSQLiteStorage.create
         :dedent: 4

Refuse a duplicate value
------------------------

The ``userName`` of a user is unique, whatever its case. On a creation and on an update, raise
:class:`~scim2_models.UniquenessException` when another user already has the value. The ``UNIQUE`` constraint of the table refuses such a write, even when two
requests arrive at the same time. The example commits each write, and turns the refusal into the
exception:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/sqlite_storage.py
         :language: python
         :pyobject: SQLiteStorage.writing
         :dedent: 4

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/async_sqlite_storage.py
         :language: python
         :pyobject: AsyncSQLiteStorage.writing
         :dedent: 4

Update a resource
-----------------

:meth:`~scim2_server.storage.ScimStorage.update` receives the whole new state of a resource, for
a PUT request and for a PATCH request. The server has already applied the request. Keep
``meta.created``, and change ``meta.lastModified`` and ``meta.version``.

When ``expected_version`` is given, compare it with the stored version, and raise
:class:`~scim2_models.PreconditionFailedException` when they differ. Another client changed the
resource since the server read it.

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/sqlite_storage.py
         :language: python
         :pyobject: SQLiteStorage.update
         :dedent: 4

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/async_sqlite_storage.py
         :language: python
         :pyobject: AsyncSQLiteStorage.update
         :dedent: 4

Delete a resource
-----------------

:meth:`~scim2_server.storage.ScimStorage.delete` checks ``expected_version`` the same way, and
removes the resource:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/sqlite_storage.py
         :language: python
         :pyobject: SQLiteStorage.delete
         :dedent: 4

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/async_sqlite_storage.py
         :language: python
         :pyobject: AsyncSQLiteStorage.delete
         :dedent: 4

Search the resources
--------------------

:meth:`~scim2_server.storage.ScimStorage.search` receives the resource types to search, and a
validated :class:`~scim2_models.SearchRequest`. A search on an endpoint covers one resource type,
and a search at the root covers all of them. Filter, sort and page the resources, and return the
number of matching resources with the page:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/sqlite_storage.py
         :language: python
         :pyobject: SQLiteStorage.search
         :dedent: 4

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/async_sqlite_storage.py
         :language: python
         :pyobject: AsyncSQLiteStorage.search
         :dedent: 4

Count the matching resources before paging them: ``totalResults`` counts every matching
resource, and the page holds at most ``count`` of them.

Search in a database
--------------------

The search of the example reads every resource. A database storage translates the filter of the
:class:`~scim2_models.SearchRequest` into a query instead:

- :attr:`SearchRequest.filter <scim2_models.SearchRequest.filter>` holds the parsed filter, to
  translate into a ``WHERE`` clause;
- ``sort_by``, ``sort_order``, ``start_index`` and ``count`` give the order and the page;
- a filter the storage cannot translate raises :class:`~scim2_models.InvalidFilterException`.
  The server then answers 400. Do not filter in Python after the query has paged the results:
  ``totalResults`` would be wrong.

The server already refuses a filter or a sort that its
:class:`~scim2_models.ServiceProviderConfig` does not announce. A storage that cannot search
several resource types at once raises :class:`~scim2_models.NotImplementedException` when it
receives more than one.

Enclose each operation in a transaction
---------------------------------------

The server calls :meth:`~scim2_server.storage.ScimStorage.operation` around each SCIM
operation, and around each operation of a bulk request. Return a context manager from it to
open a transaction or a savepoint. The default context does nothing.
:meth:`AsyncScimStorage.operation <scim2_server.storage.AsyncScimStorage.operation>` returns an
asynchronous context manager.

Serve the storage
-----------------

Pass the storage to :class:`~scim2_server.wsgi.WSGIApplication` or to
:class:`~scim2_server.asgi.ASGIApplication`, or to the handler of :doc:`integrate-a-web-framework`:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. doctest::

          >>> import sqlite3
          >>> from doc._examples.sqlite_storage import SQLiteStorage
          >>> from scim2_server.utils import load_default_provider
          >>> from scim2_server.wsgi import WSGIApplication
          >>> from werkzeug.test import Client

          >>> provider = load_default_provider()
          >>> storage = SQLiteStorage(sqlite3.connect(":memory:"), provider)
          >>> app = WSGIApplication(storage, provider)
          >>> response = Client(app).post(
          ...     "/v2/Users", json={"userName": "bjensen"}, content_type="application/scim+json"
          ... )
          >>> response.status_code
          201

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          from scim2_server.asgi import ASGIApplication
          from scim2_server.utils import load_default_provider

          from myapp.scim import AsyncSQLiteStorage

          provider = load_default_provider()
          async_storage = AsyncSQLiteStorage("scim.sqlite", provider)
          app = ASGIApplication(async_storage, provider)

Close the database when the application stops:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. doctest::

          >>> storage.connection.close()

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          await async_storage.close()

:doc:`../explanation/storage` explains why the storage receives the whole resource, and how the
versions protect concurrent writes.
