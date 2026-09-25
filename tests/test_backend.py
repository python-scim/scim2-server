from typing import Annotated

import pytest
from scim2_models import URN
from scim2_models import Attribute
from scim2_models import CaseExact
from scim2_models import Extension
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import Schema
from scim2_models import ScimProvider
from scim2_models import SearchRequest
from scim2_models import Uniqueness
from scim2_models import UniquenessException
from scim2_models import User

from scim2_server.backend import InMemoryBackend


class TestBackend:
    def test_unique_attributes(self, app):
        """The uniqueness constraints are read from the annotations of the model, extensions included."""
        foo_schema = Schema(
            id="urn:example:2.0:Foo",
            name="Foo",
            attributes=[
                Attribute(
                    name="a",
                    type=Attribute.Type.string,
                    uniqueness=Uniqueness.server,
                    case_exact=CaseExact.true,
                ),
            ],
        )
        bar_schema = Schema(
            id="urn:example:2.0:Bar",
            name="Bar",
            attributes=[
                Attribute(
                    name="a",
                    type=Attribute.Type.string,
                    uniqueness=Uniqueness.global_,
                ),
            ],
        )
        Bar = Extension.from_schema(bar_schema)
        FooBar = Resource.from_schema(foo_schema)[Bar]

        assert InMemoryBackend.collect_unique_attrs(FooBar) == [
            InMemoryBackend.UniquenessDescriptor(None, "a", True),
            InMemoryBackend.UniquenessDescriptor("Bar", "a", False),
        ]

        resource = FooBar.model_validate(
            {"a": "ABC", "urn:example:2.0:Bar": {"a": "DEF"}}
        )
        foo, bar = InMemoryBackend.collect_unique_attrs(FooBar)
        assert foo.get_attribute(resource) == "ABC"
        assert bar.get_attribute(resource) == "def"
        assert bar.get_attribute(FooBar(a="ABC")) is None

    def test_unique_attributes_of_the_default_user(self, app):
        """The only uniqueness constraint checked on a User is userName, the id being assigned by the backend."""
        User = app.backend.get_model("User")
        assert InMemoryBackend.collect_unique_attrs(User) == [
            InMemoryBackend.UniquenessDescriptor(None, "user_name", False)
        ]

    def test_a_missing_unique_value_does_not_clash(self):
        """Two resources lacking a unique value do not conflict, as SQL NULLs do not."""

        class Badge(Resource):
            __schema__ = URN("urn:example:2.0:Badge")
            code: Annotated[str | None, Uniqueness.server] = None

        backend = InMemoryBackend(ScimProvider(models=[Badge]))
        backend.create_resource("Badge", Badge())
        backend.create_resource("Badge", Badge())
        backend.create_resource("Badge", Badge(code="x"))
        with pytest.raises(UniquenessException):
            backend.create_resource("Badge", Badge(code="x"))

    def test_unique_values_are_compared_with_unicode_case_folding(self, app):
        """Unicode case folding makes "Straße" and "STRASSE" the same value."""
        backend = app.backend
        User = backend.get_model("User")
        backend.create_resource("User", User(user_name="Straße"))
        with pytest.raises(UniquenessException):
            backend.create_resource("User", User(user_name="STRASSE"))

    def test_query_resources_without_count_returns_every_resource(self, app):
        """A search request carrying no count is not paginated by the backend."""
        backend = app.backend
        for user_name in ("a", "b", "c"):
            backend.create_resource(
                "User", backend.get_model("User")(user_name=user_name)
            )
        total_results, resources = backend.query_resources(SearchRequest(), "User")
        assert total_results == 3
        assert len(resources) == 3

    def test_query_resources_total_results_counts_beyond_the_page(self, app):
        """The total results count every matching resource, not only the returned page."""
        backend = app.backend
        for user_name in ("a", "b", "c"):
            backend.create_resource(
                "User", backend.get_model("User")(user_name=user_name)
            )
        total_results, resources = backend.query_resources(
            SearchRequest(start_index=2, count=1), "User"
        )
        assert total_results == 3
        assert len(resources) == 1

    def test_update_unknown_resource(self, backend):
        resource = User(id="123")
        assert backend.update_resource("User", resource) is None


@pytest.mark.parametrize("resource_type_id", ["User", None])
def test_query_resources_binds_a_filter_that_names_no_resource_type(
    app, resource_type_id
):
    """A filter left unbound is resolved against the resource types being queried."""
    backend = app.backend
    for user_name in ("alice", "bob"):
        backend.create_resource("User", backend.get_model("User")(user_name=user_name))
    request = SearchRequest(filter='userName eq "bob"')
    total_results, resources = backend.query_resources(request, resource_type_id)
    assert total_results == 1
    assert resources[0].user_name == "bob"


def test_a_resource_type_named_apart_from_its_id(static_data):
    """The resources of a resource type whose name differs from its id stay reachable."""
    resource_type = ResourceType(
        id="Usr",
        name="User",
        endpoint="/Users",
        schema="urn:ietf:params:scim:schemas:core:2.0:User",
    )
    provider = ScimProvider.from_discovery(static_data[0].values(), [resource_type])
    backend = InMemoryBackend(provider)
    User = backend.get_model("Usr")
    created = backend.create_resource("Usr", User(user_name="bjensen"))
    assert created.meta.resource_type == "User"

    assert backend.get_resource("Usr", created.id).user_name == "bjensen"
    assert backend.query_resources(SearchRequest(), "Usr")[0] == 1
    with pytest.raises(UniquenessException):
        backend.create_resource("Usr", User(user_name="bjensen"))
    assert backend.delete_resource("Usr", created.id)
    assert backend.get_resource("Usr", created.id) is None
