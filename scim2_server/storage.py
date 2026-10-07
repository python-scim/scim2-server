import inspect
import warnings
from abc import ABC
from abc import abstractmethod
from contextlib import AbstractAsyncContextManager
from contextlib import AbstractContextManager
from contextlib import nullcontext
from typing import Any

from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ResponseParameters
from scim2_models import SearchRequest


def takes_response_parameters(storage_class: type) -> bool:
    """Tell whether the ``get`` method of a storage accepts ``response_parameters``, and warn when it does not."""
    parameters = inspect.signature(storage_class.get).parameters.values()  # type: ignore[attr-defined]
    if any(
        parameter.name == "response_parameters"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    ):
        return True
    warnings.warn(
        f"{storage_class.__qualname__}.get should accept a response_parameters "
        "keyword argument. scim2-server 0.9 will require it.",
        DeprecationWarning,
        # takes_response_parameters, __init_subclass__, ABCMeta.__new__, then the class statement
        stacklevel=4,
    )
    return False


def projection(
    storage: "ScimStorage | AsyncScimStorage",
    response_parameters: ResponseParameters[Any] | None,
) -> dict[str, Any]:
    """Return the arguments that pass the response parameters to the get method of a storage, if it accepts them."""
    if not storage._get_takes_response_parameters:
        return {}
    return {"response_parameters": response_parameters}


class ScimStorage(ABC):
    """Where a SCIM server reads and writes its resources.

    Subclass it to connect the server to a data source, such as a SQL database
    or a directory. The server handles the SCIM protocol, and calls these
    methods to read, search, create, update and delete resources. Every method
    receives the :class:`~scim2_models.ResourceType` it applies to, so a single
    storage can serve several resource types.

    A storage follows these rules, which
    :class:`~scim2_server.testing.ScimStorageContract` checks:

    - Every resource it returns is a copy. Changing it does not change the
      stored resource, and the resources it receives are not changed either.
    - It fills ``id``, ``meta.resourceType``, ``meta.created``,
      ``meta.lastModified`` and ``meta.version``. It leaves ``meta.location``
      to the server, which knows the URLs. For the same reason, a reference
      to another resource, such as ``members.$ref``, can be relative to the
      SCIM root, such as ``Users/2819c223``. The server returns its URL.
    - The version changes whenever the resource changes. An update that
      changes nothing may keep it.
    - A resource that does not exist raises
      :class:`~scim2_models.NotFoundException`.
    - A value already taken by an attribute whose uniqueness is ``server`` or
      ``global`` raises :class:`~scim2_models.UniquenessException`.
    - It supports what the :class:`~scim2_models.ServiceProviderConfig` of the
      server announces, such as sorting. The server refuses the rest before
      calling the storage.
    """

    _get_takes_response_parameters = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._get_takes_response_parameters = takes_response_parameters(cls)

    @abstractmethod
    def get(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        response_parameters: ResponseParameters[Any] | None = None,
    ) -> Resource[Any]:
        """Return a resource.

        The server passes ``response_parameters`` when it returns the resource
        to the client, for a GET request. The storage may then leave out the
        attributes the response does not keep, for instance to avoid loading
        the members of a group. It must return every attribute the response
        keeps. :meth:`Path.iter_paths <scim2_models.Path.iter_paths>` gives
        them. The server applies the parameters to the response anyway.
        Without ``response_parameters``, the storage returns the whole
        resource.

        A ``get`` method without ``response_parameters`` is deprecated, and
        raises a :class:`DeprecationWarning`. The server then does not pass
        the parameters. scim2-server 0.9 will require it.

        :raises ~scim2_models.NotFoundException: When no resource of this type
            has this identifier.
        """

    @abstractmethod
    def search(
        self, resource_types: list[ResourceType], search_request: SearchRequest[Any]
    ) -> tuple[int, list[Resource[Any]]]:
        """Return the number of matching resources, and one page of them.

        The search request is already validated, and its ``count`` is already
        bounded by the ``maxResults`` of the server. The storage filters, sorts
        and pages the resources of every given resource type as a single
        collection. Several resource types mean a search at the server root.

        An attribute that a resource type does not declare matches none of its
        resources (:rfc:`RFC 7644 §3.4.2.1 <7644#section-3.4.2.1>`).

        :raises ~scim2_models.InvalidFilterException: When the storage cannot
            evaluate the filter. Filtering the page afterwards would make
            ``totalResults`` and the paging wrong.
        :raises ~scim2_models.NotImplementedException: When the storage does
            not support searching several resource types at once.
        """

    @abstractmethod
    def create(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        """Store a new resource, and return the stored resource.

        :raises ~scim2_models.UniquenessException: When a unique value is taken.
        """

    @abstractmethod
    def update(
        self,
        resource_type: ResourceType,
        resource: Resource[Any],
        *,
        expected_version: str | None = None,
    ) -> Resource[Any]:
        """Replace the stored resource that has the identifier of ``resource``, and return the stored resource.

        The server calls it for PUT and PATCH requests. It applies the
        request to the stored resource first, so ``resource`` is the whole new
        state of the resource.

        :param expected_version: The version the stored resource must still
            have, when given.
        :raises ~scim2_models.NotFoundException: When the resource does not exist.
        :raises ~scim2_models.PreconditionFailedException: When the stored
            version is not ``expected_version``.
        :raises ~scim2_models.UniquenessException: When a unique value is taken.
        """

    @abstractmethod
    def delete(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        expected_version: str | None = None,
    ) -> None:
        """Delete a resource.

        :param expected_version: The version the stored resource must still
            have, when given.
        :raises ~scim2_models.NotFoundException: When the resource does not exist.
        :raises ~scim2_models.PreconditionFailedException: When the stored
            version is not ``expected_version``.
        """

    def operation(self) -> AbstractContextManager[None]:
        """Enclose one SCIM operation: a single request, or one operation of a bulk request.

        It does nothing by default. A SQL storage can open a savepoint here,
        so that a failed operation does not prevent the next ones of a bulk
        request (:rfc:`RFC 7644 §3.7 <7644#section-3.7>`). Committing the
        request is left to the application.
        """
        return nullcontext()


class AsyncScimStorage(ABC):
    """The asynchronous variant of :class:`ScimStorage`.

    Its methods are coroutines, and follow the rules of :class:`ScimStorage`,
    which :class:`~scim2_server.testing.AsyncScimStorageContract` checks.
    """

    _get_takes_response_parameters = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._get_takes_response_parameters = takes_response_parameters(cls)

    @abstractmethod
    async def get(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        response_parameters: ResponseParameters[Any] | None = None,
    ) -> Resource[Any]:
        """Return a resource. See :meth:`ScimStorage.get`."""

    @abstractmethod
    async def search(
        self, resource_types: list[ResourceType], search_request: SearchRequest[Any]
    ) -> tuple[int, list[Resource[Any]]]:
        """Return the number of matching resources, and one page of them. See :meth:`ScimStorage.search`."""

    @abstractmethod
    async def create(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        """Store a new resource. See :meth:`ScimStorage.create`."""

    @abstractmethod
    async def update(
        self,
        resource_type: ResourceType,
        resource: Resource[Any],
        *,
        expected_version: str | None = None,
    ) -> Resource[Any]:
        """Replace a stored resource. See :meth:`ScimStorage.update`."""

    @abstractmethod
    async def delete(
        self,
        resource_type: ResourceType,
        resource_id: str,
        *,
        expected_version: str | None = None,
    ) -> None:
        """Delete a resource. See :meth:`ScimStorage.delete`."""

    def operation(self) -> AbstractAsyncContextManager[None]:
        """Enclose one SCIM operation. See :meth:`ScimStorage.operation`."""
        return nullcontext()
