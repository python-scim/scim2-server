import importlib.resources
import json
from typing import Any
from typing import TypeVar
from typing import cast

from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import Schema
from scim2_models import ScimProvider
from scim2_models import ServiceProviderConfig

GenericT = TypeVar("GenericT")
ResourceT = TypeVar("ResourceT", bound=Resource[Any])


def parametrize(generic: type[GenericT], parameter: Any) -> type[GenericT]:
    """Parametrize a generic class with a type only known at runtime."""
    return cast("type[GenericT]", cast(Any, generic)[parameter])


def load_json_resource(json_name: str) -> Any:
    """Load a JSON document from the scim2_server package resources."""
    fp = importlib.resources.files("scim2_server") / "resources" / json_name
    with fp.open() as f:
        return json.load(f)


def load_scim_resource(json_name: str, type_: type[ResourceT]) -> dict[str, ResourceT]:
    """Load and validates a JSON document from the scim2_server package resources."""
    ret = {}
    definitions = load_json_resource(json_name)
    for d in definitions:
        model = type_.model_validate(d)
        assert model.id is not None
        ret[model.id] = model
    return ret


def load_default_schemas() -> dict[str, Schema]:
    """Load the default schemas from RFC 7643."""
    return load_scim_resource("default-schemas.json", Schema)


def load_default_resource_types() -> dict[str, ResourceType]:
    """Load the default resource types from RFC 7643."""
    return load_scim_resource("default-resource-types.json", ResourceType)


def load_default_service_provider_config() -> ServiceProviderConfig:
    """Load the default service provider configuration."""
    return ServiceProviderConfig.model_validate(
        load_json_resource("default-service-provider-config.json")
    )


def load_default_provider() -> ScimProvider:
    """Describe a service serving the default schemas, resource types and configuration."""
    return ScimProvider.from_discovery(
        load_default_schemas().values(),
        load_default_resource_types().values(),
        config=load_default_service_provider_config(),
    )
