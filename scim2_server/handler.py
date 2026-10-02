from collections.abc import Mapping
from http import HTTPStatus
from typing import Any
from typing import cast

from scim2_models import BulkOperation
from scim2_models import Error
from scim2_models import InvalidValueException
from scim2_models import PatchOp
from scim2_models import Resource
from scim2_models import ResourceType

from scim2_server.bulk import BulkPlan
from scim2_server.bulk import BulkStep
from scim2_server.conditions import NO_CONDITIONS
from scim2_server.conditions import Conditions
from scim2_server.responses import ScimResponse
from scim2_server.service import ScimService
from scim2_server.storage import ScimStorage

BULK_SUCCESS_STATUS = {
    BulkOperation.Method.post: HTTPStatus.CREATED,
    BulkOperation.Method.put: HTTPStatus.OK,
    BulkOperation.Method.patch: HTTPStatus.OK,
    BulkOperation.Method.delete: HTTPStatus.NO_CONTENT,
}


class ScimHandler:
    """Serve the SCIM operations, by calling the steps of a service and a storage in turn.

    Each method serves one SCIM operation, and returns its
    :class:`~scim2_server.responses.ScimResponse`. A failure raises an exception,
    that :meth:`ScimService.error_response
    <scim2_server.service.ScimService.error_response>` turns into a response.

    ``base_url`` is the root URL of the SCIM endpoints, as the client sees it,
    and ``endpoint`` the endpoint of a resource type, such as ``Users``.
    """

    def __init__(self, service: ScimService, storage: ScimStorage):
        self.service = service
        self.storage = storage

    # -- Resources ------------------------------------------------------

    def create(
        self,
        base_url: str,
        endpoint: str,
        body: bytes,
        content_type: str | None,
    ) -> ScimResponse:
        """Create a resource (RFC 7644 §3.3)."""
        with self.service.provider:
            resource_type = self.service.resource_type_at(endpoint)
            resource = self.service.read_creation(resource_type, body, content_type)
            with self.storage.operation():
                created = self.storage.create(resource_type, resource)
            return self.service.creation_response(base_url, created)

    def query(
        self,
        base_url: str,
        endpoint: str,
        resource_id: str,
        query: Mapping[str, str],
        conditions: Conditions = NO_CONDITIONS,
    ) -> ScimResponse:
        """Read a resource (RFC 7644 §3.4.1)."""
        with self.service.provider:
            resource_type = self.service.resource_type_at(endpoint)
            response_parameters = self.service.read_response_parameters(
                resource_type, query
            )
            with self.storage.operation():
                resource = self.storage.get(resource_type, resource_id)
            return self.service.query_response(
                base_url, resource, response_parameters, conditions
            )

    def replace(
        self,
        base_url: str,
        endpoint: str,
        resource_id: str,
        body: bytes,
        content_type: str | None,
        query: Mapping[str, str],
        conditions: Conditions = NO_CONDITIONS,
    ) -> ScimResponse:
        """Replace a resource (RFC 7644 §3.5.1)."""
        with self.service.provider:
            resource_type = self.service.resource_type_at(endpoint)
            response_parameters = self.service.read_response_parameters(
                resource_type, query
            )
            with self.storage.operation():
                current = self.storage.get(resource_type, resource_id)
                replacement = self.service.read_replacement(
                    resource_type, body, content_type
                )
                resource = self.replace_resource(
                    resource_type, current, replacement, conditions
                )
            return self.service.replacement_response(
                base_url, resource, response_parameters
            )

    def patch(
        self,
        base_url: str,
        endpoint: str,
        resource_id: str,
        body: bytes,
        content_type: str | None,
        query: Mapping[str, str],
        conditions: Conditions = NO_CONDITIONS,
    ) -> ScimResponse:
        """Modify a resource (RFC 7644 §3.5.2)."""
        with self.service.provider:
            resource_type = self.service.resource_type_at(endpoint)
            response_parameters = self.service.read_response_parameters(
                resource_type, query
            )
            patch_op = self.service.read_patch(resource_type, body, content_type)
            with self.storage.operation():
                resource = self.patch_resource(
                    resource_type, resource_id, patch_op, conditions
                )
            return self.service.patch_response(base_url, resource, response_parameters)

    def delete(
        self,
        endpoint: str,
        resource_id: str,
        conditions: Conditions = NO_CONDITIONS,
    ) -> ScimResponse:
        """Delete a resource (RFC 7644 §3.6)."""
        with self.service.provider:
            resource_type = self.service.resource_type_at(endpoint)
            with self.storage.operation():
                self.delete_resource(resource_type, resource_id, conditions)
            return self.service.deletion_response()

    def replace_resource(
        self,
        resource_type: ResourceType,
        current: Resource[Any],
        replacement: Resource[Any],
        conditions: Conditions,
    ) -> Resource[Any]:
        """Apply a validated replacement to a stored resource, and return the stored result."""
        assert current.meta is not None
        version = current.meta.version
        replaced = self.service.apply_replacement(current, replacement, conditions)
        if replaced is None:
            return current
        return self.storage.update(resource_type, replaced, expected_version=version)

    def patch_resource(
        self,
        resource_type: ResourceType,
        resource_id: str,
        patch_op: "PatchOp[Any]",
        conditions: Conditions,
    ) -> Resource[Any]:
        """Apply a validated PATCH to a stored resource, and return the stored result."""
        current = self.storage.get(resource_type, resource_id)
        assert current.meta is not None
        version = current.meta.version
        patched = self.service.apply_patch(current, patch_op, conditions)
        if patched is None:
            return current
        return self.storage.update(resource_type, patched, expected_version=version)

    def delete_resource(
        self, resource_type: ResourceType, resource_id: str, conditions: Conditions
    ) -> None:
        """Delete a stored resource once its conditions are met."""
        current = self.storage.get(resource_type, resource_id)
        self.service.check_deletion(current, conditions)
        assert current.meta is not None
        self.storage.delete(
            resource_type, resource_id, expected_version=current.meta.version
        )

    # -- Search ---------------------------------------------------------

    def search(
        self, base_url: str, endpoint: str | None, query: Mapping[str, str]
    ) -> ScimResponse:
        """Search with GET, on a resource type or at the root when ``endpoint`` is None (RFC 7644 §3.4.2)."""
        with self.service.provider:
            resource_types = self.searched_types(endpoint)
            search_request = self.service.read_search_query(resource_types, query)
            with self.storage.operation():
                total, resources = self.storage.search(resource_types, search_request)
            return self.service.search_response(
                base_url, total, resources, search_request
            )

    def search_with_body(
        self,
        base_url: str,
        endpoint: str | None,
        body: bytes,
        content_type: str | None,
    ) -> ScimResponse:
        """Search with POST on ".search", on a resource type or at the root (RFC 7644 §3.4.3)."""
        with self.service.provider:
            resource_types = self.searched_types(endpoint)
            search_request = self.service.read_search_body(
                resource_types, body, content_type
            )
            with self.storage.operation():
                total, resources = self.storage.search(resource_types, search_request)
            return self.service.search_response(
                base_url, total, resources, search_request
            )

    def searched_types(self, endpoint: str | None) -> list[ResourceType]:
        """Return the resource types a search covers."""
        if endpoint is None:
            return list(self.service.provider.resource_types)
        return [self.service.resource_type_at(endpoint)]

    # -- Bulk -----------------------------------------------------------

    def bulk(
        self, base_url: str, body: bytes, content_type: str | None
    ) -> ScimResponse:
        """Run a bulk request (RFC 7644 §3.7)."""
        with self.service.provider:
            plan = self.service.read_bulk(body, content_type)
            for step in plan:
                result, resource = self.run_bulk_step(base_url, plan, step)
                plan.record(step, result, resource)
            return self.service.bulk_response(plan)

    def run_bulk_step(
        self, base_url: str, plan: BulkPlan, step: BulkStep
    ) -> tuple[dict[str, Any], Resource[Any] | None]:
        """Apply one step of a bulk request.

        An operation that failed its validation keeps its error, once its
        references are resolved to locate it.

        :return: The outcome of the step, and the resource it created or updated.
        """
        operation = plan.operation(step)
        result: dict[str, Any] = {
            "method": operation.method,
            "bulk_id": operation.bulk_id,
        }

        try:
            operation = plan.resolve(step)
            resource_type = self.service.get_resource_type_by_endpoint(
                operation.endpoint or ""
            )
            if resource_type is not None and operation.resource_id:
                result["location"] = self.service.resource_location(
                    base_url, resource_type, operation.resource_id
                )
            if isinstance(operation.response, Error):
                return {
                    **result,
                    "status": operation.status,
                    "response": operation.response,
                }, None
            with self.storage.operation():
                resource = self.apply_bulk_operation(
                    cast(ResourceType, resource_type), operation
                )
        except Exception as exception:
            error = self.service.error_of(exception)
            return {**result, "status": error.status, "response": error}, None

        assert operation.method is not None
        result["status"] = BULK_SUCCESS_STATUS[operation.method]
        if resource is None:
            return result, None

        published = self.service.publish(base_url, resource)
        assert published.meta is not None
        result["location"] = published.meta.location
        result["version"] = published.meta.version
        return result, resource

    def apply_bulk_operation(
        self, resource_type: ResourceType, operation: BulkOperation[Resource[Any]]
    ) -> Resource[Any] | None:
        """Apply a validated bulk operation, and return the resource it acted on.

        The data of the operation is already validated, in the context of the
        request the operation stands for.
        """
        resource_id = operation.resource_id
        if (operation.method == BulkOperation.Method.post) == bool(resource_id):
            raise InvalidValueException(
                detail="A POST path must target a resource type endpoint, other methods a resource"
            )

        if operation.method == BulkOperation.Method.post or resource_id is None:
            return self.storage.create(
                resource_type, cast(Resource[Any], operation.data)
            )

        conditions = Conditions(if_match=operation.version)
        match operation.method:
            case BulkOperation.Method.put:
                return self.replace_resource(
                    resource_type,
                    self.storage.get(resource_type, resource_id),
                    cast(Resource[Any], operation.data),
                    conditions,
                )
            case BulkOperation.Method.patch:
                self.service.ensure_patch_supported()
                return self.patch_resource(
                    resource_type,
                    resource_id,
                    cast("PatchOp[Any]", operation.data),
                    conditions,
                )
            case _:  # DELETE
                self.delete_resource(resource_type, resource_id, conditions)
                return None

    # -- Discovery ------------------------------------------------------

    def service_provider_config(
        self, location: str, query: Mapping[str, str]
    ) -> ScimResponse:
        """Serve the ServiceProviderConfig endpoint (RFC 7644 §4).

        :param location: The URL of the endpoint.
        """
        with self.service.provider:
            return self.service.service_provider_config(location, query)

    def resource_types(self, location: str, query: Mapping[str, str]) -> ScimResponse:
        """Serve the ResourceTypes endpoint (RFC 7644 §4)."""
        with self.service.provider:
            return self.service.resource_types(location, query)

    def resource_type(
        self, location: str, resource_type_id: str, query: Mapping[str, str]
    ) -> ScimResponse:
        """Serve one resource type (RFC 7644 §4)."""
        with self.service.provider:
            return self.service.resource_type(location, resource_type_id, query)

    def schemas(self, location: str, query: Mapping[str, str]) -> ScimResponse:
        """Serve the Schemas endpoint (RFC 7644 §4)."""
        with self.service.provider:
            return self.service.schemas(location, query)

    def schema(
        self, location: str, schema_id: str, query: Mapping[str, str]
    ) -> ScimResponse:
        """Serve one schema (RFC 7644 §4)."""
        with self.service.provider:
            return self.service.schema(location, schema_id, query)
