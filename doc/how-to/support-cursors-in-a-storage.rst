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

The storage uses the helpers of :mod:`scim2_server.keyset`:

- :meth:`Position.load <scim2_server.keyset.Position.load>` reads the position. It holds the
  identifier of the row the page starts after, or ends before.
- The next page selects the rows after this identifier. The previous page selects the rows
  before it, in the reverse order.
- The storage reads ``count + 1`` rows. :func:`~scim2_server.keyset.page_rows` puts them back in
  order, and tells whether another page follows.
- :func:`~scim2_server.keyset.keyset_page` returns the page, with the positions of the pages
  before and after it. It needs the first and the last rows of the page, as
  :class:`~scim2_server.keyset.Bound`.

A position is a value that JSON can hold. A storage may write its own positions instead of
these ones.

Support the sort
----------------

A storage that supports the sort orders the rows by the sort value, then by identifier, so that
no two rows are equal. Its positions also hold the sort value of the resource, as
:meth:`~scim2_models.SearchRequest.sort_value` gives it. A row without sort value comes last
when ascending, and first when descending, so the storage writes the condition on the sort value
by hand, rather than with a row comparison, which does not handle ``NULL``.

With the helpers, the storage passes ``has_sort=True`` to
:meth:`Position.load <scim2_server.keyset.Position.load>`, and the sort value of each row as the
``key`` of its :class:`~scim2_server.keyset.Bound`. Dates, decimal numbers and UUIDs are kept in
their own type.

At the root, two resources of different types may share an identifier. A storage can then order
the rows by resource type, between the sort value and the identifier. It passes the number of
resource types as ``type_count`` to :meth:`Position.load <scim2_server.keyset.Position.load>`,
and the index of the resource type of each row as ``type_index``. The storage of the example
does not need it: its identifiers are UUIDs, so they are unique across the resource types.

A backend that can only page with offsets does not set
:attr:`~scim2_server.storage.ScimStorage.supports_cursors`, and its server pages by index only.
The server refuses to start when its configuration announces the cursors and the storage does
not support them.
