import datetime
import importlib.resources
import json
import re
import sys
from inspect import isclass
from typing import Any

from pydantic import EmailStr
from pydantic import ValidationError
from scim2_models import BaseModel
from scim2_models import Error
from scim2_models import Extension
from scim2_models import Mutability
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import Schema


class SCIMException(Exception):
    """A wrapper class, because an "Error" does not inherit from Exception and should not be raised."""

    def __init__(self, scim_error: Error):
        self.scim_error = scim_error


def load_json_resource(json_name: str) -> list:
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


def merge_resources(target: Resource, updates: BaseModel):
    """Merge a resource with another resource as specified for HTTP PUT (RFC 7644, section 3.5.1)."""
    for set_attribute in updates.model_fields_set:
        mutability = target.get_field_annotation(set_attribute, Mutability)
        if mutability == Mutability.read_only:
            continue
        if isinstance(getattr(updates, set_attribute), Extension):
            # This is a model extension, handle it as its own resource
            # and don't simply overwrite it
            merge_resources(
                getattr(target, set_attribute), getattr(updates, set_attribute)
            )
            continue
        new_value = getattr(updates, set_attribute)
        if mutability == Mutability.immutable and getattr(
            target, set_attribute
        ) not in (None, new_value):
            raise SCIMException(Error.make_mutability_error())
        setattr(target, set_attribute, new_value)


def get_by_alias(
    resource: BaseModel, scim_name: str, allow_none: bool = False
) -> str | None:
    """Return the pydantic attribute name for a BaseModel and given SCIM attribute name.

    :param r: BaseModel
    :param scim_name: SCIM attribute name
    :param allow_none: Allow returning None if attribute is not found
    :return: pydantic attribute name
    :raises SCIMException: If no attribute is found and allow_none is
        False
    """
    klass = resource.__class__ if not isclass(resource) else resource
    try:
        return next(
            key
            for key, value in klass.model_fields.items()
            if value.serialization_alias.lower() == scim_name.lower()
        )
    except StopIteration as exc:
        if allow_none:
            return None
        raise SCIMException(Error.make_no_target_error()) from exc


def get_schemas(resource: Resource) -> list[str]:
    """Return a list of all schemas possible for a given resource.

    Note that this may include schemas the resource does not currently
    have (such as missing optional schema extensions).
    """
    return resource.__class__.model_fields["schemas"].default


def get_or_create(
    model: BaseModel, attribute_name: str, check_mutability: bool = False
):
    """Get or creates a complex attribute model for a given resource.

    :param model: The model
    :param attribute_name: The attribute name
    :param check_mutability: If True, validate that the attribute is
        mutable
    :return: A complex attribute model
    :raises SCIMException: If attribute is not mutable and
        check_mutability is True
    """
    if check_mutability:
        if model.get_field_annotation(attribute_name, Mutability) in (
            Mutability.read_only,
            Mutability.immutable,
        ):
            raise SCIMException(Error.make_mutability_error())
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
    default_schema = get_schemas(resource)[0].lower()
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
                ext = get_or_create(resource, get_by_alias(resource, extension_model))
                return ext, scim_name
    return resource, scim_name


def model_validate_from_dict(field_root_type: BaseModel, value: dict) -> Any:
    """Workaround for some of the "special" requirements for MS Entra, mixing display and displayName in some cases."""
    if (
        "display" not in value
        and "display" in field_root_type.model_fields
        and "displayName" in value
    ):
        value["display"] = value["displayName"]
        del value["displayName"]
    return field_root_type.model_validate(value)


def parse_new_value(model: BaseModel, attribute_name: str, value: Any) -> Any:
    """Given a model and attribute name, attempt to parse a new value so that the type matches the type expected by the model.

    :raises SCIMException: If attribute can not be mapped to the
        required type
    """
    field_root_type = model.get_field_root_type(attribute_name)
    try:
        if isinstance(value, dict):
            new_value = model_validate_from_dict(field_root_type, value)
        elif isinstance(value, list):
            new_value = [model_validate_from_dict(field_root_type, v) for v in value]
        else:
            if field_root_type is bool and isinstance(value, str):
                new_value = not value.lower() == "false"
            elif field_root_type is datetime.datetime and isinstance(value, str):
                # ISO 8601 datetime format (notably with the Z suffix) are only supported from Python 3.11
                if sys.version_info < (3, 11):  # pragma: no cover
                    new_value = datetime.datetime.fromisoformat(
                        re.sub(r"Z$", "+00:00", value)
                    )
                else:
                    new_value = datetime.datetime.fromisoformat(value)
            elif field_root_type is EmailStr and isinstance(value, str):
                new_value = value
            elif hasattr(field_root_type, "model_fields"):
                primary_value = get_by_alias(field_root_type, "value", True)
                if primary_value is not None:
                    new_value = field_root_type(value=value)
                else:
                    raise TypeError
            else:
                new_value = field_root_type(value)
    except (AttributeError, TypeError, ValueError, ValidationError) as e:
        raise SCIMException(Error.make_invalid_value_error()) from e
    return new_value
