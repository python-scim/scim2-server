import sqlite3

import pytest

from doc._examples.sqlite_storage import SQLiteStorage
from scim2_server.testing import ScimStorageContract


class TestStorageWithoutRootSearch(ScimStorageContract):
    supports_root_search = False

    @pytest.fixture
    def storage(self, provider):
        connection = sqlite3.connect(":memory:")
        yield SQLiteStorage(connection, provider)
        connection.close()
