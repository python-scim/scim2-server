from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from scim2_models import BulkOperation
from scim2_models import ConflictException
from scim2_models import InvalidValueException
from scim2_models import Resource

BULK_ID_PREFIX = "bulkId:"


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


@dataclass(frozen=True)
class BulkStep:
    """One step of a bulk plan.

    A step produces the result of one operation of the request. A step is not
    an operation, so that a plan can later split an operation in several
    steps, for instance to resolve circular references.
    """

    index: int
    """The position, in the request, of the operation whose result the step produces."""


class BulkPlan:
    """The order of the operations of a bulk request, and the "bulkId:" references between them.

    RFC 7644 §3.7.2 lets an operation reference a resource that another POST
    of the same request creates. The operations run in the order of the
    request, except that a POST runs before the first operation that
    references it. The results keep the order of the request.

    A plan does no input or output. The code running the request iterates
    over the steps, resolves the references of each one, applies it, and
    records its outcome.
    """

    def __init__(
        self,
        operations: list[BulkOperation[Resource[Any]]],
        fail_on_errors: int | None,
    ):
        self.operations = operations
        self.fail_on_errors = fail_on_errors
        self.results: dict[int, dict[str, Any]] = {}
        self.created: dict[str, Resource[Any]] = {}
        self.errors = 0

        self.creations: dict[str, int] = {}
        for index, operation in enumerate(operations):
            if (
                operation.method == BulkOperation.Method.post
                and operation.bulk_id is not None
            ):
                self.creations.setdefault(operation.bulk_id, index)

        self.steps = self.plan_steps()

    def plan_steps(self) -> list[BulkStep]:
        """Order the operations so that a creation comes before the operations referencing it.

        The order comes from a depth-first walk of the references, without
        recursion, so a long chain of references cannot exhaust the stack.
        A circular reference leaves the referencing operation before the
        creation it references, and :meth:`resolve` then refuses it.
        """
        order: list[BulkStep] = []
        visited: set[int] = set()
        for root in range(len(self.operations)):
            if root in visited:
                continue
            on_path = {root}
            stack = [(root, iter(self.dependencies(root)))]
            while stack:
                index, dependencies = stack[-1]
                dependency = next(dependencies, None)
                if dependency is None:
                    stack.pop()
                    on_path.discard(index)
                    visited.add(index)
                    order.append(BulkStep(index))
                elif dependency not in visited and dependency not in on_path:
                    on_path.add(dependency)
                    stack.append((dependency, iter(self.dependencies(dependency))))
        return order

    def dependencies(self, index: int) -> list[int]:
        """Return the positions of the creations an operation references."""
        return [
            self.creations[bulk_id]
            for bulk_id in self.references(self.operations[index])
            if bulk_id in self.creations
        ]

    @property
    def stopped(self) -> bool:
        """Whether the request reached the number of errors the client accepts.

        RFC 7644 §3.7.3: the request goes on despite failures, unless the
        client caps the errors it accepts with "failOnErrors".
        """
        return (
            self.errors > 0
            and self.fail_on_errors is not None
            and self.errors >= self.fail_on_errors
        )

    def __iter__(self) -> Iterator[BulkStep]:
        """Yield the steps to run, until the request is stopped."""
        for step in self.steps:
            if self.stopped:
                return
            yield step

    def operation(self, step: BulkStep) -> BulkOperation[Resource[Any]]:
        """Return the operation of a step, as the client sent it."""
        return self.operations[step.index]

    def record(
        self, step: BulkStep, result: dict[str, Any], resource: Resource[Any] | None
    ) -> None:
        """Record the outcome of a step, and the resource it created or updated."""
        self.results[step.index] = result
        if result["status"] >= 400:
            self.errors += 1
        elif resource is not None and self.is_creation(step.index, result["bulk_id"]):
            self.created[result["bulk_id"]] = resource

    def outcomes(self) -> list[dict[str, Any]]:
        """Return the outcome of the operations that ran, in the order of the request."""
        return [self.results[index] for index in sorted(self.results)]

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

    def resolve(self, step: BulkStep) -> BulkOperation[Resource[Any]]:
        """Replace the bulkId references of the operation of a step with the identifiers of the created resources.

        :raises ~scim2_models.InvalidValueException: When a POST reuses the
            bulkId of an earlier POST.
        :raises ~scim2_models.ConflictException: When a referenced resource
            was not created, as RFC 7644 §3.7.1 allows for circular references.
        """
        operation = self.operation(step)
        if (
            operation.method == BulkOperation.Method.post
            and operation.bulk_id is not None
            and not self.is_creation(step.index, operation.bulk_id)
        ):
            raise InvalidValueException(
                detail=f"The bulkId {operation.bulk_id} is not unique in the request"
            )

        def replace(bulk_id: str) -> str:
            if bulk_id in self.created:
                return str(self.created[bulk_id].id)
            creation = self.creations.get(bulk_id)
            if creation is not None and creation not in self.results:
                raise ConflictException(
                    detail=f"The bulkId {bulk_id} is part of a circular reference"
                )
            raise ConflictException(
                detail=f"No resource was created with the bulkId {bulk_id}"
            )

        return resolve_operation(operation, replace)
