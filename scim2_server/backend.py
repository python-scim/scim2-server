import dataclasses
import datetime
import pickle
import uuid
from inspect import isclass
from threading import Lock
from typing import Any
from typing import Union

from scim2_models import BaseModel
from scim2_models import CaseExact
from scim2_models import Extension
from scim2_models import Meta
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ScimFilter
from scim2_models import SearchRequest
from scim2_models import Uniqueness
from scim2_models import UniquenessException
from werkzeug.http import generate_etag


class Backend:
    """The base class for a SCIM provider backend.

    A backend only stores resources: what the service serves is described by the
    :class:`~scim2_models.ScimProvider` of the application.
    """

    def __enter__(self):
        """Allow the backend to be used as a context manager.

        This enables support for transactions.
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the transaction."""
        pass

    def query_resources(
        self,
        search_request: SearchRequest,
        resource_type: ResourceType | None = None,
    ) -> tuple[int, list[Resource]]:
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
    ) -> Resource | None:
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
        self, resource_type: ResourceType, resource: Resource
    ) -> Resource | None:
        """Create a resource.

        :param resource_type: The resource type to create.
        :param resource: Resource to create.
        :return: The created resource. Creation should set system-
            defined attributes (ID, Metadata). May be the same object
            that is passed in.
        """
        raise NotImplementedError

    def update_resource(
        self, resource_type: ResourceType, resource: Resource
    ) -> Resource | None:
        """Update a resource. The resource is identified by its ID.

        :param resource_type: The resource type to update.
        :param resource: Resource to update.
        :return: The updated resource. Updating should update the
            "meta.lastModified" data. May be the same object that is
            passed in.
        """
        raise NotImplementedError


class InMemoryBackend(Backend):
    """An example in-memory backend for the SCIM provider.

    It is not optimized for performance. Many operations are O(n) or
    worse, whereas they would perform better with an actual production
    database in the backend. This is intentional to keep the
    implementation simple.
    """

    @dataclasses.dataclass(frozen=True)
    class UniquenessDescriptor:
        """Used to mimic uniqueness constraints e.g. from a SQL database."""

        extension: str | None
        field_name: str
        case_exact: bool
        schema: str

        def is_declared_by(self, resource: Resource) -> bool:
            """Tell whether the model of a resource holds the schema of the attribute."""
            model = type(resource)
            return self.schema == model.__schema__ or (
                self.schema in model.get_extension_models()
            )

        def get_attribute(self, resource: Resource) -> Any:
            holder = getattr(resource, self.extension) if self.extension else resource
            value = getattr(holder, self.field_name, None) if holder else None
            if isinstance(value, str) and not self.case_exact:
                return value.casefold()
            return value

    @classmethod
    def collect_unique_attrs(
        cls, model: type[BaseModel], extension: str | None = None
    ) -> list[UniquenessDescriptor]:
        """Return the uniqueness constraints the annotations of a model declare.

        The ``id`` is left out: the backend issues it, so it cannot clash.
        """
        descriptors = [
            cls.UniquenessDescriptor(
                extension,
                field_name,
                model.get_field_annotation(field_name, CaseExact) == CaseExact.true,
                str(model.__schema__),
            )
            for field_name in model.model_fields
            if field_name != "id"
            and model.get_field_annotation(field_name, Uniqueness) != Uniqueness.none
        ]
        for field_name in model.model_fields:
            root_type = model.get_field_root_type(field_name)
            if isclass(root_type) and issubclass(root_type, Extension):
                descriptors.extend(cls.collect_unique_attrs(root_type, field_name))
        return descriptors

    def __init__(self):
        super().__init__()
        self.resources: list[Resource] = []
        self.lock: Lock = Lock()

    def __enter__(self):
        """See super docs.

        The InMemoryBackend uses a simple Lock to synchronize all
        access.
        """
        super().__enter__()
        self.lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        super().__exit__(exc_type, exc_val, exc_tb)
        self.lock.release()

    def query_resources(
        self,
        search_request: SearchRequest,
        resource_type: ResourceType | None = None,
    ) -> tuple[int, list[Resource]]:
        start_index = (search_request.start_index or 1) - 1

        candidates = [
            r
            for r in self.resources
            if resource_type is None or self._is_of_type(r, resource_type)
        ]

        scim_filter = search_request.filter
        if scim_filter is not None and not scim_filter.models and candidates:
            models = tuple(dict.fromkeys(type(r) for r in candidates))
            scim_filter = ScimFilter[Union[models]](str(scim_filter))  # noqa: UP007

        found_resources = [
            r for r in candidates if scim_filter is None or scim_filter.match(r)
        ]

        found_resources = search_request.sort(found_resources)

        total_results = len(found_resources)
        found_resources = found_resources[start_index:]
        if search_request.count is not None:
            found_resources = found_resources[: search_request.count]
        return total_results, found_resources

    def _is_of_type(self, resource: Resource, resource_type: ResourceType) -> bool:
        """Tell whether a resource belongs to a resource type.

        RFC 7643 §3.1 has meta.resourceType carry the name of the resource type,
        which may differ from its id.
        """
        return resource.meta.resource_type == resource_type.name

    def _get_resource_idx(
        self, resource_type: ResourceType, object_id: str
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
    ) -> Resource | None:
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
        self, resource_type: ResourceType, resource: Resource
    ) -> Resource | None:
        resource = resource.model_copy(deep=True)
        resource.id = uuid.uuid4().hex
        utcnow = datetime.datetime.now(datetime.timezone.utc)
        resource.meta = Meta(
            resource_type=resource_type.name,
            created=utcnow,
            last_modified=utcnow,
            location="/v2" + resource_type.endpoint + "/" + resource.id,
        )
        self._touch_resource(resource, utcnow)
        self._check_uniqueness(resource)
        self.resources.append(resource)
        return resource

    def _check_uniqueness(self, resource: Resource):
        """Refuse a resource sharing a unique value with another one of the same schema.

        RFC 7643 erratum 8279 scopes the uniqueness to the resources using the
        schema that declares the attribute, whatever their resource type. A
        missing value never clashes, as a SQL NULL does not.
        """
        for unique_attribute in self.collect_unique_attrs(type(resource)):
            value = unique_attribute.get_attribute(resource)
            if value is None:
                continue
            for existing_resource in self.resources:
                if (
                    unique_attribute.is_declared_by(existing_resource)
                    and existing_resource.id != resource.id
                    and unique_attribute.get_attribute(existing_resource) == value
                ):
                    raise UniquenessException()

    @staticmethod
    def _touch_resource(resource: Resource, last_modified: datetime.datetime):
        """Touches a resource (updates last_modified and version).

        Version is generated by hashing last_modified. Another option
        would be to hash the entire resource instead.
        """
        resource.meta.last_modified = last_modified
        etag = generate_etag(pickle.dumps(resource.meta.last_modified))
        resource.meta.version = f'W/"{etag}"'

    def update_resource(
        self, resource_type: ResourceType, resource: Resource
    ) -> Resource | None:
        found_res_idx = self._get_resource_idx(resource_type, resource.id)
        if found_res_idx is not None:
            updated_resource = type(resource).model_validate(resource.model_dump())
            self._touch_resource(
                updated_resource, datetime.datetime.now(datetime.timezone.utc)
            )

            self._check_uniqueness(updated_resource)
            self.resources[found_res_idx] = updated_resource
            return updated_resource
        return None
