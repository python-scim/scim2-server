from scim2_server.utils import load_default_provider
from scim2_server.utils import load_default_schemas


def test_the_default_provider_publishes_the_default_schemas():
    """The schemas rebuilt from the default models are the ones the package ships."""
    published = [schema.model_dump() for schema in load_default_provider().schemas]
    shipped = [schema.model_dump() for schema in load_default_schemas().values()]
    assert published == shipped
