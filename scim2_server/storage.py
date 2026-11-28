import inspect
import warnings
from abc import ABC
from abc import abstractmethod
from contextlib import AbstractAsyncContextManager
from contextlib import AbstractContextManager
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any
from typing import cast
from weakref import WeakKeyDictionary

from scim2_models import AttributeBinding
from scim2_models import Extension
from scim2_models import Mutability
from scim2_models import Path
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ResponseParameters
from scim2_models import Returned
from scim2_models import SearchRequest
from scim2_models import Uniqueness
from scim2_models import UniquenessException

from scim2_server.utils import parametrize


def _accepts(storage_class: type, method: str, parameter: str) -> bool:
    """Tell whether a method of a storage accepts a keyword argument, and warn when it does not."""
    parameters = inspect.signature(getattr(storage_class, method)).parameters.values()
    if any(
        candidate.name == parameter or candidate.kind is inspect.Parameter.VAR_KEYWORD
        for candidate in parameters
    ):
        return True
    warnings.warn(
        f"{storage_class.__qualname__}.{method} should accept a {parameter} "
        "keyword argument. scim2-server 0.9 will require it.",
        DeprecationWarning,
        # _accepts, __init_subclass__, ABCMeta.__new__, then the class statement
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


def uniqueness_conflict(attribute: AttributeBinding) -> UniquenessException:
    """Return the 409 refusing a value that another resource holds, for one of the :meth:`~scim2_server.mapping.ResourceMapping.unique_attributes`.

    The detail tells the attribute, but never the value. An attribute of
    the schema of the resource is told by its path, such as ``emails.value``.
    An attribute of an extension is told by its URN.

    >>> from scim2_models import Path, User
    >>> uniqueness_conflict(Path[User]("emails.value").resolve()).detail
    'Uniqueness conflict with attribute emails.value'
    """
    return _uniqueness_conflict(_attribute_path(attribute))


def _attribute_path(attribute: AttributeBinding) -> str:
    """Return the path of an attribute of the schema of the resource, or the URN of an attribute of an extension."""
    if issubclass(attribute.model, Extension):
        return attribute.urn

    return attribute.urn.rpartition(":")[2]


def _uniqueness_conflict(path: str) -> UniquenessException:
    return UniquenessException(detail=f"Uniqueness conflict with attribute {path}")


def _unique_values(resource: Resource[Any]) -> dict[AttributeBinding, list[Any]]:
    """Return the unique values of a resource, by attribute, in the form they are compared in.

    The attributes are those whose uniqueness is server or global, at any
    depth, and that clients read. Each value of a multi-valued attribute
    counts on its own. A missing value is left out, and so is a value the
    policy cannot compare: it is equal to no other value. A value that cannot
    be hashed, such as a complex one, is held by its representation.
    """
    model_path = parametrize(Path, type(resource))
    values: dict[AttributeBinding, list[Any]] = {}
    for attribute in _unique_attributes(type(resource)):
        held = model_path(attribute.urn).get(resource, strict=False)
        comparables = [
            _hashable(comparable)
            for value in (held if isinstance(held, list) else [held])
            if value is not None
            and (comparable := _comparable(attribute, value)) is not None
        ]
        if comparables:
            values[attribute] = comparables

    return values


_UNIQUE_ATTRIBUTES: "WeakKeyDictionary[type, list[AttributeBinding]]" = (
    WeakKeyDictionary()
)


def _unique_attributes(model: type[Resource[Any]]) -> list[AttributeBinding]:
    """Return the unique attributes of a model that clients read, once per model."""
    attributes = _UNIQUE_ATTRIBUTES.get(model)
    if attributes is None:
        bindings = (path.resolve() for path in parametrize(Path, model).iter_paths())
        attributes = [
            binding
            for binding in bindings
            if binding is not None
            and binding.get_annotation(Uniqueness)
            in (Uniqueness.server, Uniqueness.global_)
            and not _hidden(binding)
        ]
        _UNIQUE_ATTRIBUTES[model] = attributes

    return attributes


def _hidden(attribute: AttributeBinding) -> bool:
    """Tell whether clients never read an attribute, on its own or through its parent."""
    annotation = attribute.model.get_field_annotation
    returned = {
        annotation(attribute.field_name, Returned),
        attribute.get_annotation(Returned),
    }
    mutability = {
        annotation(attribute.field_name, Mutability),
        attribute.get_annotation(Mutability),
    }
    return Returned.never in returned or Mutability.write_only in mutability


def _comparable(attribute: AttributeBinding, value: Any) -> Any:
    try:
        return attribute.comparable(value)
    except ValueError:
        return None


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


@dataclass
class SearchPage:
    """A page of found resources, as :meth:`ScimStorage.search` returns it."""

    total: int | None
    """The number of matching resources, on every page.

    It may be :data:`None` when the resources are paged with a cursor
    (:rfc:`RFC 9865 §2 <9865#section-2>`).
    """

    resources: list[Resource[Any]]
    """The resources of the page."""

    next: Any = None
    """The position of the next page, or :data:`None` on the last page."""

    previous: Any = None
    """The position of the previous page, or :data:`None` on the first page."""


def search_page(
    storage: "ScimStorage",
    resource_types: list[ResourceType],
    search_request: SearchRequest[Any],
    position: Any,
) -> SearchPage:
    """Search a storage, whether its search method accepts a position or not.

    This function goes away with the deprecated search methods in
    scim2-server 0.9.
    """
    if storage._search_takes_position:
        result = storage.search(resource_types, search_request, position=position)
    else:
        result = storage.search(resource_types, search_request)
    return _page_of(result)


async def async_search_page(
    storage: "AsyncScimStorage",
    resource_types: list[ResourceType],
    search_request: SearchRequest[Any],
    position: Any,
) -> SearchPage:
    """Search an asynchronous storage, as search_page does.

    This function goes away with the deprecated search methods in
    scim2-server 0.9.
    """
    if storage._search_takes_position:
        result = await storage.search(resource_types, search_request, position=position)
    else:
        result = await storage.search(resource_types, search_request)
    return _page_of(result)


def _page_of(result: Any) -> SearchPage:
    """Return the page a search returned, as a deprecated search returns the number of resources and the page."""
    if isinstance(result, tuple):
        return SearchPage(*result)
    return cast(SearchPage, result)


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
    - It fills ``id``, ``meta.resourceType`` and ``meta.version``. It fills
      ``meta.created`` and ``meta.lastModified`` when it stores them, and
      leaves them empty otherwise. It leaves ``meta.location``
      to the server, which knows the URLs. For the same reason, a reference
      to another resource, such as ``members.$ref``, can be relative to the
      SCIM root, such as ``Users/2819c223``. The server returns its URL.
    - The version changes whenever the resource changes. An update that
      changes nothing may keep it.
    - A resource that does not exist raises
      :class:`~scim2_models.NotFoundException`.
    - A value already taken by an attribute whose uniqueness is ``server`` or
      ``global`` raises :class:`~scim2_models.UniquenessException`. This
      holds for sub-attributes too, such as ``emails.value``. Each value of a
      multi-valued attribute counts on its own. The uniqueness holds within
      a resource type. A storage on a database relies on its unique
      constraints, and turns their violations into the exception. The
      constraint should find equal the values the filters find equal, and the
      storage documents the differences. A resource keeping its values, or
      swapping them between its entries, is written.
    - It supports what the :class:`~scim2_models.ServiceProviderConfig` of the
      server announces, such as sorting. The server refuses the rest before
      calling the storage.
    """

    supports_cursors: bool = False
    """Whether :meth:`search` pages the resources with cursors that give stable pages.

    Set it to :data:`True` once :meth:`search` follows the rules of the
    cursors. The server refuses to start when its configuration announces
    cursor pagination and the storage does not support it.
    """

    _get_takes_response_parameters = True
    _search_takes_position = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._get_takes_response_parameters = _accepts(cls, "get", "response_parameters")
        cls._search_takes_position = _accepts(cls, "search", "position")

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

        The whole resource holds the write-only and never returned
        attributes, such as ``password``, in the form the storage keeps them,
        a hash for instance. The server never returns them to the client
        (:rfc:`RFC 7643 §2.2 <7643#section-2.2>`). It needs them to keep a
        value that a PUT leaves out (:rfc:`RFC 7644 §3.5.1
        <7644#section-3.5.1>`), and to clear one that a PATCH removes
        (:rfc:`RFC 7644 §3.5.2.2 <7644#section-3.5.2.2>`).

        A ``get`` method without ``response_parameters`` is deprecated, and
        raises a :class:`DeprecationWarning`. The server then does not pass
        the parameters. scim2-server 0.9 will require it.

        :raises ~scim2_models.NotFoundException: When no resource of this type
            has this identifier.
        """

    @abstractmethod
    def search(
        self,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
        *,
        position: Any = None,
    ) -> SearchPage:
        """Return one page of the matching resources, and their number.

        The search request is already validated, and its ``count`` is already
        bounded by the ``maxResults`` of the server. The storage filters, sorts
        and pages the resources of every given resource type as a single
        collection. Several resource types mean a search at the server root.

        An attribute that a resource type does not declare matches none of its
        resources (:rfc:`RFC 7644 §3.4.2.1 <7644#section-3.4.2.1>`).

        A :attr:`~scim2_models.SearchRequest.start_index` pages the resources
        by index. A :attr:`~scim2_models.SearchRequest.cursor` that is not
        :data:`None` pages them with a cursor (:rfc:`RFC 9865 <9865>`), when
        the storage :attr:`supports_cursors`. The storage then returns the
        ``next`` and ``previous`` positions of the page, as values that JSON
        can hold. The server hides them in the cursors it gives the client,
        and passes them back as ``position``. ``position`` is :data:`None` on
        the first page.

        The cursors give stable pages. A position holds the sort value and
        the identifier of the last resource of the page, or of the first one
        for the previous page. A resource that exists during the whole
        paging, and whose sort value does not change, is returned exactly
        once. A resource created or deleted meanwhile is returned or not,
        depending on where it sorts.

        A ``search`` method without ``position`` is deprecated, and raises a
        :class:`DeprecationWarning`. It returns the number of matching
        resources and the page, and pages by index only. scim2-server 0.9 will
        require ``position``.

        :raises ~scim2_models.InvalidFilterException: When the storage cannot
            evaluate the filter. Filtering the page afterwards would make
            ``totalResults`` and the paging wrong.
        :raises ~scim2_models.InvalidCursorException: When the storage cannot
            read ``position``.
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

        A write-only or never returned value that :meth:`get` returned comes
        back unchanged
        when the request does not change it. The storage keeps it as it is,
        and does not hash it again. It clears the value when it comes back as
        :data:`None`.

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
        request is left to the application. The following storage opens a
        SQLAlchemy savepoint:

        >>> class SQLStorage(ScimStorage):
        ...     def operation(self):
        ...         return self.session.begin_nested()

        :doc:`/how-to/write-a-storage` describes the other methods.
        """
        return nullcontext()


class AsyncScimStorage(ABC):
    """The asynchronous variant of :class:`ScimStorage`.

    Its methods are coroutines, and follow the rules of :class:`ScimStorage`,
    which :class:`~scim2_server.testing.AsyncScimStorageContract` checks.
    """

    supports_cursors: bool = False
    """Whether :meth:`search` pages the resources with cursors that give stable pages. See :attr:`ScimStorage.supports_cursors`."""

    _get_takes_response_parameters = True
    _search_takes_position = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls._get_takes_response_parameters = _accepts(cls, "get", "response_parameters")
        cls._search_takes_position = _accepts(cls, "search", "position")

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
        self,
        resource_types: list[ResourceType],
        search_request: SearchRequest[Any],
        *,
        position: Any = None,
    ) -> SearchPage:
        """Return one page of the matching resources, and their number. See :meth:`ScimStorage.search`."""

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
