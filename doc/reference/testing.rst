Contract tests
==============

The test suites checking that a storage follows the storage contract. They need the
``testing`` extra.

.. autoclass:: scim2_server.testing.ScimStorageContract
   :members: supports_root_search, search_users

.. autoclass:: scim2_server.testing.AsyncScimStorageContract
