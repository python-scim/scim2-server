from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from scim2_models import BulkOperation
from scim2_models import InvalidValueException
from scim2_models import Resource
from werkzeug.exceptions import Conflict

BULK_ID_PREFIX = "bulkId:"

Resolver = Callable[[BulkOperation[Resource[Any]]], BulkOperation[Resource[Any]]]
"""Replaces the bulkId references of an operation."""

OperationRunner = Callable[
    [BulkOperation[Resource[Any]], Resolver],
    tuple[dict[str, Any], Resource[Any] | None],
]
"""Applies an operation once resolved, and returns its outcome and the resource it acted on."""


def replace_bulk_ids(value: Any, replace: Callable[[str], str]) -> Any:
    """Replace every "bulkId:" reference of a value.

    A value without reference is returned as it is. Models are copied with
    only the changed fields, so the fields the client set stay the same.
    """
    if isinstance(value, str) and value.startswith(BULK_ID_PREFIX):
        return replace(value.removeprefix(BULK_ID_PREFIX))

    if isinstance(value, list):
        items = [replace_bulk_ids(item, replace) for item in value]
        changed = any(new is not old for new, old in zip(items, value, strict=True))
        return items if changed else value

    if isinstance(value, dict):
        entries = {key: replace_bulk_ids(item, replace) for key, item in value.items()}
        changed = any(entries[key] is not item for key, item in value.items())
        return entries if changed else value

    if isinstance(value, BaseModel):
        updates = {}
        for name in type(value).model_fields:
            field = getattr(value, name)
            replaced = replace_bulk_ids(field, replace)
            if replaced is not field:
                updates[name] = replaced
        return value.model_copy(update=updates) if updates else value

    return value


def resolve_operation(
    operation: BulkOperation[Resource[Any]], replace: Callable[[str], str]
) -> BulkOperation[Resource[Any]]:
    """Replace the "bulkId:" references of the path and the data of a bulk operation."""
    updates: dict[str, Any] = {}
    if operation.path is not None:
        path = "/".join(
            replace_bulk_ids(segment, replace) for segment in operation.path.split("/")
        )
        if path != operation.path:
            updates["path"] = path

    data = replace_bulk_ids(operation.data, replace)
    if data is not operation.data:
        updates["data"] = data

    return operation.model_copy(update=updates) if updates else operation


class BulkJob:
    """Run the operations of a bulk request, and resolve their "bulkId:" references.

    RFC 7644 §3.7.2 lets an operation reference a resource that another POST
    of the same request creates. The operations run in the order of the
    request, except that a POST runs before the first operation that
    references it. The results keep the order of the request.
    """

    def __init__(
        self,
        operations: list[BulkOperation[Resource[Any]]],
        fail_on_errors: int | None,
        run: OperationRunner,
    ):
        self.run_resolved = run
        self.operations = operations
        self.fail_on_errors = fail_on_errors
        self.results: dict[int, dict[str, Any]] = {}
        self.created: dict[str, Resource[Any]] = {}
        self.running: set[int] = set()
        self.errors = 0

        self.creations: dict[str, int] = {}
        for index, operation in enumerate(operations):
            if (
                operation.method == BulkOperation.Method.post
                and operation.bulk_id is not None
            ):
                self.creations.setdefault(operation.bulk_id, index)

    @property
    def stopped(self) -> bool:
        """Whether the job reached the number of errors the client accepts.

        RFC 7644 §3.7.3: the job goes on despite failures, unless the client
        caps the errors it accepts with "failOnErrors".
        """
        return (
            self.errors > 0
            and self.fail_on_errors is not None
            and self.errors >= self.fail_on_errors
        )

    def run(self) -> list[dict[str, Any]]:
        """Run every operation, and return the results of the operations that ran."""
        for index in range(len(self.operations)):
            self.run_operation(index)
        return [self.results[index] for index in sorted(self.results)]

    def run_operation(self, index: int) -> None:
        """Run an operation, after the creations it references."""
        if index in self.results or index in self.running or self.stopped:
            return

        operation = self.operations[index]
        self.running.add(index)
        for bulk_id in self.references(operation):
            if bulk_id in self.creations:
                self.run_operation(self.creations[bulk_id])

        if not self.stopped:
            result, resource = self.run_resolved(
                operation, lambda operation: self.resolve(index, operation)
            )
            self.results[index] = result
            if result["status"] >= 400:
                self.errors += 1
            elif resource is not None and self.is_creation(index, result["bulk_id"]):
                self.created[result["bulk_id"]] = resource
        self.running.discard(index)

    def is_creation(self, index: int, bulk_id: str | None) -> bool:
        """Whether an operation is the creation a bulkId references."""
        return bulk_id is not None and self.creations.get(bulk_id) == index

    @staticmethod
    def references(operation: BulkOperation[Resource[Any]]) -> list[str]:
        """Return the bulkIds an operation references."""
        bulk_ids: list[str] = []

        def collect(bulk_id: str) -> str:
            bulk_ids.append(bulk_id)
            return bulk_id

        resolve_operation(operation, collect)
        return bulk_ids

    def resolve(
        self, index: int, operation: BulkOperation[Resource[Any]]
    ) -> BulkOperation[Resource[Any]]:
        """Replace the bulkId references of an operation with the identifiers of the created resources.

        :raises Conflict: When a referenced resource was not created, as
            RFC 7644 §3.7.1 allows for circular references.
        """
        if (
            operation.method == BulkOperation.Method.post
            and operation.bulk_id is not None
            and not self.is_creation(index, operation.bulk_id)
        ):
            raise InvalidValueException(
                detail=f"The bulkId {operation.bulk_id} is not unique in the request"
            )

        def replace(bulk_id: str) -> str:
            if bulk_id in self.created:
                return str(self.created[bulk_id].id)
            if self.creations.get(bulk_id) in self.running:
                raise Conflict(f"The bulkId {bulk_id} is part of a circular reference")
            raise Conflict(f"No resource was created with the bulkId {bulk_id}")

        return resolve_operation(operation, replace)
