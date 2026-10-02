Writing resources
=================

This page explains the rules a storage follows, and how the server writes resources safely when
several clients write at the same time. The steps to write a storage are in
:doc:`../how-to/write-a-storage`.

Why the storage receives the whole resource
-------------------------------------------

A PATCH request describes changes: add this value, remove that entry, replace the entries a
filter matches. Applying these changes takes the SCIM path grammar, and the rules on immutable,
read-only and required attributes. scim2-models implements them. The service applies the
request to the stored resource, and the storage only saves the result.

So a storage has a single ``update`` method, for both PUT and PATCH. Every storage gets the
same PATCH rules.

The cost is a read before each write, and a full write where a partial one would do. A SQL
storage can compare the old and the new resource, and only write the changed columns.

Who fills the meta attribute
----------------------------

Every resource carries a ``meta`` attribute, which describes the resource
(:rfc:`RFC 7643 §3.1 <7643#section-3.1>`). The client never sets it. Each value goes to the
layer that has the information:

.. list-table::
   :header-rows: 1

   * - Value
     - Filled by
     - Reason
   * - ``id``
     - the storage
     - It is the identifier of the backend.
   * - ``resourceType``
     - the storage
     - Two resource types can share a schema. A search at the root needs it to find the endpoint
       of each resource.
   * - ``created``, ``lastModified``
     - the storage
     - Only the storage knows when it writes.
   * - ``version``
     - the storage
     - It can be a version column, a counter or a hash.
   * - ``location``
     - the service
     - It depends on the URL of the server, which the storage does not know.

Why the resources are copies
----------------------------

The service changes the resources it reads: it applies a PATCH to the resource the storage
returns. If the storage returned its own object, a PATCH that fails halfway would leave the
stored resource half changed. With a copy, a failed PATCH never reaches ``update``, and nothing
is saved.

Concurrent writes
-----------------

Two clients can change the same resource at the same time. Three problems can follow:

- **Lost updates.** A PUT or a PATCH reads the resource, changes it, and writes it. When two
  requests overlap, the second one overwrites the first.
- **Outdated ETags.** The service checks ``If-Match`` after the read, but the resource can change
  before the write.
- **Duplicates.** "Check that the userName is free, then insert" is not atomic.

The server solves the first two with optimistic concurrency. The handler passes the version it
read as ``expected_version`` to ``update`` and ``delete``. The storage refuses the write with a
412 when the stored version differs. A SQL storage does it in one query:
``UPDATE … WHERE id = :id AND version = :version``. No update means a 412.

The third one belongs to the storage, which raises
:class:`~scim2_models.UniquenessException`. A SQL storage relies on a unique constraint.

Transactions
------------

The handler calls :meth:`~scim2_server.storage.ScimStorage.operation` around each SCIM
operation, and around each operation of a bulk request. A SQL storage opens a savepoint there.
This matters for the bulk requests: :rfc:`RFC 7644 §3.7 <7644#section-3.7>` asks the server to
go on after a failed operation, and with PostgreSQL, a failed statement makes the whole
transaction unusable until it is rolled back to a savepoint.

The server never commits. The integration or the application does, for instance with one
transaction per request.

The in-memory storages
----------------------

:class:`~scim2_server.memory.InMemoryStorage` holds a lock during each call and each operation,
so a threaded server can use it. Its versions come from a counter, so two writes never get the
same version, even within the same microsecond.

:class:`~scim2_server.memory.AsyncInMemoryStorage` takes no lock in
:meth:`~scim2_server.storage.AsyncScimStorage.operation`. Holding a thread lock across an
``await`` would block the event loop. Its calls never await anything, so no other coroutine
runs during a call. Two concurrent updates of a resource are still told apart by
``expected_version``.
