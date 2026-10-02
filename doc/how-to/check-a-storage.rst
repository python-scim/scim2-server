Check a storage
===============

Use this guide to check that a storage follows the rules of
:class:`~scim2_server.storage.ScimStorage`, with the test suite scim2-server provides. It
assumes a storage written with :doc:`write-a-storage`, and tests run with
`pytest <https://docs.pytest.org>`_.

The suite checks what the method signatures do not tell: the copies, the 404 and 412 errors,
the dates and versions, the uniqueness of ``userName``, and the filters, sorting and paging of
the searches. It uses the ``User`` and ``Group`` resource types of :rfc:`RFC 7643 <7643>`.

Run the suite
-------------

#. Install the ``testing`` extra, which installs pytest:

   .. code-block:: console

       $ pip install scim2-server[testing]

#. In a test module, subclass :class:`~scim2_server.testing.ScimStorageContract`, and give it a
   ``storage`` fixture that returns a new, empty storage. The ``provider`` fixture of the suite
   gives the description of the service, that the storage needs. For an
   :class:`~scim2_server.storage.AsyncScimStorage`, subclass
   :class:`~scim2_server.testing.AsyncScimStorageContract` instead, and name the fixture
   ``async_storage``:

   .. tab-set::
      :class: outline

      .. tab-item:: Sync
         :sync: sync

         .. code-block:: python

             import sqlite3

             import pytest
             from scim2_server.testing import ScimStorageContract

             from myapp.scim import SQLiteStorage


             class TestSQLiteStorage(ScimStorageContract):
                 @pytest.fixture
                 def storage(self, provider):
                     connection = sqlite3.connect(":memory:")
                     yield SQLiteStorage(connection, provider)
                     connection.close()

      .. tab-item:: Async
         :sync: async

         .. code-block:: python

             import asyncio

             import pytest
             from scim2_server.testing import AsyncScimStorageContract

             from myapp.scim import AsyncSQLiteStorage


             class TestAsyncSQLiteStorage(AsyncScimStorageContract):
                 @pytest.fixture
                 def async_storage(self, provider):
                     storage = AsyncSQLiteStorage(":memory:", provider)
                     yield storage
                     asyncio.run(storage.close())

   The asynchronous suite runs the same tests. Each test runs the coroutines of the storage on
   its own event loop.

#. Run pytest. Every test of the suite runs against the storage:

   .. code-block:: console

       $ pytest
       ..............................                                           [100%]
       30 passed in 0.83s

Skip what the storage does not support
--------------------------------------

The suite skips the tests of a feature that the service does not announce. pytest lists these
tests as skipped, with the feature they need.

Describe the service as the server announces it. For a storage that cannot filter or sort,
override the ``provider`` fixture. In its :class:`~scim2_models.ServiceProviderConfig`, set
``supported`` to :data:`False` for ``filter`` or ``sort``:

.. literalinclude:: ../_examples/contract_without_search_features.py
   :language: python

.. code-block:: console

    $ pytest -rs
    SKIPPED [2] scim2_server/testing.py:101: The service does not support filtering
    SKIPPED [1] scim2_server/testing.py:101: The service does not support sorting
    ======================== 27 passed, 3 skipped in 1.03s =========================

For a storage that cannot search several resource types at once, set
:attr:`~scim2_server.testing.ScimStorageContract.supports_root_search` to :data:`False`:

.. literalinclude:: ../_examples/contract_without_root_search.py
   :language: python

.. code-block:: console

    $ pytest -rs
    SKIPPED [2] scim2_server/testing.py:101: The service does not support searching at the root
    ======================== 28 passed, 2 skipped in 0.99s =========================
