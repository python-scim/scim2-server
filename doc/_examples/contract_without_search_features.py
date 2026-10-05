import sqlite3

import pytest
from myapp.scim import SQLiteStorage

from scim2_server.testing import ScimStorageContract
from scim2_server.utils import load_default_provider


class TestStorageWithoutFilterNorSort(ScimStorageContract):
    @pytest.fixture
    def storage(self, provider):
        connection = sqlite3.connect(":memory:")
        yield SQLiteStorage(connection, provider)
        connection.close()

    @pytest.fixture
    def provider(self):
        provider = load_default_provider()
        provider.config.filter.supported = False
        provider.config.sort.supported = False
        return provider
