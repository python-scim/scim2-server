import dataclasses
import datetime
import operator
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
from scim2_models import Schema
from scim2_models import ScimFilter
from scim2_models import SearchRequest
from scim2_models import Uniqueness
from scim2_models import UniquenessException
from werkzeug.http import generate_etag

from scim2_server.operators import ResolveSortOperator


class Backend:
    """The base class for a SCIM provider backend."""

    def __init__(self):
        self.schemas: dict[str, Schema] = {}
        self.resource_types: dict[str, ResourceType] = {}
        self.resource_types_by_endpoint: dict[str, ResourceType] = {}
        self.models_dict: dict[str, BaseModel] = {}

    def __enter__(self):
        """Allow the backend to be used as a context manager.

        This enables support for transactions.
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the transaction."""
        pass

    def register_schema(self, schema: Schema):
        """Register a Schema for use with the backend."""
        self.schemas[schema.id] = schema

    def get_schemas(self):
        """Return all schemas registered with the backend."""
        return self.schemas.values()

    def get_schema(self, schema_id: str) -> Schema | None:
        """Get a schema by its id."""
        return self.schemas.get(schema_id)

    def register_resource_type(self, resource_type: ResourceType):
        """Register a ResourceType for use with the backend.

        The schemas used for the resource and its extensions must have
        been registered with the Backend beforehand.
        """
        if resource_type.schema_ not in self.schemas:
            raise RuntimeError(f"Unknown schema: {resource_type.schema_}")
        for resource_extension in resource_type.schema_extensions or []:
            if resource_extension.schema_ not in self.schemas:
                raise RuntimeError(f"Unknown schema: {resource_extension.schema_}")

        self.resource_types[resource_type.id] = resource_type
        self.resource_types_by_endpoint[resource_type.endpoint.lower()] = resource_type

        extensions = [
            Extension.from_schema(self.get_schema(se.schema_))
            for se in resource_type.schema_extensions or []
        ]
        base_schema = self.get_schema(resource_type.schema_)
        self.models_dict[resource_type.id] = Resource.from_schema(base_schema)
        if extensions:
            self.models_dict[resource_type.id] = self.models_dict[resource_type.id][
                Union[tuple(extensions)]  # noqa: UP007
            ]

    def get_resource_types(self):
        """Return all resource types registered with the backend."""
        return self.resource_types.values()

    def get_resource_type(self, resource_type_id: str) -> ResourceType | None:
        """Return the resource type by its id."""
        return self.resource_types.get(resource_type_id)

    def get_resource_type_by_endpoint(self, endpoint: str) -> ResourceType | None:
        """Return the resource type by its endpoint."""
        return self.resource_types_by_endpoint.get(endpoint.lower())

    def get_model(self, resource_type_id: str) -> BaseModel | None:
        """Return the Pydantic Python model for a given resource type."""
        return self.models_dict.get(resource_type_id)

    def get_models(self):
        """Return all Pydantic Python models for all known resource types."""
        return self.models_dict.values()

    def query_resources(
        self,
        search_request: SearchRequest,
        resource_type_id: str | None = None,
    ) -> tuple[int, list[Resource]]:
        """Query the backend for a set of resources.

        :param search_request: SearchRequest instance describing the
            query.
        :param resource_type_id: ID of the resource type to query. If
            None, all resource types are queried.
        :return: A tuple of "total results" and a List of found
            Resources. The List must contain a copy of resources.
            Mutating elements in the List must not modify the data
            stored in the backend.
        :raises TooManyException: If the backend only supports querying
            for one resource type at a time, setting resource_type_id to
            None the backend may raise TooManyException.
        """
        raise NotImplementedError

    def get_resource(self, resource_type_id: str, object_id: str) -> Resource | None:
        """Query the backend for a resources by its ID.

        :param resource_type_id: ID of the resource type to get the
            object from.
        :param object_id: ID of the object to get.
        :return: The resource object if it exists, None otherwise. The
            resource must be a copy, modifying it must not change the
            data stored in the backend.
        """
        raise NotImplementedError

    def delete_resource(self, resource_type_id: str, object_id: str) -> bool:
        """Delete a resource.

        :param resource_type_id: ID of the resource type to delete the
            object from.
        :param object_id: ID of the object to delete.
        :return: True if the resource was deleted, False otherwise.
        """
        raise NotImplementedError

    def create_resource(
        self, resource_type_id: str, resource: Resource
    ) -> Resource | None:
        """Create a resource.

        :param resource_type_id: ID of the resource type to create.
        :param resource: Resource to create.
        :return: The created resource. Creation should set system-
            defined attributes (ID, Metadata). May be the same object
            that is passed in.
        """
        raise NotImplementedError

    def update_resource(
        self, resource_type_id: str, resource: Resource
    ) -> Resource | None:
        """Update a resource. The resource is identified by its ID.

        :param resource_type_id: ID of the resource type to update.
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
        resource_type_id: str | None = None,
    ) -> tuple[int, list[Resource]]:
        start_index = (search_request.start_index or 1) - 1

        scim_filter = search_request.filter
        if scim_filter is not None and not scim_filter.models:
            models = (
                list(self.models_dict.values())
                if resource_type_id is None
                else [self.models_dict[resource_type_id]]
            )
            scim_filter = ScimFilter[Union[tuple(models)]](str(scim_filter))  # noqa: UP007

        found_resources = [
            r
            for r in self.resources
            if (resource_type_id is None or r.meta.resource_type == resource_type_id)
            and (scim_filter is None or scim_filter.match(r))
        ]

        if search_request.sort_by is not None:
            descending = search_request.sort_order == SearchRequest.SortOrder.descending
            sort_operator = ResolveSortOperator(str(search_request.sort_by))

            # To ensure that unset attributes are sorted last (when ascending, as defined in the RFC),
            # we have to divide the result set into a set and unset subset.
            unset_values = []
            set_values = []
            for resource in found_resources:
                result = sort_operator(resource)
                if result is None:
                    unset_values.append(resource)
                else:
                    set_values.append((resource, result))

            set_values.sort(key=operator.itemgetter(1), reverse=descending)
            set_values = [value[0] for value in set_values]
            if descending:
                found_resources = unset_values + set_values
            else:
                found_resources = set_values + unset_values

        total_results = len(found_resources)
        found_resources = found_resources[start_index:]
        if search_request.count is not None:
            found_resources = found_resources[: search_request.count]
        return total_results, found_resources

    def _get_resource_idx(self, resource_type_id: str, object_id: str) -> int | None:
        return next(
            (
                idx
                for idx, r in enumerate(self.resources)
                if r.meta.resource_type == resource_type_id and r.id == object_id
            ),
            None,
        )

    def get_resource(self, resource_type_id: str, object_id: str) -> Resource | None:
        resource_dict_idx = self._get_resource_idx(resource_type_id, object_id)
        if resource_dict_idx is not None:
            return self.resources[resource_dict_idx].model_copy(deep=True)
        return None

    def delete_resource(self, resource_type_id: str, object_id: str) -> bool:
        found = self.get_resource(resource_type_id, object_id)
        if found:
            self.resources = [
                r
                for r in self.resources
                if not (r.meta.resource_type == resource_type_id and r.id == object_id)
            ]
            return True
        return False

    def create_resource(
        self, resource_type_id: str, resource: Resource
    ) -> Resource | None:
        resource = resource.model_copy(deep=True)
        resource.id = uuid.uuid4().hex
        utcnow = datetime.datetime.now(datetime.timezone.utc)
        resource.meta = Meta(
            resource_type=self.resource_types[resource_type_id].name,
            created=utcnow,
            last_modified=utcnow,
            location="/v2"
            + self.resource_types[resource_type_id].endpoint
            + "/"
            + resource.id,
        )
        self._touch_resource(resource, utcnow)
        self._check_uniqueness(resource_type_id, resource)
        self.resources.append(resource)
        return resource

    def _check_uniqueness(self, resource_type_id: str, resource: Resource):
        """Refuse a resource sharing a unique value with another one of its type.

        A missing value never clashes, as a SQL NULL does not.
        """
        for unique_attribute in self.collect_unique_attrs(type(resource)):
            value = unique_attribute.get_attribute(resource)
            if value is None:
                continue
            for existing_resource in self.resources:
                if (
                    existing_resource.meta.resource_type == resource_type_id
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
        self, resource_type_id: str, resource: Resource
    ) -> Resource | None:
        found_res_idx = self._get_resource_idx(resource_type_id, resource.id)
        if found_res_idx is not None:
            updated_resource = self.models_dict[resource_type_id].model_validate(
                resource.model_dump()
            )
            self._touch_resource(
                updated_resource, datetime.datetime.now(datetime.timezone.utc)
            )

            self._check_uniqueness(resource_type_id, updated_resource)
            self.resources[found_res_idx] = updated_resource
            return updated_resource
        return None
