import datetime
import importlib.resources
import json
import re
import sys
from typing import Any

from pydantic import EmailStr
from pydantic import ValidationError
from scim2_models import BaseModel
from scim2_models import InvalidValueException
from scim2_models import Mutability
from scim2_models import MutabilityException
from scim2_models import NoTargetException
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import Schema
from scim2_models import ScimProvider
from scim2_models import ServiceProviderConfig


def load_json_resource(json_name: str) -> Any:
    """Load a JSON document from the scim2_server package resources."""
    fp = importlib.resources.files("scim2_server") / "resources" / json_name
    with open(fp) as f:
        return json.load(f)


def load_scim_resource(json_name: str, type_: type[Resource]):
    """Load and validates a JSON document from the scim2_server package resources."""
    ret = {}
    definitions = load_json_resource(json_name)
    for d in definitions:
        model = type_.model_validate(d)
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


def get_by_alias(
    r: type[BaseModel], scim_name: str, allow_none: bool = False
) -> str | None:
    """Return the pydantic attribute name for a BaseModel type and given SCIM attribute name.

    :param r: BaseModel type
    :param scim_name: SCIM attribute name
    :param allow_none: Allow returning None if attribute is not found
    :return: pydantic attribute name
    :raises NoTargetException: If no attribute is found and allow_none
        is False
    """
    try:
        return next(
            k
            for k, v in r.model_fields.items()
            if v.serialization_alias.lower() == scim_name.lower()
        )
    except StopIteration as e:
        if allow_none:
            return None
        raise NoTargetException() from e


def get_or_create(
    model: BaseModel, attribute_name: str, check_mutability: bool = False
):
    """Get or creates a complex attribute model for a given resource.

    :param model: The model
    :param attribute_name: The attribute name
    :param check_mutability: If True, validate that the attribute is
        mutable
    :return: A complex attribute model
    :raises MutabilityException: If attribute is not mutable and
        check_mutability is True
    """
    if check_mutability:
        if model.get_field_annotation(attribute_name, Mutability) in (
            Mutability.read_only,
            Mutability.immutable,
        ):
            raise MutabilityException()
    ret = getattr(model, attribute_name, None)
    if not ret:
        if model.get_field_multiplicity(attribute_name):
            ret = []
            setattr(model, attribute_name, ret)
        else:
            field_root_type = model.get_field_root_type(attribute_name)
            ret = field_root_type()
            setattr(model, attribute_name, ret)
    return ret


def handle_extension(resource: Resource, scim_name: str) -> tuple[BaseModel, str]:
    default_schema = str(resource.__class__.__schema__).lower()
    if scim_name.lower().startswith(default_schema):
        scim_name = scim_name[len(default_schema) :].lstrip(":")
        return resource, scim_name

    if isinstance(resource, Resource):
        for extension_model in resource.get_extension_models():
            extension_prefix = extension_model.lower()
            if scim_name.lower().startswith(extension_prefix):
                scim_name = scim_name[len(extension_prefix) :]
                scim_name = scim_name.lstrip(":")
                if extension_model.lower() not in [s.lower() for s in resource.schemas]:
                    resource.schemas.append(extension_model)
                ext = get_or_create(
                    resource, get_by_alias(type(resource), extension_model)
                )
                return ext, scim_name
    return resource, scim_name


def parse_value(field_root_type: type, value: Any) -> Any:
    """Parse a PATCH value according to the target field root type."""
    if isinstance(value, dict):
        if not hasattr(field_root_type, "model_fields"):
            raise TypeError

        # Work around mixed display/displayName payloads emitted by MS Entra.
        if (
            "display" not in value
            and "display" in field_root_type.model_fields
            and "displayName" in value
        ):
            value = value.copy()
            value["display"] = value["displayName"]
            del value["displayName"]
        return field_root_type.model_validate(value)

    if field_root_type is bool and isinstance(value, str):
        return not value.lower() == "false"

    if field_root_type is datetime.datetime and isinstance(value, str):
        # ISO 8601 datetime format (notably with the Z suffix) are only supported from Python 3.11
        if sys.version_info < (3, 11):  # pragma: no cover
            return datetime.datetime.fromisoformat(re.sub(r"Z$", "+00:00", value))
        return datetime.datetime.fromisoformat(value)

    if field_root_type is EmailStr and isinstance(value, str):
        return value

    if hasattr(field_root_type, "model_fields"):
        primary_value = get_by_alias(field_root_type, "value", True)
        if primary_value is not None:
            return field_root_type(value=value)
        raise TypeError

    return field_root_type(value)


def parse_new_value(model: BaseModel, attribute_name: str, value: Any) -> Any:
    """Given a model and attribute name, attempt to parse a new value so that the type matches the type expected by the model.

    :raises InvalidValueException: If attribute can not be mapped to
        the required type
    """
    field_root_type = model.get_field_root_type(attribute_name)
    try:
        if isinstance(value, list):
            new_value = [parse_value(field_root_type, v) for v in value]
        else:
            new_value = parse_value(field_root_type, value)
    except (AttributeError, TypeError, ValueError, ValidationError) as e:
        raise InvalidValueException() from e
    return new_value
