import pytest
from scim2_models import URN
from scim2_models import MutabilityException
from scim2_models import PatchOperation
from scim2_models.resources.resource import Resource

from scim2_server.operators import patch_resource


class TestPatch:
    def test_patch_operation_add_simple(self, app):
        user = app.backend.get_model("User")(id="123")
        patch_resource(
            user,
            PatchOperation(
                op=PatchOperation.Op.add,
                value={
                    "userName": "Foo",
                },
            ),
        )
        assert user.model_dump() == {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "id": "123",
            "userName": "Foo",
        }
        patch_resource(
            user,
            PatchOperation(
                op=PatchOperation.Op.add,
                value={
                    "userName": "Bar",
                },
            ),
        )
        assert user.model_dump() == {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "id": "123",
            "userName": "Bar",
        }
        with pytest.raises(MutabilityException):
            patch_resource(
                user,
                PatchOperation(
                    op=PatchOperation.Op.add,
                    value={
                        "id": "4",
                    },
                ),
            )
        assert user.model_dump() == {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "id": "123",
            "userName": "Bar",
        }

    def test_patch_operation_add_complex(self, app):
        user = app.backend.get_model("User")(id="123")
        patch_resource(
            user,
            PatchOperation(
                op=PatchOperation.Op.add,
                value={
                    "name": {"formatted": "Mr. Foo"},
                },
            ),
        )
        assert user.model_dump() == {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "id": "123",
            "name": {
                "formatted": "Mr. Foo",
            },
        }
        patch_resource(
            user,
            PatchOperation(
                op=PatchOperation.Op.add,
                value={
                    "name": {"givenName": "Baz"},
                },
            ),
        )
        assert user.model_dump() == {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "id": "123",
            "name": {
                "givenName": "Baz",
            },
        }
        patch_resource(
            user,
            PatchOperation(
                op=PatchOperation.Op.add,
                path="name",
                value={
                    "formatted": "Mr. Foo",
                },
            ),
        )
        assert user.model_dump() == {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "id": "123",
            "name": {
                "formatted": "Mr. Foo",
            },
        }
        patch_resource(
            user,
            PatchOperation(
                op=PatchOperation.Op.add,
                path="name.familyName",
                value="Jensen",
            ),
        )
        assert user.model_dump() == {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "id": "123",
            "name": {
                "formatted": "Mr. Foo",
                "familyName": "Jensen",
            },
        }

    def test_patch_operation_add_multi_valued(self, app):
        user = app.backend.get_model("User")(id="123")
        patch_resource(
            user,
            PatchOperation(
                op=PatchOperation.Op.add,
                path="emails",
                value={
                    "value": "foo@example.com",
                    "type": "work",
                    "primary": True,
                },
            ),
        )
        assert user.model_dump() == {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "id": "123",
            "emails": [
                {
                    "value": "foo@example.com",
                    "type": "work",
                    "primary": True,
                }
            ],
        }
        patch_resource(
            user,
            PatchOperation(
                op=PatchOperation.Op.add,
                path="emails",
                value={
                    "value": "bar@example.com",
                    "type": "home",
                    "primary": True,
                },
            ),
        )
        assert user.model_dump() == {
            "schemas": [
                "urn:ietf:params:scim:schemas:core:2.0:User",
                "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User",
            ],
            "id": "123",
            "emails": [
                {
                    "value": "foo@example.com",
                    "type": "work",
                    "primary": False,
                },
                {
                    "value": "bar@example.com",
                    "type": "home",
                    "primary": True,
                },
            ],
        }

    def test_patch_replace_multivalued_primitive_attribute(self):
        """Replace a multi-valued primitive attribute."""

        class MyResource(Resource):
            __schema__ = URN("urn:example:schemas:MyResource")

            tags: list[str] | None = None

        resource = MyResource(id="123")

        patch_resource(
            resource,
            PatchOperation(
                op=PatchOperation.Op.replace_,
                path="tags",
                value=["tag1", "tag2"],
            ),
        )

        assert resource.tags == ["tag1", "tag2"]
