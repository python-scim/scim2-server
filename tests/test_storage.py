from scim2_server.storage import ScimStorage


class MinimalStorage(ScimStorage):
    def get(self, resource_type, resource_id):
        raise NotImplementedError

    def search(self, resource_types, search_request):
        raise NotImplementedError

    def create(self, resource_type, resource):
        raise NotImplementedError

    def update(self, resource_type, resource, *, expected_version=None):
        raise NotImplementedError

    def delete(self, resource_type, resource_id, *, expected_version=None):
        raise NotImplementedError


def test_operation_does_nothing_by_default():
    """A storage that does not enclose its operations gets a context that does nothing."""
    with MinimalStorage().operation() as value:
        assert value is None
