Storages
========

The contract between the server and the data of an application, and the storages that keep the
resources in memory.

.. autoclass:: scim2_server.storage.ScimStorage
   :members:

.. autoclass:: scim2_server.storage.AsyncScimStorage
   :members:

.. autoclass:: scim2_server.memory.InMemoryStorage
   :members: generate_id, next_version, operation

.. autoclass:: scim2_server.memory.AsyncInMemoryStorage
   :members: resources
