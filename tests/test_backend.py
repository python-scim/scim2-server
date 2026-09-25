import pytest
from scim2_models import Attribute
from scim2_models import CaseExact
from scim2_models import Extension
from scim2_models import ResourceType
from scim2_models import Schema
from scim2_models import SchemaExtension
from scim2_models import SearchRequest
from scim2_models import Uniqueness
from scim2_models import User

from scim2_server.backend import InMemoryBackend


class TestBackend:
    def test_unique_attributes(self, provider):
        backend = provider.backend
        assert "Group" in backend.unique_attributes
        assert "User" in backend.unique_attributes
        assert len(backend.unique_attributes["User"]) == 1
        assert backend.unique_attributes["User"][
            0
        ] == InMemoryBackend.UniquenessDescriptor(
            schema=None, attribute_name="userName", case_exact=False
        )

        rt = ResourceType(
            schema="urn:example:2.0:Foo",
            schema_extensions=[
                SchemaExtension(schema="urn:example:2.0:Bar", required=True)
            ],
        )
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
                    case_exact=CaseExact.true,
                ),
            ],
        )

        desc_1 = InMemoryBackend.UniquenessDescriptor(
            schema=None, attribute_name="a", case_exact=True
        )
        desc_2 = InMemoryBackend.UniquenessDescriptor(
            schema="urn:example:2.0:Bar", attribute_name="a", case_exact=True
        )

        assert InMemoryBackend.collect_resource_unique_attrs(
            rt,
            {
                "urn:example:2.0:Foo": foo_schema,
                "urn:example:2.0:Bar": bar_schema,
            },
        ) == [desc_1, desc_2]

        ResType = ResourceType.from_schema(foo_schema)[
            Extension.from_schema(bar_schema)
        ]
        res = ResType.model_validate(
            {
                "a": "ABC",
                "urn:example:2.0:Bar": {
                    "a": "DEF",
                },
            }
        )
        assert desc_1.get_attribute(res) == "ABC"
        assert desc_2.get_attribute(res) == "DEF"

    def test_query_resources_without_count_returns_every_resource(self, provider):
        """A search request carrying no count is not paginated by the backend."""
        backend = provider.backend
        for user_name in ("a", "b", "c"):
            backend.create_resource(
                "User", backend.get_model("User")(user_name=user_name)
            )
        total_results, resources = backend.query_resources(SearchRequest(), "User")
        assert total_results == 3
        assert len(resources) == 3

    def test_query_resources_total_results_counts_beyond_the_page(self, provider):
        """The total results count every matching resource, not only the returned page."""
        backend = provider.backend
        for user_name in ("a", "b", "c"):
            backend.create_resource(
                "User", backend.get_model("User")(user_name=user_name)
            )
        total_results, resources = backend.query_resources(
            SearchRequest(start_index=2, count=1), "User"
        )
        assert total_results == 3
        assert len(resources) == 1

    def test_meta_resource_type_name(self, provider):
        backend = provider.backend
        backend.resource_types["User"].name = "User RT Name"
        resource = backend.get_model("User")(user_name="bjensen")
        created = backend.create_resource("User", resource)
        assert created.meta.resource_type == "User RT Name"

    def test_register_resource_type_unknown_schema(self):
        backend = InMemoryBackend()
        rt = ResourceType(schema="urn:unknown:Foo")
        with pytest.raises(RuntimeError):
            backend.register_resource_type(rt)

        schema = Schema(id="urn:unknown:Foo")
        backend.register_schema(schema)
        rt = ResourceType(
            schema="urn:unknown:Foo",
            schema_extensions=[
                SchemaExtension(schema="urn:unknown:Bar", required=True)
            ],
        )
        with pytest.raises(RuntimeError):
            backend.register_resource_type(rt)

    def test_update_unknown_resource(self):
        backend = InMemoryBackend()
        resource = User(id="123")
        assert backend.update_resource("User", resource) is None
