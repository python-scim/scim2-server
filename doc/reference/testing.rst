Contract tests
==============

The test suites checking that a storage follows the storage contract. They need the
``testing`` extra.

.. autoclass:: scim2_server.testing.ScimStorageContract
   :members: supports_root_search, stores_created, stores_last_modified, stores_password, stores_mailboxes, search_users

.. autoclass:: scim2_server.testing.Mailbox()

.. autoclass:: scim2_server.testing.MailboxAddress()

.. autoclass:: scim2_server.testing.MailboxOwner()

.. autodata:: scim2_server.testing.MAILBOX_TYPE
   :no-value:

.. autoclass:: scim2_server.testing.AsyncScimStorageContract
