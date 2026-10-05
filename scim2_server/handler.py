from typing import Any
from typing import cast

from scim2_models import BulkOperation
from scim2_models import PatchOp
from scim2_models import Resource
from scim2_models import ResourceType

from scim2_server.bulk import BulkPlan
from scim2_server.bulk import BulkStep
from scim2_server.conditions import Conditions
from scim2_server.requests import ScimRequest
from scim2_server.responses import ScimResponse
from scim2_server.routing import Operation
from scim2_server.service import ScimService
from scim2_server.storage import AsyncScimStorage
from scim2_server.storage import ScimStorage


class ScimHandler:
    """Serve the SCIM operations, by calling the steps of a service and a storage in turn.

    Each method serves one SCIM operation: it takes a
    :class:`~scim2_server.requests.ScimRequest`, and returns its
    :class:`~scim2_server.responses.ScimResponse`. :meth:`handle` serves any
    request, by calling the method of its operation. A failure raises a
    :class:`~scim2_models.SCIMException`, that :meth:`ScimService.error_response
    <scim2_server.service.ScimService.error_response>` turns into a response.
    Any other exception is a bug.
    """

    def __init__(self, service: ScimService, storage: ScimStorage):
        self.service = service
        self.storage = storage

    def handle(self, request: ScimRequest) -> ScimResponse:
        """Serve a request with the method of its operation (:rfc:`RFC 7644 §3.2 <7644#section-3.2>`).

        :raises ~scim2_models.NotFoundException: When the path matches no
            route.
        :raises ~scim2_server.errors.MethodNotAllowedException: When the
            endpoint does not support the method.
        :raises ~scim2_models.NotImplementedException: When the request is
            on ``/Me`` and the service does not override
            :meth:`~scim2_server.service.ScimService.me_target`.
        """
        with self.service.provider:
            target = self.service.match(request)
        response: ScimResponse = getattr(self, target.operation.value)(request)
        return response

    # -- Resources ------------------------------------------------------

    def create(self, request: ScimRequest) -> ScimResponse:
        """Create a resource (:rfc:`RFC 7644 §3.3 <7644#section-3.3>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.create)
            resource_type = self.service.resource_type_at(cast(str, target.endpoint))
            self.service.authorize(request, target, resource_type)
            resource = self.service.read_creation(
                resource_type, request.body, request.header("Content-Type")
            )
            with self.storage.operation():
                created = self.storage.create(resource_type, resource)
            return self.service.creation_response(request.base_url, created)

    def query(self, request: ScimRequest) -> ScimResponse:
        """Read a resource (:rfc:`RFC 7644 §3.4.1 <7644#section-3.4.1>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.query)
            resource_type = self.service.resource_type_at(cast(str, target.endpoint))
            self.service.authorize(request, target, resource_type)
            response_parameters = self.service.read_response_parameters(
                resource_type, request.query
            )
            with self.storage.operation():
                resource = self.storage.get(
                    resource_type, cast(str, target.resource_id)
                )
            response = self.service.query_response(
                request.base_url,
                resource,
                response_parameters,
                self.service.read_conditions(request),
            )
            return self.service.me_response(request, target, response)

    def replace(self, request: ScimRequest) -> ScimResponse:
        """Replace a resource (:rfc:`RFC 7644 §3.5.1 <7644#section-3.5.1>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.replace)
            resource_type = self.service.resource_type_at(cast(str, target.endpoint))
            self.service.authorize(request, target, resource_type)
            response_parameters = self.service.read_response_parameters(
                resource_type, request.query
            )
            with self.storage.operation():
                current = self.storage.get(resource_type, cast(str, target.resource_id))
                replacement = self.service.read_replacement(
                    resource_type, request.body, request.header("Content-Type")
                )
                resource = self.replace_resource(
                    resource_type,
                    current,
                    replacement,
                    self.service.read_conditions(request),
                )
            response = self.service.replacement_response(
                request.base_url, resource, response_parameters
            )
            return self.service.me_response(request, target, response)

    def patch(self, request: ScimRequest) -> ScimResponse:
        """Modify a resource (:rfc:`RFC 7644 §3.5.2 <7644#section-3.5.2>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.patch)
            resource_type = self.service.resource_type_at(cast(str, target.endpoint))
            self.service.authorize(request, target, resource_type)
            response_parameters = self.service.read_response_parameters(
                resource_type, request.query
            )
            patch_op = self.service.read_patch(
                resource_type, request.body, request.header("Content-Type")
            )
            with self.storage.operation():
                resource = self.patch_resource(
                    resource_type,
                    cast(str, target.resource_id),
                    patch_op,
                    self.service.read_conditions(request),
                )
            response = self.service.patch_response(
                request.base_url, resource, response_parameters
            )
            return self.service.me_response(request, target, response)

    def delete(self, request: ScimRequest) -> ScimResponse:
        """Delete a resource (:rfc:`RFC 7644 §3.6 <7644#section-3.6>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.delete)
            resource_type = self.service.resource_type_at(cast(str, target.endpoint))
            self.service.authorize(request, target, resource_type)
            with self.storage.operation():
                self.delete_resource(
                    resource_type,
                    cast(str, target.resource_id),
                    self.service.read_conditions(request),
                )
            return self.service.me_response(
                request, target, self.service.deletion_response()
            )

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

    def search(self, request: ScimRequest) -> ScimResponse:
        """Search with GET, on a resource type or at the root (:rfc:`RFC 7644 §3.4.2 <7644#section-3.4.2>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.search)
            resource_types = self.service.searched_types(target.endpoint)
            authorized_types = self.service.authorized_types(
                request, target, resource_types
            )
            search_request = self.service.read_search_query(
                resource_types, request.query
            )
            with self.storage.operation():
                total, resources = self.storage.search(authorized_types, search_request)
            return self.service.search_response(
                request.base_url, total, resources, search_request
            )

    def search_with_body(self, request: ScimRequest) -> ScimResponse:
        """Search with POST on ".search", on a resource type or at the root (:rfc:`RFC 7644 §3.4.3 <7644#section-3.4.3>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.search_with_body)
            resource_types = self.service.searched_types(target.endpoint)
            authorized_types = self.service.authorized_types(
                request, target, resource_types
            )
            search_request = self.service.read_search_body(
                resource_types, request.body, request.header("Content-Type")
            )
            with self.storage.operation():
                total, resources = self.storage.search(authorized_types, search_request)
            return self.service.search_response(
                request.base_url, total, resources, search_request
            )

    # -- Bulk -----------------------------------------------------------

    def bulk(self, request: ScimRequest) -> ScimResponse:
        """Run a bulk request (:rfc:`RFC 7644 §3.7 <7644#section-3.7>`)."""
        with self.service.provider:
            self.service.route(request, Operation.bulk)
            plan = self.service.read_bulk(request.body, request.header("Content-Type"))
            for step in plan:
                result, resource = self.run_bulk_step(request, plan, step)
                plan.record(step, result, resource)
            return self.service.bulk_response(plan)

    def run_bulk_step(
        self, request: ScimRequest, plan: BulkPlan, step: BulkStep
    ) -> tuple[dict[str, Any], Resource[Any] | None]:
        """Apply one step of a bulk request.

        The step resolves the references of the operation, locates it and
        authorizes it. An operation that failed its validation then keeps its
        error.

        :return: The outcome of the step, and the resource it created or updated.
        """
        operation = plan.operation(step)
        outcome = self.service.bulk_outcome(operation)
        try:
            operation = plan.resolve(step)
            resource_type = self.service.locate_bulk_operation(
                request.base_url, operation, outcome
            )
            self.service.authorize_bulk_operation(request, operation, resource_type)
            failure = self.service.bulk_validation_failure(operation, outcome)
            if failure is not None:
                return failure, None
            with self.storage.operation():
                resource = self.apply_bulk_operation(
                    cast(ResourceType, resource_type), operation
                )
        except Exception as exception:
            return self.service.bulk_failure(outcome, exception), None
        return self.service.bulk_success(
            request.base_url, operation, outcome, resource
        ), resource

    def apply_bulk_operation(
        self, resource_type: ResourceType, operation: BulkOperation[Resource[Any]]
    ) -> Resource[Any] | None:
        """Apply a validated bulk operation, and return the resource it acted on.

        The data of the operation is already validated, in the context of the
        request the operation stands for.
        """
        resource_id = self.service.check_bulk_target(operation)
        if resource_id is None:
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

    def service_provider_config(self, request: ScimRequest) -> ScimResponse:
        """Serve the ServiceProviderConfig endpoint (:rfc:`RFC 7644 §4 <7644#section-4>`)."""
        with self.service.provider:
            self.service.route(request, Operation.service_provider_config)
            return self.service.service_provider_config(request.url, request.query)

    def resource_types(self, request: ScimRequest) -> ScimResponse:
        """Serve the ResourceTypes endpoint (:rfc:`RFC 7644 §4 <7644#section-4>`)."""
        with self.service.provider:
            self.service.route(request, Operation.resource_types)
            return self.service.resource_types(request.url, request.query)

    def resource_type(self, request: ScimRequest) -> ScimResponse:
        """Serve one resource type (:rfc:`RFC 7644 §4 <7644#section-4>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.resource_type)
            return self.service.resource_type(
                request.url, cast(str, target.resource_id), request.query
            )

    def schemas(self, request: ScimRequest) -> ScimResponse:
        """Serve the Schemas endpoint (:rfc:`RFC 7644 §4 <7644#section-4>`)."""
        with self.service.provider:
            self.service.route(request, Operation.schemas)
            return self.service.schemas(request.url, request.query)

    def schema(self, request: ScimRequest) -> ScimResponse:
        """Serve one schema (:rfc:`RFC 7644 §4 <7644#section-4>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.schema)
            return self.service.schema(
                request.url, cast(str, target.resource_id), request.query
            )


class AsyncScimHandler:
    """The asynchronous variant of :class:`ScimHandler`, over an :class:`~scim2_server.storage.AsyncScimStorage`.

    It calls the same steps of the same service, and awaits the storage
    between them.
    """

    def __init__(self, service: ScimService, storage: AsyncScimStorage):
        self.service = service
        self.storage = storage

    async def handle(self, request: ScimRequest) -> ScimResponse:
        """Serve a request with the method of its operation (:rfc:`RFC 7644 §3.2 <7644#section-3.2>`).

        :raises ~scim2_models.NotFoundException: When the path matches no
            route.
        :raises ~scim2_server.errors.MethodNotAllowedException: When the
            endpoint does not support the method.
        :raises ~scim2_models.NotImplementedException: When the request is
            on ``/Me`` and the service does not override
            :meth:`~scim2_server.service.ScimService.me_target`.
        """
        with self.service.provider:
            target = self.service.match(request)
        response: ScimResponse = await getattr(self, target.operation.value)(request)
        return response

    # -- Resources ------------------------------------------------------

    async def create(self, request: ScimRequest) -> ScimResponse:
        """Create a resource (:rfc:`RFC 7644 §3.3 <7644#section-3.3>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.create)
            resource_type = self.service.resource_type_at(cast(str, target.endpoint))
            self.service.authorize(request, target, resource_type)
            resource = self.service.read_creation(
                resource_type, request.body, request.header("Content-Type")
            )
            async with self.storage.operation():
                created = await self.storage.create(resource_type, resource)
            return self.service.creation_response(request.base_url, created)

    async def query(self, request: ScimRequest) -> ScimResponse:
        """Read a resource (:rfc:`RFC 7644 §3.4.1 <7644#section-3.4.1>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.query)
            resource_type = self.service.resource_type_at(cast(str, target.endpoint))
            self.service.authorize(request, target, resource_type)
            response_parameters = self.service.read_response_parameters(
                resource_type, request.query
            )
            async with self.storage.operation():
                resource = await self.storage.get(
                    resource_type, cast(str, target.resource_id)
                )
            response = self.service.query_response(
                request.base_url,
                resource,
                response_parameters,
                self.service.read_conditions(request),
            )
            return self.service.me_response(request, target, response)

    async def replace(self, request: ScimRequest) -> ScimResponse:
        """Replace a resource (:rfc:`RFC 7644 §3.5.1 <7644#section-3.5.1>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.replace)
            resource_type = self.service.resource_type_at(cast(str, target.endpoint))
            self.service.authorize(request, target, resource_type)
            response_parameters = self.service.read_response_parameters(
                resource_type, request.query
            )
            async with self.storage.operation():
                current = await self.storage.get(
                    resource_type, cast(str, target.resource_id)
                )
                replacement = self.service.read_replacement(
                    resource_type, request.body, request.header("Content-Type")
                )
                resource = await self.replace_resource(
                    resource_type,
                    current,
                    replacement,
                    self.service.read_conditions(request),
                )
            response = self.service.replacement_response(
                request.base_url, resource, response_parameters
            )
            return self.service.me_response(request, target, response)

    async def patch(self, request: ScimRequest) -> ScimResponse:
        """Modify a resource (:rfc:`RFC 7644 §3.5.2 <7644#section-3.5.2>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.patch)
            resource_type = self.service.resource_type_at(cast(str, target.endpoint))
            self.service.authorize(request, target, resource_type)
            response_parameters = self.service.read_response_parameters(
                resource_type, request.query
            )
            patch_op = self.service.read_patch(
                resource_type, request.body, request.header("Content-Type")
            )
            async with self.storage.operation():
                resource = await self.patch_resource(
                    resource_type,
                    cast(str, target.resource_id),
                    patch_op,
                    self.service.read_conditions(request),
                )
            response = self.service.patch_response(
                request.base_url, resource, response_parameters
            )
            return self.service.me_response(request, target, response)

    async def delete(self, request: ScimRequest) -> ScimResponse:
        """Delete a resource (:rfc:`RFC 7644 §3.6 <7644#section-3.6>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.delete)
            resource_type = self.service.resource_type_at(cast(str, target.endpoint))
            self.service.authorize(request, target, resource_type)
            async with self.storage.operation():
                await self.delete_resource(
                    resource_type,
                    cast(str, target.resource_id),
                    self.service.read_conditions(request),
                )
            return self.service.me_response(
                request, target, self.service.deletion_response()
            )

    async def replace_resource(
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
        return await self.storage.update(
            resource_type, replaced, expected_version=version
        )

    async def patch_resource(
        self,
        resource_type: ResourceType,
        resource_id: str,
        patch_op: "PatchOp[Any]",
        conditions: Conditions,
    ) -> Resource[Any]:
        """Apply a validated PATCH to a stored resource, and return the stored result."""
        current = await self.storage.get(resource_type, resource_id)
        assert current.meta is not None
        version = current.meta.version
        patched = self.service.apply_patch(current, patch_op, conditions)
        if patched is None:
            return current
        return await self.storage.update(
            resource_type, patched, expected_version=version
        )

    async def delete_resource(
        self, resource_type: ResourceType, resource_id: str, conditions: Conditions
    ) -> None:
        """Delete a stored resource once its conditions are met."""
        current = await self.storage.get(resource_type, resource_id)
        self.service.check_deletion(current, conditions)
        assert current.meta is not None
        await self.storage.delete(
            resource_type, resource_id, expected_version=current.meta.version
        )

    # -- Search ---------------------------------------------------------

    async def search(self, request: ScimRequest) -> ScimResponse:
        """Search with GET, on a resource type or at the root (:rfc:`RFC 7644 §3.4.2 <7644#section-3.4.2>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.search)
            resource_types = self.service.searched_types(target.endpoint)
            authorized_types = self.service.authorized_types(
                request, target, resource_types
            )
            search_request = self.service.read_search_query(
                resource_types, request.query
            )
            async with self.storage.operation():
                total, resources = await self.storage.search(
                    authorized_types, search_request
                )
            return self.service.search_response(
                request.base_url, total, resources, search_request
            )

    async def search_with_body(self, request: ScimRequest) -> ScimResponse:
        """Search with POST on ".search", on a resource type or at the root (:rfc:`RFC 7644 §3.4.3 <7644#section-3.4.3>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.search_with_body)
            resource_types = self.service.searched_types(target.endpoint)
            authorized_types = self.service.authorized_types(
                request, target, resource_types
            )
            search_request = self.service.read_search_body(
                resource_types, request.body, request.header("Content-Type")
            )
            async with self.storage.operation():
                total, resources = await self.storage.search(
                    authorized_types, search_request
                )
            return self.service.search_response(
                request.base_url, total, resources, search_request
            )

    # -- Bulk -----------------------------------------------------------

    async def bulk(self, request: ScimRequest) -> ScimResponse:
        """Run a bulk request (:rfc:`RFC 7644 §3.7 <7644#section-3.7>`)."""
        with self.service.provider:
            self.service.route(request, Operation.bulk)
            plan = self.service.read_bulk(request.body, request.header("Content-Type"))
            for step in plan:
                result, resource = await self.run_bulk_step(request, plan, step)
                plan.record(step, result, resource)
            return self.service.bulk_response(plan)

    async def run_bulk_step(
        self, request: ScimRequest, plan: BulkPlan, step: BulkStep
    ) -> tuple[dict[str, Any], Resource[Any] | None]:
        """Apply one step of a bulk request.

        The step resolves the references of the operation, locates it and
        authorizes it. An operation that failed its validation then keeps its
        error.

        :return: The outcome of the step, and the resource it created or updated.
        """
        operation = plan.operation(step)
        outcome = self.service.bulk_outcome(operation)
        try:
            operation = plan.resolve(step)
            resource_type = self.service.locate_bulk_operation(
                request.base_url, operation, outcome
            )
            self.service.authorize_bulk_operation(request, operation, resource_type)
            failure = self.service.bulk_validation_failure(operation, outcome)
            if failure is not None:
                return failure, None
            async with self.storage.operation():
                resource = await self.apply_bulk_operation(
                    cast(ResourceType, resource_type), operation
                )
        except Exception as exception:
            return self.service.bulk_failure(outcome, exception), None
        return self.service.bulk_success(
            request.base_url, operation, outcome, resource
        ), resource

    async def apply_bulk_operation(
        self, resource_type: ResourceType, operation: BulkOperation[Resource[Any]]
    ) -> Resource[Any] | None:
        """Apply a validated bulk operation, and return the resource it acted on.

        The data of the operation is already validated, in the context of the
        request the operation stands for.
        """
        resource_id = self.service.check_bulk_target(operation)
        if resource_id is None:
            return await self.storage.create(
                resource_type, cast(Resource[Any], operation.data)
            )

        conditions = Conditions(if_match=operation.version)
        match operation.method:
            case BulkOperation.Method.put:
                return await self.replace_resource(
                    resource_type,
                    await self.storage.get(resource_type, resource_id),
                    cast(Resource[Any], operation.data),
                    conditions,
                )
            case BulkOperation.Method.patch:
                self.service.ensure_patch_supported()
                return await self.patch_resource(
                    resource_type,
                    resource_id,
                    cast("PatchOp[Any]", operation.data),
                    conditions,
                )
            case _:  # DELETE
                await self.delete_resource(resource_type, resource_id, conditions)
                return None

    # -- Discovery ------------------------------------------------------

    async def service_provider_config(self, request: ScimRequest) -> ScimResponse:
        """Serve the ServiceProviderConfig endpoint (:rfc:`RFC 7644 §4 <7644#section-4>`)."""
        with self.service.provider:
            self.service.route(request, Operation.service_provider_config)
            return self.service.service_provider_config(request.url, request.query)

    async def resource_types(self, request: ScimRequest) -> ScimResponse:
        """Serve the ResourceTypes endpoint (:rfc:`RFC 7644 §4 <7644#section-4>`)."""
        with self.service.provider:
            self.service.route(request, Operation.resource_types)
            return self.service.resource_types(request.url, request.query)

    async def resource_type(self, request: ScimRequest) -> ScimResponse:
        """Serve one resource type (:rfc:`RFC 7644 §4 <7644#section-4>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.resource_type)
            return self.service.resource_type(
                request.url, cast(str, target.resource_id), request.query
            )

    async def schemas(self, request: ScimRequest) -> ScimResponse:
        """Serve the Schemas endpoint (:rfc:`RFC 7644 §4 <7644#section-4>`)."""
        with self.service.provider:
            self.service.route(request, Operation.schemas)
            return self.service.schemas(request.url, request.query)

    async def schema(self, request: ScimRequest) -> ScimResponse:
        """Serve one schema (:rfc:`RFC 7644 §4 <7644#section-4>`)."""
        with self.service.provider:
            target = self.service.route(request, Operation.schema)
            return self.service.schema(
                request.url, cast(str, target.resource_id), request.query
            )
