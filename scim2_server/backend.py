import datetime
import pickle
import uuid
from threading import Lock
from types import TracebackType
from typing import Any
from typing import Self
from typing import Union
from typing import cast

from scim2_models import AttributeBinding
from scim2_models import Meta
from scim2_models import Path
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ScimFilter
from scim2_models import SearchRequest
from scim2_models import Uniqueness
from scim2_models import UniquenessException
from werkzeug.http import generate_etag

from scim2_server.utils import parametrize


class Backend:
    """The base class for a SCIM provider backend.

    A backend only stores resources: what the service serves is described by the
    :class:`~scim2_models.ScimProvider` of the application.
    """

    def __enter__(self) -> Self:
        """Allow the backend to be used as a context manager.

        This enables support for transactions.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the transaction."""
        pass

    def query_resources(
        self,
        search_request: SearchRequest[Any],
        resource_type: ResourceType | None = None,
    ) -> tuple[int, list[Resource[Any]]]:
        """Query the backend for a set of resources.

        :param search_request: SearchRequest instance describing the
            query.
        :param resource_type: The resource type to query. If None, all
            resource types are queried.
        :return: A tuple of "total results" and a List of found
            Resources. The List must contain a copy of resources.
            Mutating elements in the List must not modify the data
            stored in the backend.
        :raises TooManyException: If the backend only supports querying
            for one resource type at a time, setting resource_type to
            None the backend may raise TooManyException.
        """
        raise NotImplementedError

    def get_resource(
        self, resource_type: ResourceType, object_id: str
    ) -> Resource[Any] | None:
        """Query the backend for a resources by its ID.

        :param resource_type: The resource type to get the object from.
        :param object_id: ID of the object to get.
        :return: The resource object if it exists, None otherwise. The
            resource must be a copy, modifying it must not change the
            data stored in the backend.
        """
        raise NotImplementedError

    def delete_resource(self, resource_type: ResourceType, object_id: str) -> bool:
        """Delete a resource.

        :param resource_type: The resource type to delete the object
            from.
        :param object_id: ID of the object to delete.
        :return: True if the resource was deleted, False otherwise.
        """
        raise NotImplementedError

    def create_resource(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        """Create a resource.

        :param resource_type: The resource type to create.
        :param resource: Resource to create.
        :return: The created resource. Creation should set system-
            defined attributes (ID, Metadata). May be the same object
            that is passed in. A relative ``meta.location`` is relative to
            the root of the application.
        """
        raise NotImplementedError

    def update_resource(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any] | None:
        """Update a resource. The resource is identified by its ID.

        :param resource_type: The resource type to update.
        :param resource: Resource to update.
        :return: The updated resource, or None if no resource has its ID.
            Updating should update the "meta.lastModified" data. May be
            the same object that is passed in.
        """
        raise NotImplementedError


class InMemoryBackend(Backend):
    """An example in-memory backend for the SCIM provider.

    It is not optimized for performance. Many operations are O(n) or
    worse, whereas they would perform better with an actual production
    database in the backend. This is intentional to keep the
    implementation simple.
    """

    def __init__(self) -> None:
        super().__init__()
        self.resources: list[Resource[Any]] = []
        self.lock: Lock = Lock()

    def __enter__(self) -> Self:
        """See super docs.

        The InMemoryBackend uses a simple Lock to synchronize all
        access.
        """
        super().__enter__()
        self.lock.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        super().__exit__(exc_type, exc_val, exc_tb)
        self.lock.release()

    def query_resources(
        self,
        search_request: SearchRequest[Any],
        resource_type: ResourceType | None = None,
    ) -> tuple[int, list[Resource[Any]]]:
        start_index = (search_request.start_index or 1) - 1

        candidates = [
            r
            for r in self.resources
            if resource_type is None or self._is_of_type(r, resource_type)
        ]

        scim_filter = search_request.filter
        if scim_filter is not None and not scim_filter.models and candidates:
            models = tuple(dict.fromkeys(type(r) for r in candidates))
            scim_filter = parametrize(ScimFilter, Union[models])(str(scim_filter))  # noqa: UP007

        found_resources = [
            r for r in candidates if scim_filter is None or scim_filter.match(r)
        ]

        found_resources = search_request.sort(found_resources)

        total_results = len(found_resources)
        found_resources = found_resources[start_index:]
        if search_request.count is not None:
            found_resources = found_resources[: search_request.count]
        return total_results, found_resources

    def _is_of_type(self, resource: Resource[Any], resource_type: ResourceType) -> bool:
        """Tell whether a resource belongs to a resource type.

        RFC 7643 §3.1 has meta.resourceType carry the name of the resource type,
        which may differ from its id.
        """
        assert resource.meta is not None
        return resource.meta.resource_type == resource_type.name

    def _get_resource_idx(
        self, resource_type: ResourceType, object_id: str | None
    ) -> int | None:
        return next(
            (
                idx
                for idx, r in enumerate(self.resources)
                if self._is_of_type(r, resource_type) and r.id == object_id
            ),
            None,
        )

    def get_resource(
        self, resource_type: ResourceType, object_id: str
    ) -> Resource[Any] | None:
        resource_dict_idx = self._get_resource_idx(resource_type, object_id)
        if resource_dict_idx is not None:
            return self.resources[resource_dict_idx].model_copy(deep=True)
        return None

    def delete_resource(self, resource_type: ResourceType, object_id: str) -> bool:
        found = self.get_resource(resource_type, object_id)
        if found:
            self.resources = [
                r
                for r in self.resources
                if not (self._is_of_type(r, resource_type) and r.id == object_id)
            ]
            return True
        return False

    def create_resource(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        resource = resource.model_copy(deep=True)
        resource.id = uuid.uuid4().hex
        utcnow = datetime.datetime.now(datetime.UTC)
        assert resource_type.endpoint is not None
        resource.meta = Meta(
            resource_type=resource_type.name,
            created=utcnow,
            last_modified=utcnow,
            location=f"v2/{resource_type.endpoint.strip('/')}/{resource.id}",
        )
        self._touch_resource(resource, utcnow)
        self._check_uniqueness(resource)
        self.resources.append(resource)
        return resource

    def _check_uniqueness(self, resource: Resource[Any]) -> None:
        """Refuse a resource sharing a unique value with another one of the same schema.

        RFC 7643 erratum 8279 scopes the uniqueness to the resources using the
        schema that declares the attribute, whatever their resource type. A
        missing value never clashes, as a SQL NULL does not.
        """
        unique_paths = parametrize(Path, type(resource)).iter_paths(
            include_subattributes=False,
            uniqueness=[Uniqueness.server, Uniqueness.global_],
        )
        for path in unique_paths:
            attribute = cast(AttributeBinding, path.resolve())
            value = self._unique_value(resource, attribute)
            if value is None:
                continue
            for existing_resource in self.resources:
                if (
                    existing_resource.id != resource.id
                    and self._unique_value(existing_resource, attribute) == value
                ):
                    raise UniquenessException()

    @staticmethod
    def _unique_value(resource: Resource[Any], attribute: AttributeBinding) -> Any:
        """Return the value a resource holds for a unique attribute, in the form it is compared in.

        A resource whose schemas do not declare the attribute holds no value.
        """
        value = parametrize(Path, type(resource))(attribute.urn).get(
            resource, strict=False
        )
        if isinstance(value, str) and not attribute.case_exact:
            return value.casefold()
        return value

    @staticmethod
    def _touch_resource(
        resource: Resource[Any], last_modified: datetime.datetime
    ) -> None:
        """Touches a resource (updates last_modified and version).

        Version is generated by hashing last_modified. Another option
        would be to hash the entire resource instead.
        """
        assert resource.meta is not None
        resource.meta.last_modified = last_modified
        etag = generate_etag(pickle.dumps(resource.meta.last_modified))
        resource.meta.version = f'W/"{etag}"'

    def update_resource(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any] | None:
        found_res_idx = self._get_resource_idx(resource_type, resource.id)
        if found_res_idx is not None:
            updated_resource = type(resource).model_validate(resource.model_dump())
            self._touch_resource(updated_resource, datetime.datetime.now(datetime.UTC))

            self._check_uniqueness(updated_resource)
            self.resources[found_res_idx] = updated_resource
            return updated_resource
        return None
