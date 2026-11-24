Storages
========

The contract between the server and the data of an application, and the storages that keep the
resources in memory.

.. autoclass:: scim2_server.storage.ScimStorage
   :members:

.. autoclass:: scim2_server.storage.AsyncScimStorage
   :members:

.. autoclass:: scim2_server.storage.SearchPage
   :members:

.. autoclass:: scim2_server.memory.InMemoryStorage
   :members: generate_id, next_version, operation

.. autoclass:: scim2_server.memory.AsyncInMemoryStorage
   :members: resources

Keyset pagination
-----------------

.. automodule:: scim2_server.keyset

.. autofunction:: scim2_server.keyset.encode_sort_value

.. autofunction:: scim2_server.keyset.decode_sort_value

.. autoclass:: scim2_server.keyset.Position
   :members:

.. autoclass:: scim2_server.keyset.Bound
   :members:

.. autofunction:: scim2_server.keyset.page_rows

.. autofunction:: scim2_server.keyset.keyset_page
