from dataclasses import dataclass
from enum import Enum

from scim2_models import NotFoundException

from scim2_server.errors import MethodNotAllowedException

RESERVED_NAMES = frozenset(
    {"ServiceProviderConfig", "ResourceTypes", "Schemas", "Bulk", "Me"}
)
"""The endpoint names of :rfc:`RFC 7644 §3.2 <7644#section-3.2>`. No resource type endpoint can take them."""


class Operation(Enum):
    """A SCIM operation, named after the handler method that serves it."""

    create = "create"
    query = "query"
    replace = "replace"
    patch = "patch"
    delete = "delete"
    search = "search"
    search_with_body = "search_with_body"
    bulk = "bulk"
    service_provider_config = "service_provider_config"
    resource_types = "resource_types"
    resource_type = "resource_type"
    schemas = "schemas"
    schema = "schema"


@dataclass(frozen=True)
class Route:
    """A route of a SCIM server (:rfc:`RFC 7644 §3.2 <7644#section-3.2>`).

    ``{endpoint}`` in the pattern stands for the endpoint of a resource type,
    and ``{resource_id}`` for an identifier.
    """

    method: str
    pattern: str
    operation: Operation
    name: str


ROUTES = (
    Route("GET", "/ServiceProviderConfig", Operation.service_provider_config, "service_provider_config"),
    Route("GET", "/ResourceTypes", Operation.resource_types, "resource_types"),
    Route("GET", "/ResourceTypes/{resource_id}", Operation.resource_type, "resource_type"),
    Route("GET", "/Schemas", Operation.schemas, "schemas"),
    Route("GET", "/Schemas/{resource_id}", Operation.schema, "schema"),
    Route("POST", "/Bulk", Operation.bulk, "bulk"),
    Route("GET", "/Me", Operation.query, "me_query"),
    Route("PUT", "/Me", Operation.replace, "me_replace"),
    Route("PATCH", "/Me", Operation.patch, "me_patch"),
    Route("DELETE", "/Me", Operation.delete, "me_delete"),
    Route("POST", "/Me", Operation.create, "me_create"),
    Route("GET", "/", Operation.search, "search_root"),
    Route("POST", "/.search", Operation.search_with_body, "search_root_with_body"),
    Route("POST", "/{endpoint}/.search", Operation.search_with_body, "search_with_body"),
    Route("GET", "/{endpoint}", Operation.search, "search"),
    Route("POST", "/{endpoint}", Operation.create, "create"),
    Route("GET", "/{endpoint}/{resource_id}", Operation.query, "query"),
    Route("PUT", "/{endpoint}/{resource_id}", Operation.replace, "replace"),
    Route("PATCH", "/{endpoint}/{resource_id}", Operation.patch, "patch"),
    Route("DELETE", "/{endpoint}/{resource_id}", Operation.delete, "delete"),
)  # fmt: skip
"""The routes of a SCIM server, the literal ones first.

The routes of ``/Me`` serve the resource of the authenticated subject
(:rfc:`RFC 7644 §3.11 <7644#section-3.11>`).
"""


@dataclass(frozen=True)
class Target:
    """The operation a request asks for, and what it acts on."""

    operation: Operation
    endpoint: str | None = None
    """The endpoint of the resource type, or :data:`None` at the root."""

    resource_id: str | None = None
    """The identifier of a resource, a resource type or a schema."""

    me: bool = False
    """Whether the request targets ``/Me``, the alias of the resource of the
    authenticated subject."""


def match(method: str, path: str) -> Target:
    """Return the target of a request.

    :raises ~scim2_models.NotFoundException: When no route has this path.
    :raises ~scim2_server.errors.MethodNotAllowedException: When the routes of
        this path do not support the method.
    """
    segments = path.strip("/").split("/") if path.strip("/") else []

    allowed = []
    for route in ROUTES:
        values = match_pattern(route.pattern, segments)
        if values is None:
            continue
        if route.method == method:
            return Target(route.operation, **values, me=route.pattern == "/Me")
        allowed.append(route.method)
    if allowed:
        raise MethodNotAllowedException(allowed=allowed)
    raise NotFoundException(detail=f"No SCIM endpoint at {path!r}")


def match_pattern(pattern: str, segments: list[str]) -> dict[str, str] | None:
    """Return the values of the placeholders of a pattern, or None when the segments do not match it."""
    parts = pattern.strip("/").split("/") if pattern.strip("/") else []
    if len(parts) != len(segments):
        return None
    values = {}
    for part, segment in zip(parts, segments, strict=True):
        if part == "{endpoint}":
            if segment in RESERVED_NAMES or segment == ".search":
                return None
            values["endpoint"] = segment
        elif part == "{resource_id}":
            values["resource_id"] = segment
        elif part != segment:
            return None
    return values
