import datetime
import itertools
import uuid
from collections.abc import Callable
from collections.abc import Generator
from contextlib import contextmanager
from threading import RLock
from typing import Any
from typing import Union
from typing import cast

from scim2_models import AttributeBinding
from scim2_models import InvalidCursorException
from scim2_models import Meta
from scim2_models import NotFoundException
from scim2_models import Path
from scim2_models import PreconditionFailedException
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ResponseParameters
from scim2_models import ScimFilter
from scim2_models import SearchRequest
from scim2_models import Uniqueness
from scim2_models import UniquenessException
from scim2_models.path import CompareOperator
from scim2_models.path import Comparison

from scim2_server.storage import AsyncScimStorage
from scim2_server.storage import ScimStorage
from scim2_server.storage import SearchPage
from scim2_server.storage import projection
from scim2_server.storage import search_page
from scim2_server.utils import parametrize


def utcnow() -> datetime.datetime:
    """Return the current date, in UTC."""
    return datetime.datetime.now(datetime.UTC)


def _page(
    search_request: SearchRequest[Any], position: Any, resources: list[Resource[Any]]
) -> SearchPage:
    """Sort every matching resource by its sort value then its identifier, and return the page a search asks for.

    A cursor position holds the sort value and the identifier of the resource
    the page starts after, or ends before, so the pages stay stable.
    """
    descending = (
        search_request.sort_by is not None
        and search_request.sort_order == SearchRequest.SortOrder.descending
    )
    pairs = sorted(
        ((_sort_key(search_request, resource), resource) for resource in resources),
        key=lambda pair: pair[0],
        reverse=descending,
    )
    found = [resource for _, resource in pairs]
    if search_request.cursor is None:
        start = search_request.start_index_0 or 0
        stop = None if search_request.count is None else start + search_request.count
        return SearchPage(len(found), found[start:stop])

    if search_request.count == 0:
        return SearchPage(len(found), [])

    start, stop = _bounds(
        [key for key, _ in pairs], position, search_request.count, descending
    )
    page = SearchPage(len(found), found[start:stop])
    if page.resources and stop < len(found):
        page.next = _position("next", pairs[stop - 1][0])
    if page.resources and start > 0:
        page.previous = _position("previous", pairs[start][0])
    return page


SortKey = tuple[bool, Any, str]


def _sort_key(search_request: SearchRequest[Any], resource: Resource[Any]) -> SortKey:
    """Return the key that orders a resource: its sort value, missing values last, then its identifier."""
    value = search_request.sort_value(resource)
    return value is None, value if value is not None else "", cast(str, resource.id)


def _bounds(
    keys: list[SortKey], position: Any, count: int | None, descending: bool
) -> tuple[int, int]:
    """Return the slice of the sorted keys that a cursor page covers."""
    if position is None:
        return 0, len(keys) if count is None else min(count, len(keys))

    direction, anchor = _read_position(position)

    def before(key: SortKey) -> bool:
        return key > anchor if descending else key < anchor

    if direction == "next":
        start = sum(1 for key in keys if before(key) or key == anchor)
        stop = len(keys) if count is None else min(start + count, len(keys))
        return start, stop

    stop = sum(1 for key in keys if before(key))
    start = 0 if count is None else max(stop - count, 0)
    return start, stop


def _position(direction: str, key: SortKey) -> dict[str, Any]:
    """Return the position of the page that starts after, or ends before, a sort key."""
    missing, value, identifier = key
    return {"d": direction, "k": None if missing else _encode(value), "i": identifier}


def _read_position(position: Any) -> tuple[str, SortKey]:
    """Return the direction and the sort key of a position that _page gave."""
    try:
        direction, value, identifier = position["d"], position["k"], position["i"]
    except (TypeError, KeyError):
        raise InvalidCursorException from None
    if direction not in ("next", "previous") or not isinstance(identifier, str):
        raise InvalidCursorException
    if value is None:
        return direction, (True, "", identifier)
    return direction, (False, _decode(value), identifier)


def _encode(value: Any) -> Any:
    """Return a sort value in a form JSON can hold."""
    if isinstance(value, datetime.datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, str | bool | int | float):
        return value
    raise TypeError(f"A cursor cannot hold a sort value of type {type(value).__name__}")


def _decode(value: Any) -> Any:
    """Return the sort value that _encode gave."""
    if not isinstance(value, dict):
        return value
    try:
        return datetime.datetime.fromisoformat(value["datetime"])
    except (KeyError, TypeError, ValueError):
        raise InvalidCursorException from None


Key = tuple[str | None, str]


def _key(resource: Resource[Any]) -> Key:
    """Return the resource type and the identifier a resource is stored under.

    Per RFC 7643 §3.1, meta.resourceType holds the name of the resource
    type, which may differ from its id.
    """
    assert resource.meta is not None
    return resource.meta.resource_type, cast(str, resource.id)


def _equality(scim_filter: ScimFilter[Any] | None) -> AttributeBinding | None:
    """Return the attribute a filter compares to a string with eq, if it holds a single string."""
    if scim_filter is None or scim_filter.model is None:
        return None
    node = scim_filter.ast
    if (
        not isinstance(node, Comparison)
        or node.op != CompareOperator.eq
        or not isinstance(node.value, str)
    ):
        return None
    binding = scim_filter.resolve_comparison(node.attr_path, strict=False)
    if binding is None or binding.is_multivalued or binding.target_type is not str:
        return None
    return binding


def _hashable(value: Any) -> Any:
    """Return a form of a value that a dict can hold as a key.

    Lists and models cannot be hashed, so they are held by their
    representation.
    """
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


class InMemoryStorage(ScimStorage):
    """A storage keeping the resources in memory, for tests, debugging and demos.

    It indexes the resources by identifier and the unique values. A search
    walks every resource of the searched types, unless it compares a single
    string attribute to a string with ``eq``. A lock serializes every access,
    so it can serve a threaded server.

    It indexes the values under the policy in effect when they were written,
    so it serves the resources of a single policy.

    :param clock: Return the date of a write, for ``meta.created`` and
        ``meta.lastModified``. Pass a fixed clock to get predictable dates.
    """

    supports_cursors = True

    def __init__(self, clock: Callable[[], datetime.datetime] = utcnow) -> None:
        self._resources: dict[str | None, dict[str, Resource[Any]]] = {}
        self._owners: dict[str, dict[Any, Key]] = {}
        self._unique_values: dict[Key, dict[str, Any]] = {}
        self._equalities: dict[
            str | None, dict[AttributeBinding, dict[Any, set[str]]]
        ] = {}
        self._equal_values: dict[Key, dict[AttributeBinding, Any]] = {}
        self.clock = clock
        self.lock = RLock()
        self.versions = itertools.count(1)

    @property
    def resources(self) -> list[Resource[Any]]:
        """The stored resources."""
        return [
            resource
            for resources in self._resources.values()
            for resource in resources.values()
        ]

    @resources.setter
    def resources(self, resources: list[Resource[Any]]) -> None:
        self._resources = {}
        self._owners = {}
        self._unique_values = {}
        self._equalities = {}
        self._equal_values = {}
        for resource in resources:
            self._store(resource, self._values_of(resource))

    def generate_id(self, resource_type: ResourceType, resource: Resource[Any]) -> str:
        """Return the identifier of a new resource.

        Override this method to get predictable identifiers.
        """
        return uuid.uuid4().hex

    def next_version(self) -> str:
        """Return the version of a new write.

        Versions come from a counter, so two writes never share a version,
        even within the same microsecond.
        """
        return f'W/"{next(self.versions)}"'

    @contextmanager
    def operation(self) -> Generator[None]:
        """Hold the lock for the whole operation."""
        with self.lock:
            yield

    def get(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        response_parameters: ResponseParameters[Any] | None = None,
    ) -> Resource[Any]:
        with self.lock:
            return self._stored(resource_type, resource_id).model_copy(deep=True)

    def search(
        self,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
        *,
        position: Any = None,
    ) -> SearchPage:
        with self.lock:
            names = list(dict.fromkeys(rt.name for rt in resource_types))
            scim_filter = search_request.filter
            candidates = self._candidates(names, scim_filter)

            if scim_filter is not None and not scim_filter.models and candidates:
                models = tuple(dict.fromkeys(type(r) for r in candidates))
                scim_filter = parametrize(ScimFilter, Union[models])(str(scim_filter))  # noqa: UP007

            found = [
                r for r in candidates if scim_filter is None or scim_filter.match(r)
            ]
            page = _page(search_request, position, found)
            page.resources = [r.model_copy(deep=True) for r in page.resources]
            return page

    def create(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        with self.lock:
            resource = resource.model_copy(deep=True)
            resource.id = self.generate_id(resource_type, resource)
            now = self.clock()
            resource.meta = Meta(
                resource_type=resource_type.name,
                created=now,
                last_modified=now,
                version=self.next_version(),
            )
            values = self._values_of(resource)
            self._check_uniqueness(resource, values)
            self._store(resource, values)
            return resource.model_copy(deep=True)

    def update(
        self,
        resource_type: ResourceType,
        resource: Resource[Any],
        *,
        expected_version: str | None = None,
    ) -> Resource[Any]:
        with self.lock:
            stored = self._stored(resource_type, resource.id)
            self._check_version(stored, expected_version)

            updated = type(resource).model_validate(resource.model_dump())
            assert stored.meta is not None
            updated.meta = stored.meta.model_copy(
                update={
                    "location": None,
                    "last_modified": self.clock(),
                    "version": self.next_version(),
                }
            )
            values = self._values_of(updated)
            self._check_uniqueness(updated, values)
            self._store(updated, values)
            return updated.model_copy(deep=True)

    def delete(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        expected_version: str | None = None,
    ) -> None:
        with self.lock:
            stored = self._stored(resource_type, resource_id)
            self._check_version(stored, expected_version)
            self._remove(_key(stored))

    def _candidates(
        self, names: list[str | None], scim_filter: ScimFilter[Any] | None
    ) -> list[Resource[Any]]:
        """Return the resources a search walks.

        A search on a single resource type, comparing a single string
        attribute to a string with eq, walks only the resources holding an
        equal value. The values are indexed on the first such search.
        """
        binding = _equality(scim_filter) if len(names) == 1 else None
        if binding is None:
            return [
                resource
                for name in names
                for resource in self._resources.get(name, {}).values()
            ]

        node = cast(Comparison, cast(ScimFilter[Any], scim_filter).ast)
        try:
            value = binding.comparable(node.value)
        except ValueError:
            return []
        resources = self._resources.get(names[0], {})
        ids = self._equality_index(names[0], binding).get(value, set())
        return [resources[resource_id] for resource_id in ids]

    def _equality_index(
        self, name: str | None, binding: AttributeBinding
    ) -> dict[Any, set[str]]:
        """Return the identifiers of the resources of a type, by the value of an attribute."""
        indexes = self._equalities.setdefault(name, {})
        if binding not in indexes:
            indexes[binding] = {}
            for resource in self._resources.get(name, {}).values():
                self._index_equality(resource, binding)
        return indexes[binding]

    def _index_equality(
        self, resource: Resource[Any], binding: AttributeBinding
    ) -> None:
        key = _key(resource)
        value = self._comparable_value(resource, binding)
        self._equal_values.setdefault(key, {})[binding] = value
        self._equalities[key[0]][binding].setdefault(value, set()).add(key[1])

    def _store(self, resource: Resource[Any], values: dict[str, Any]) -> None:
        """Keep a resource under its resource type and its identifier, and index its values."""
        key = _key(resource)
        self._remove(key)
        self._resources.setdefault(key[0], {})[key[1]] = resource
        self._unique_values[key] = values
        for urn, value in values.items():
            self._owners.setdefault(urn, {})[value] = key
        for binding in self._equalities.get(key[0], {}):
            self._index_equality(resource, binding)

    def _remove(self, key: Key) -> None:
        """Forget a resource and its indexed values."""
        self._resources.get(key[0], {}).pop(key[1], None)
        for urn, value in self._unique_values.pop(key, {}).items():
            del self._owners[urn][value]
        for binding, value in self._equal_values.pop(key, {}).items():
            self._equalities[key[0]][binding][value].discard(key[1])

    def _stored(
        self, resource_type: ResourceType, resource_id: str | None
    ) -> Resource[Any]:
        try:
            return self._resources[resource_type.name][cast(str, resource_id)]
        except KeyError:
            raise NotFoundException(
                detail=f"{resource_type.name} {resource_id!r} not found"
            ) from None

    @staticmethod
    def _check_version(resource: Resource[Any], expected_version: str | None) -> None:
        assert resource.meta is not None
        if expected_version is not None and resource.meta.version != expected_version:
            raise PreconditionFailedException

    def _check_uniqueness(
        self, resource: Resource[Any], values: dict[str, Any]
    ) -> None:
        """Refuse a resource sharing a unique value with another resource."""
        key = _key(resource)
        for urn, value in values.items():
            if self._owners.get(urn, {}).get(value, key) != key:
                raise UniquenessException()

    def _values_of(self, resource: Resource[Any]) -> dict[str, Any]:
        """Return the unique values of a resource, by the URN of their attribute.

        Per RFC 7643 erratum 8279, the uniqueness applies to the resources
        using the schema that declares the attribute, whatever their resource
        type. The URN of an attribute holds its schema, so two resources clash
        when they hold the same value under the same URN. A missing value
        never clashes, as a SQL NULL does not.

        The values are compared under the policy of the write that stored
        them, so a storage serves the resources of a single policy.
        """
        unique_paths = parametrize(Path, type(resource)).iter_paths(
            include_subattributes=False,
            uniqueness=[Uniqueness.server, Uniqueness.global_],
        )
        values = {}
        for path in unique_paths:
            attribute = cast(AttributeBinding, path.resolve())
            value = self._comparable_value(resource, attribute)
            if value is not None:
                values[attribute.urn] = _hashable(value)
        return values

    @staticmethod
    def _comparable_value(resource: Resource[Any], attribute: AttributeBinding) -> Any:
        """Return the value a resource holds for an attribute, in the form it is compared in.

        A resource whose schemas do not declare the attribute holds no value.
        A string the policy cannot compare is equal to no other value, so it
        is held as missing too.
        """
        value = parametrize(Path, type(resource))(attribute.urn).get(
            resource, strict=False
        )
        try:
            return attribute.comparable(value)
        except ValueError:
            return None


class AsyncInMemoryStorage(AsyncScimStorage):
    """The asynchronous variant of :class:`InMemoryStorage`.

    It serves the resources of an :class:`InMemoryStorage`. Every call runs
    without awaiting anything, so a call is never interrupted by another
    coroutine. :meth:`~scim2_server.storage.AsyncScimStorage.operation` takes no lock: holding the lock of the
    storage across an ``await`` would block the event loop. Two concurrent
    updates of a resource are still told apart by ``expected_version``.

    :param storage: The storage to serve. Pass a subclass of
        :class:`InMemoryStorage` to change how identifiers are generated.
    """

    supports_cursors = True

    def __init__(self, storage: InMemoryStorage | None = None) -> None:
        self.storage = storage if storage is not None else InMemoryStorage()

    @property
    def resources(self) -> list[Resource[Any]]:
        """The stored resources."""
        return self.storage.resources

    async def get(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        response_parameters: ResponseParameters[Any] | None = None,
    ) -> Resource[Any]:
        return self.storage.get(
            resource_type,
            resource_id,
            **projection(self.storage, response_parameters),
        )

    async def search(
        self,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
        *,
        position: Any = None,
    ) -> SearchPage:
        return search_page(self.storage, resource_types, search_request, position)

    async def create(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        return self.storage.create(resource_type, resource)

    async def update(
        self,
        resource_type: ResourceType,
        resource: Resource[Any],
        *,
        expected_version: str | None = None,
    ) -> Resource[Any]:
        return self.storage.update(
            resource_type, resource, expected_version=expected_version
        )

    async def delete(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        expected_version: str | None = None,
    ) -> None:
        self.storage.delete(
            resource_type, resource_id, expected_version=expected_version
        )
