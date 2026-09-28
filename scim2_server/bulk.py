from collections.abc import Callable
from typing import Any

from scim2_models import InvalidValueException
from scim2_models import Resource
from werkzeug.exceptions import Conflict

BULK_ID_PREFIX = "bulkId:"

Resolver = Callable[[Any], Any]
"""Replaces the bulkId references of a raw operation."""

OperationRunner = Callable[[Any, Resolver], tuple[dict[str, Any], Resource | None]]
"""Applies a raw operation once resolved, and returns its outcome and the resource it acted on."""


def raw_attribute(payload: Any, name: str) -> Any:
    """Return an attribute of a raw payload, whose names are case insensitive."""
    if not isinstance(payload, dict):
        return None
    return next(
        (value for key, value in payload.items() if key.casefold() == name.casefold()),
        None,
    )


def replace_bulk_ids(value: Any, replace: Callable[[str], str]) -> Any:
    """Replace every "bulkId:" reference of a raw value."""
    if isinstance(value, str) and value.startswith(BULK_ID_PREFIX):
        return replace(value.removeprefix(BULK_ID_PREFIX))
    if isinstance(value, list):
        return [replace_bulk_ids(item, replace) for item in value]
    if isinstance(value, dict):
        return {key: replace_bulk_ids(item, replace) for key, item in value.items()}
    return value


def resolve_operation(payload: Any, replace: Callable[[str], str]) -> Any:
    """Replace the "bulkId:" references of the path and the data of a raw bulk operation."""
    if not isinstance(payload, dict):
        return payload

    resolved = {}
    for key, value in payload.items():
        if key.casefold() == "path" and isinstance(value, str):
            value = "/".join(
                replace_bulk_ids(segment, replace) for segment in value.split("/")
            )
        elif key.casefold() == "data":
            value = replace_bulk_ids(value, replace)
        resolved[key] = value
    return resolved


class BulkJob:
    """Run the operations of a bulk request, and resolve their "bulkId:" references.

    RFC 7644 §3.7.2 lets an operation reference a resource that another POST
    of the same request creates. The operations run in the order of the
    request, except that a POST runs before the first operation that
    references it. The results keep the order of the request.
    """

    def __init__(
        self,
        operations: list[Any],
        fail_on_errors: int | None,
        run: OperationRunner,
    ):
        self.run_resolved = run
        self.operations = operations
        self.fail_on_errors = fail_on_errors
        self.results: dict[int, dict[str, Any]] = {}
        self.created: dict[str, Resource] = {}
        self.running: set[int] = set()
        self.errors = 0

        self.creations: dict[str, int] = {}
        for index, payload in enumerate(operations):
            bulk_id = raw_attribute(payload, "bulkId")
            if raw_attribute(payload, "method") == "POST" and isinstance(bulk_id, str):
                self.creations.setdefault(bulk_id, index)

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

        payload = self.operations[index]
        self.running.add(index)
        for bulk_id in self.references(payload):
            if bulk_id in self.creations:
                self.run_operation(self.creations[bulk_id])

        if not self.stopped:
            result, resource = self.run_resolved(
                payload, lambda payload: self.resolve(index, payload)
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
    def references(payload: Any) -> list[str]:
        """Return the bulkIds an operation references."""
        bulk_ids: list[str] = []

        def collect(bulk_id: str) -> str:
            bulk_ids.append(bulk_id)
            return bulk_id

        resolve_operation(payload, collect)
        return bulk_ids

    def resolve(self, index: int, payload: Any) -> Any:
        """Replace the bulkId references of an operation with the identifiers of the created resources.

        :raises Conflict: When a referenced resource was not created, as
            RFC 7644 §3.7.1 allows for circular references.
        """
        bulk_id = raw_attribute(payload, "bulkId")
        if (
            raw_attribute(payload, "method") == "POST"
            and isinstance(bulk_id, str)
            and not self.is_creation(index, bulk_id)
        ):
            raise InvalidValueException(
                detail=f"The bulkId {bulk_id} is not unique in the request"
            )

        def replace(bulk_id: str) -> str:
            if bulk_id in self.created:
                return str(self.created[bulk_id].id)
            if self.creations.get(bulk_id) in self.running:
                raise Conflict(f"The bulkId {bulk_id} is part of a circular reference")
            raise Conflict(f"No resource was created with the bulkId {bulk_id}")

        return resolve_operation(payload, replace)
