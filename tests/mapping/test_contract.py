import pytest

from scim2_server.testing import ScimStorageContract

from .backend import FakeStorage
from .tables import UNDATED
from .tables import WORK_EMAIL
from .tables import groups_mapping
from .tables import users_mapping


def one_work_email(user):
    emails = user.get("emails")
    if not emails:
        return user

    return user | {"emails": [{"value": emails[-1]["value"], "type": "work"}]}


class TestMappedStorage(ScimStorageContract):
    """Users and groups with a version counter and dates."""

    supports_root_search = False

    @pytest.fixture
    def storage(self, provider):
        mappings = {"User": users_mapping(), "Group": groups_mapping()}
        return FakeStorage(mappings, provider)


class TestMappedStorageWithHashes(ScimStorageContract):
    """Resources without counter nor dates get the hash of their values as version."""

    supports_root_search = False
    stores_created = False
    stores_last_modified = False

    @pytest.fixture
    def storage(self, provider):
        mappings = {
            "User": users_mapping(UNDATED, version=None),
            "Group": groups_mapping(UNDATED, version=None),
        }
        return FakeStorage(mappings, provider, version_key=b"secret")


class TestMappedStorageWithAnEntry(ScimStorageContract):
    """The emails of the users are a work email, held by the user record."""

    supports_root_search = False

    def search_users(self):
        return [one_work_email(user) for user in super().search_users()]

    @pytest.fixture
    def storage(self, provider):
        mappings = {"User": users_mapping(WORK_EMAIL), "Group": groups_mapping()}
        return FakeStorage(mappings, provider)
