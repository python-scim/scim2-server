Check a storage
===============

Use this guide to check that a storage follows the rules of
:class:`~scim2_server.storage.ScimStorage`, with the test suite scim2-server provides. It
assumes a storage written as :doc:`write-a-storage` explains, and tests run with
:doc:`pytest <pytest:index>`.

The suite checks what the method signatures do not tell: the copies, the 404 and 412 errors,
the dates and versions, the uniqueness of ``userName``, and the filters, sorting and paging of
the searches. It runs each filter and each sort of a list, and compares the answer of the
storage with the rules of SCIM. It uses the ``User`` and ``Group`` resource types of :rfc:`RFC 7643 <7643>`.

Run the suite
-------------

#. Install the ``testing`` extra, which installs pytest:

   .. code-block:: console

       $ pip install "scim2-server[testing]"

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
       ........................................................................ [ 75%]
       ........................                                                 [100%]
       96 passed in 6.09s

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
    SKIPPED [46] scim2_server/testing.py:215: The service does not support filtering
    SKIPPED [21] scim2_server/testing.py:215: The service does not support sorting
    SKIPPED [8] scim2_server/testing.py:215: The service does not support cursor pagination
    ======================= 31 passed, 75 skipped in 6.57s =========================

For a storage that cannot search several resource types at once, set
:attr:`~scim2_server.testing.ScimStorageContract.supports_root_search` to :data:`False`:

.. literalinclude:: ../_examples/contract_without_root_search.py
   :language: python

.. code-block:: console

    $ pytest -rs
    SKIPPED [3] scim2_server/testing.py:215: The service does not support searching at the root
    SKIPPED [9] scim2_server/testing.py:215: The service does not support cursor pagination
    ======================= 94 passed, 12 skipped in 6.79s =========================

A storage refuses a filter it cannot translate with :class:`~scim2_models.InvalidFilterException`,
and a sort with :class:`~scim2_models.InvalidPathException`. The suite skips the filters and the
sorts the storage refuses, and checks the others:

.. code-block:: console

    $ pytest -rs
    SKIPPED [11] scim2_server/testing.py:653: The storage refuses the search: Cannot filter on 'title'
    SKIPPED [2] scim2_server/testing.py:653: The storage refuses the search: Cannot sort on 'title'

The filters and the sorts search users with the attributes of the ``User`` resource type and of
its enterprise extension. For a storage that does not store some of them, override
:meth:`~scim2_server.testing.ScimStorageContract.search_users`, and return the users without
these attributes.
