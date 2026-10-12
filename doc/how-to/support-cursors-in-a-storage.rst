Support the cursors in a storage
================================

Use this guide to add the cursors of :rfc:`9865` to a storage written as :doc:`write-a-storage`
explains. The server pages the resources of this storage with cursors once it announces them, as
:doc:`page-with-cursors` explains.

Page with the position
----------------------

The cursors need a storage that gives stable pages: :doc:`../explanation/pagination` explains
what they guarantee. :class:`~scim2_server.memory.InMemoryStorage` gives them. To add the
cursors to another storage, set :attr:`~scim2_server.storage.ScimStorage.supports_cursors`, and
page with a cursor when :attr:`~scim2_models.SearchRequest.cursor` is not ``None``. The
``position`` argument then tells which page to return. It is ``None`` on the first page.

The storage below extends the one of :doc:`write-a-storage`, and pages its resources by
identifier. Its server announces neither the filter nor the sort:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/cursor_storage.py
         :language: python
         :pyobject: CursorSQLiteStorage

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/async_cursor_storage.py
         :language: python
         :pyobject: AsyncCursorSQLiteStorage

- The position of the next page is ``{"after": ...}``, with the identifier of the last resource
  of the page. The position of the previous page is ``{"before": ...}``, with the identifier of
  the first one. A position is a value that JSON can hold.
- The next page selects the rows after this identifier. The previous page selects the rows
  before it, in the reverse order, and reverses them afterwards.
- The storage reads ``count + 1`` rows to know whether another page follows.

Support the sort
----------------

A storage that supports the sort orders the rows by the sort value, then by identifier, so that
no two rows are equal. Its positions also hold the sort value of the resource, as
:meth:`~scim2_models.SearchRequest.sort_value` gives it. A row without sort value comes last
when ascending, and first when descending, so the storage writes the condition on the sort value
by hand, rather than with a row comparison, which does not handle ``NULL``.

A backend that can only page with offsets does not set
:attr:`~scim2_server.storage.ScimStorage.supports_cursors`, and its server pages by index only.
The server refuses to start when its configuration announces the cursors and the storage does
not support them.
