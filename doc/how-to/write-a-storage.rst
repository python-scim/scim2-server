Write a storage
===============

Use this guide to keep the SCIM resources in a database table of their own. It assumes a
:class:`~scim2_models.ScimProvider` that describes the service, such as the one of
:doc:`../overview`. To serve the data that an application already keeps in its own tables, follow
:doc:`serve-an-existing-data-model` after this guide.

Decide what to store
--------------------

A storage can keep the resources anywhere: a SQL database through a driver or an ORM, a document
database, an LDAP directory, or a remote API. The server only calls its methods. This guide keeps
the resources in a SQLite database. The synchronous storage uses :mod:`sqlite3`,
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

:class:`~scim2_server.storage.ScimStorage` declares the methods that the server calls to read and
write the resources: :meth:`~scim2_server.storage.ScimStorage.get`,
:meth:`~scim2_server.storage.ScimStorage.create`,
:meth:`~scim2_server.storage.ScimStorage.update`,
:meth:`~scim2_server.storage.ScimStorage.delete` and
:meth:`~scim2_server.storage.ScimStorage.search`. The server validates the requests and applies
them before it calls these methods: the storage only reads and writes resources.

Subclass :class:`~scim2_server.storage.ScimStorage`, or
:class:`~scim2_server.storage.AsyncScimStorage` for an asynchronous server. The methods of an
asynchronous storage are coroutines, and follow the same rules.

Pass the :class:`~scim2_models.ScimProvider` to the storage. Open the connection of an asynchronous
storage on its first call, from the event loop that serves the requests:

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

Turn a row into a resource with the model that the provider gives for its resource type. Fill its ``id``, and its
:class:`~scim2_models.Meta` with ``resourceType``, ``created``, ``lastModified`` and
``version``. The version is the :mdn:`ETag` of the resource, so it must be a quoted entity tag,
such as ``W/"3"`` (:rfc:`RFC 9110 §8.8.3 <9110#section-8.8.3>`). It must change on every write.
The example counts the writes. Leave ``meta.location`` empty: the server builds it from its own URL.

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

In the default user resource, the ``userName`` of a user is unique, whatever its case. On a creation and on an update, raise
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

When ``expected_version`` is given, write the resource only if its stored version is still
``expected_version``, in the same query. Another client may change the resource between two
queries. When no row changes, raise :class:`~scim2_models.NotFoundException` if the resource is
gone, and :class:`~scim2_models.PreconditionFailedException` otherwise. The version is an
``ETag`` such as ``W/"3"``: the example compares its value, ``3``, with the version column.

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

:meth:`~scim2_server.storage.ScimStorage.delete` removes the resource, with the same condition on
``expected_version`` in its query:

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
- :attr:`~scim2_models.SearchRequest.sort_by`, :attr:`~scim2_models.SearchRequest.sort_order`,
  :attr:`~scim2_models.SearchRequest.start_index` and :attr:`~scim2_models.SearchRequest.count`
  give the order and the page;
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

Pass the storage to :class:`~scim2_server.applications.wsgi.WSGIApplication` or to
:class:`~scim2_server.applications.asgi.ASGIApplication`, or to the handler of
:doc:`integrate-a-web-framework`:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          storage = SQLiteStorage(sqlite3.connect("scim.sqlite"), provider)
          app = WSGIApplication(storage, provider)

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          async_storage = AsyncSQLiteStorage("scim.sqlite", provider)
          app = ASGIApplication(async_storage, provider)

Close the database when the application stops:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          storage.connection.close()

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          await async_storage.close()

:doc:`../explanation/storage` explains why the storage receives the whole resource, and how the
versions protect concurrent writes.
