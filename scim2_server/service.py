from collections.abc import Mapping
from dataclasses import replace
from http import HTTPStatus
from typing import Any
from typing import TypeVar
from typing import Union
from typing import cast

from pydantic import ValidationError
from pydantic_core import from_json
from scim2_models import AuthenticationScheme
from scim2_models import BaseModel
from scim2_models import Bulk
from scim2_models import BulkOperation
from scim2_models import BulkRequest
from scim2_models import BulkResponse
from scim2_models import ChangePassword
from scim2_models import Context
from scim2_models import Error
from scim2_models import ETag
from scim2_models import Filter
from scim2_models import ForbiddenException
from scim2_models import InvalidSyntaxException
from scim2_models import InvalidValueException
from scim2_models import ListResponse
from scim2_models import Meta
from scim2_models import NotFoundException
from scim2_models import NotImplementedException
from scim2_models import Patch
from scim2_models import PatchOp
from scim2_models import PayloadTooLargeException
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ResponseParameters
from scim2_models import Schema
from scim2_models import SCIMException
from scim2_models import ScimProvider
from scim2_models import SearchRequest
from scim2_models import ServiceProviderConfig
from scim2_models import Sort

from scim2_server.bulk import BulkPlan
from scim2_server.conditions import Conditions
from scim2_server.errors import MethodNotAllowedException
from scim2_server.errors import UnsupportedMediaTypeException
from scim2_server.requests import ScimRequest
from scim2_server.responses import ScimResponse
from scim2_server.routing import Operation
from scim2_server.routing import Target
from scim2_server.routing import match
from scim2_server.utils import parametrize


def scim_exception_of(exception: ValidationError) -> SCIMException:
    """Return the SCIM exception of an invalid request."""
    return SCIMException.from_error(Error.from_validation_errors(exception)[0])


BULK_SUCCESS_STATUS = {
    BulkOperation.Method.post: HTTPStatus.CREATED,
    BulkOperation.Method.put: HTTPStatus.OK,
    BulkOperation.Method.patch: HTTPStatus.OK,
    BulkOperation.Method.delete: HTTPStatus.NO_CONTENT,
}

BULK_OPERATIONS = {
    BulkOperation.Method.post: Operation.create,
    BulkOperation.Method.put: Operation.replace,
    BulkOperation.Method.patch: Operation.patch,
    BulkOperation.Method.delete: Operation.delete,
}

CHALLENGES = {
    AuthenticationScheme.Type.oauthbearertoken: 'Bearer realm="SCIM"',
    AuthenticationScheme.Type.oauth2: 'Bearer realm="SCIM"',
    AuthenticationScheme.Type.httpbasic: 'Basic realm="SCIM"',
}

SEARCH_REQUEST_PARAMETERS = (
    "attributes",
    "excludedAttributes",
    "filter",
    "sortBy",
    "sortOrder",
    "startIndex",
    "count",
)

ModelT = TypeVar("ModelT", bound=BaseModel)
DiscoveryResourceT = TypeVar(
    "DiscoveryResourceT", ResourceType, Schema, ServiceProviderConfig
)


def is_json_media_type(content_type: str | None) -> bool:
    """Tell whether a Content-Type header designates JSON.

    ``application/scim+json``, ``application/json`` and any other
    ``application/*+json`` are accepted, whatever their parameters.
    """
    if not content_type:
        return False
    media_type = content_type.partition(";")[0].strip().lower()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


def patch_only_config() -> ServiceProviderConfig:
    """Return the configuration of a service that only supports PATCH.

    PATCH only needs the storage to update a resource, so every storage
    supports it. The other capabilities need a storage that implements them.
    """
    return ServiceProviderConfig(
        patch=Patch(supported=True),
        bulk=Bulk(supported=False, max_operations=0, max_payload_size=0),
        filter=Filter(supported=False),
        change_password=ChangePassword(supported=False),
        sort=Sort(supported=False),
        etag=ETag(supported=False),
        authentication_schemes=[],
    )


class ScimService:
    """The SCIM protocol, without input or output.

    A service validates the requests, applies the PUT and PATCH requests to
    the resources, evaluates the conditional headers and builds the responses.
    It neither reads nor writes resources:
    :class:`~scim2_server.handler.ScimHandler` calls the storage between its
    steps. It knows nothing about the web framework either: the integration
    reads the request, and turns the :class:`~scim2_server.responses.ScimResponse`
    into a response of the framework.

    A provider without ``config`` announces PATCH, and no other capability.
    The storage must implement each capability the configuration announces.

    Every public method can be overridden. ``base_url`` is the root URL of the
    SCIM endpoints, as the client sees it, such as
    ``https://example.com/scim/v2``.
    """

    def __init__(self, provider: ScimProvider):
        self.provider = provider
        self.config = provider.config or patch_only_config()

    # -- Resource types and models --------------------------------------

    def get_model(self, resource_type: ResourceType) -> type[Resource[Any]]:
        """Return the model of a resource type, its extensions included."""
        return cast(type[Resource[Any]], self.provider.model_for(resource_type))

    def get_models(self) -> list[type[Resource[Any]]]:
        """Return the models of every resource type."""
        return [self.get_model(rt) for rt in self.provider.resource_types]

    def get_resource_type_by_endpoint(self, endpoint: str) -> ResourceType | None:
        """Return the resource type an endpoint serves, if any."""
        return next(
            (
                resource_type
                for resource_type in self.provider.resource_types
                if (resource_type.endpoint or "").lstrip("/").casefold()
                == endpoint.lstrip("/").casefold()
            ),
            None,
        )

    # -- Requests -------------------------------------------------------

    @staticmethod
    def match(request: ScimRequest) -> Target:
        """Return the operation a request asks for (:rfc:`RFC 7644 §3.2 <7644#section-3.2>`).

        The target of a ``/Me`` request is not resolved yet.

        :raises ~scim2_models.NotFoundException: When no endpoint has the path
            of the request.
        :raises ~scim2_server.errors.MethodNotAllowedException: When the
            endpoint does not support the method of the request.
        """
        return match(request.method.upper(), request.path)

    def route(self, request: ScimRequest, operation: Operation) -> Target:
        """Return what a request for an operation acts on.

        A ``/Me`` request acts on the resource of :meth:`me_target`, or creates
        a resource of the type of :meth:`me_creation_type`.

        :param operation: The operation the caller serves. A request for
            another operation is a routing error of the integration.
        :raises ~scim2_models.NotFoundException: When no endpoint has the path
            of the request.
        :raises ~scim2_server.errors.MethodNotAllowedException: When the
            endpoint does not support the method of the request.
        """
        target = self.match(request)
        if target.operation is not operation:
            raise RuntimeError(
                f"{request.method} {request.path} asks for {target.operation.value}, "
                f"not for {operation.value}"
            )
        if not target.me:
            return target
        if operation is Operation.create:
            resource_type = self.me_creation_type(request)
            return replace(target, endpoint=self.endpoint_of(resource_type))
        resource_type, resource_id = self.me_target(request)
        return replace(
            target, endpoint=self.endpoint_of(resource_type), resource_id=resource_id
        )

    @staticmethod
    def endpoint_of(resource_type: ResourceType) -> str:
        """Return the endpoint of a resource type, without its slashes."""
        assert resource_type.endpoint is not None
        return resource_type.endpoint.strip("/")

    def me_target(self, request: ScimRequest) -> tuple[ResourceType, str]:
        """Return the type and the identifier of the resource ``/Me`` stands for (:rfc:`RFC 7644 §3.11 <7644#section-3.11>`).

        Override this method to serve ``/Me``. It reads the authenticated
        subject in :attr:`ScimRequest.subject
        <scim2_server.requests.ScimRequest.subject>`. The exceptions it raises
        answer the request.

        :raises ~scim2_models.NotImplementedException: By default, so that
            ``/Me`` answers 501.
        :raises ~scim2_models.UnauthorizedException: When the request has no
            authenticated subject, for an application that accepts anonymous
            requests. It answers 401.
        :raises ~scim2_models.ForbiddenException: When the subject may not
            use ``/Me``, for a 403.
        :raises ~scim2_models.NotFoundException: When the subject has no
            resource, for a 404.
        """
        raise NotImplementedException(detail="/Me is not supported")

    def me_creation_type(self, request: ScimRequest) -> ResourceType:
        """Return the type of the resource a POST on ``/Me`` creates (:rfc:`RFC 7644 §3.11 <7644#section-3.11>`).

        Override this method to let the clients register themselves
        (:rfc:`RFC 7644 §7.6 <7644#section-7.6>`). Linking the created resource to the subject is left to the
        application. The exceptions it raises answer the request.

        :raises ~scim2_models.NotImplementedException: By default, so that a
            POST on ``/Me`` answers 501.
        :raises ~scim2_models.UnauthorizedException: When the request has no
            authenticated subject, for an application that accepts anonymous
            requests. It answers 401.
        :raises ~scim2_models.ForbiddenException: When the subject may not
            register, for a 403.
        :raises ~scim2_models.UniquenessException: When the subject already
            has a resource, for a 409.
        """
        raise NotImplementedException(detail="POST /Me is not supported")

    def me_response(
        self, request: ScimRequest, target: Target, response: ScimResponse
    ) -> ScimResponse:
        """Add the location of the resource ``/Me`` stands for to a response (:rfc:`RFC 7644 §3.11 <7644#section-3.11>`)."""
        if target.me and target.resource_id is not None:
            response.headers["Location"] = self.resource_location(
                request.base_url,
                self.resource_type_at(cast(str, target.endpoint)),
                target.resource_id,
            )
        return response

    def authorize(
        self, request: ScimRequest, target: Target, resource_type: ResourceType
    ) -> None:
        """Accept or refuse an operation on a resource type (:rfc:`RFC 7644 §2 <7644#section-2>`).

        Override this method to check the rights of the client. It reads the
        authenticated subject in :attr:`ScimRequest.subject
        <scim2_server.requests.ScimRequest.subject>`. By default, every
        operation is accepted.

        The service calls this method before it validates the request body,
        and before the handler calls the storage. For a ``/Me`` request, the
        service calls it after :meth:`me_target` or :meth:`me_creation_type`
        finds the resource, and :attr:`Target.me
        <scim2_server.routing.Target.me>` is :data:`True`.

        The service can call this method several times for one request: once
        per operation of a bulk request, and once per resource type of a
        search at the root. The method must not do any input or output. The
        subject must arrive with everything the decision needs, such as its
        permissions or the scopes of its token.

        In a bulk request, ``target`` describes the operation, and ``request``
        holds the bulk request. Read the operation in ``target``, never in the
        method or the path of ``request``.

        In a search at the root, ``target.endpoint`` is :data:`None`, and
        ``resource_type`` is one of the searched types. A
        :class:`~scim2_models.ForbiddenException` removes this type from the
        search. The search answers 403 when every type is removed.

        :raises ~scim2_models.ForbiddenException: When the client may not
            perform the operation, for a 403.
        :raises ~scim2_models.UnauthorizedException: When the request has no
            authenticated subject, for an application that accepts anonymous
            requests. It answers 401.
        """

    def authorize_bulk_operation(
        self,
        request: ScimRequest,
        operation: BulkOperation[Resource[Any]],
        resource_type: ResourceType | None,
    ) -> None:
        """Authorize a resolved bulk operation with :meth:`authorize`.

        An operation without a valid method or path keeps its validation
        error, without authorization.
        """
        if resource_type is None:
            return
        target = Target(
            BULK_OPERATIONS[cast(BulkOperation.Method, operation.method)],
            endpoint=self.endpoint_of(resource_type),
            resource_id=operation.resource_id,
        )
        self.authorize(request, target, resource_type)

    def authorized_types(
        self, request: ScimRequest, target: Target, resource_types: list[ResourceType]
    ) -> list[ResourceType]:
        """Return the resource types the client may search, according to :meth:`authorize`.

        At the root, the refused types are left out of the search
        (:rfc:`RFC 7644 §3.4.2.1 <7644#section-3.4.2.1>`).

        :raises ~scim2_models.ForbiddenException: When the client may search
            none of the types.
        """
        if target.endpoint is not None:
            for resource_type in resource_types:
                self.authorize(request, target, resource_type)
            return resource_types

        authorized = []
        for resource_type in resource_types:
            try:
                self.authorize(request, target, resource_type)
            except ForbiddenException:
                continue
            authorized.append(resource_type)
        if not authorized:
            raise ForbiddenException(detail="No resource type may be searched")
        return authorized

    @staticmethod
    def read_conditions(request: ScimRequest) -> Conditions:
        """Return the conditional headers of a request."""
        return Conditions(
            if_match=request.header("If-Match"),
            if_none_match=request.header("If-None-Match"),
        )

    def max_body_size(self, request: ScimRequest) -> int | None:
        """Return the largest body the service accepts for a request, in bytes.

        An integration reads at most one byte more, and passes the body to the
        handler. It must not refuse a larger body itself: the service answers,
        with a 413 or with an error that comes first, such as a 501 when bulk
        is not supported.

        A bulk request is limited by maxPayloadSize
        (:rfc:`RFC 7644 §3.7.4 <7644#section-3.7.4>`). A request that fails
        whatever its body, such as a request on an unknown path, gets 0.
        Other requests have no limit.
        """
        try:
            operation = self.match(request).operation
        except SCIMException:
            return 0
        if operation is not Operation.bulk:
            return None
        if self.config.bulk is None or not self.config.bulk.supported:
            return 0
        return self.bulk_max_payload_size()

    def resource_type_at(self, endpoint: str) -> ResourceType:
        """Return the resource type an endpoint serves.

        :raises ~scim2_models.NotFoundException: When no resource type is
            served at this endpoint.
        """
        resource_type = self.get_resource_type_by_endpoint(endpoint)
        if resource_type is None:
            raise NotFoundException(detail=f"No resource type found at {endpoint!r}")
        return resource_type

    def get_resource_type(self, name: str) -> ResourceType:
        """Return the resource type of a name, such as ``User``.

        :raises ValueError: When the provider serves no resource type of this
            name.
        """
        for resource_type in self.provider.resource_types:
            if resource_type.name == name:
                return resource_type
        raise ValueError(f"No resource type named {name!r}")

    def get_resource_type_of(self, resource: Resource[Any]) -> ResourceType:
        """Return the resource type of a stored resource, from its meta.resourceType.

        :raises ValueError: When the storage returned a resource of a type the
            provider does not serve.
        """
        name = resource.meta.resource_type if resource.meta else None
        for resource_type in self.provider.resource_types:
            if resource_type.name == name:
                return resource_type
        raise ValueError(
            f"The storage returned a resource of an unknown resource type: {name!r}"
        )

    # -- Capabilities ---------------------------------------------------

    @property
    def etag_supported(self) -> bool:
        """Whether the configuration declares the resources versioned with ETags."""
        return bool(self.config.etag and self.config.etag.supported)

    @staticmethod
    def ensure_supported(
        capability: Patch | Bulk | Filter | Sort | None, operation: str
    ) -> None:
        """Refuse with a 501 an operation the configuration does not declare supported.

        :rfc:`RFC 7644 §3.12 <7644#section-3.12>` answers 501 when the service
        provider does not support the request operation.
        """
        if capability is None or not capability.supported:
            raise NotImplementedException(detail=f"{operation} is not supported")

    def bulk_max_payload_size(self) -> int | None:
        """Return the largest bulk request body the service accepts, in bytes.

        An integration can use it to stop reading a larger body early.
        """
        return self.config.bulk.max_payload_size if self.config.bulk else None

    def ensure_bulk_payload_size(self, size: int) -> None:
        """Refuse with a 413 a bulk request body larger than maxPayloadSize (:rfc:`RFC 7644 §3.7.4 <7644#section-3.7.4>`).

        :param size: The size of the body, in bytes.
        """
        limit = self.bulk_max_payload_size()
        if limit is not None and size > limit:
            raise self.payload_too_large(limit)

    @staticmethod
    def payload_too_large(limit: int) -> PayloadTooLargeException:
        """Return the 413 error of a bulk request body larger than maxPayloadSize."""
        return PayloadTooLargeException(
            detail=f"The payload exceeds the maxPayloadSize ({limit} bytes)"
        )

    # -- Request bodies -------------------------------------------------

    @staticmethod
    def ensure_json(content_type: str | None) -> None:
        """Refuse with a 415 a request body that is not JSON.

        Per :rfc:`RFC 7644 §3.8 <7644#section-3.8>`, JSON is the default
        format: a body without a :mdn:`Content-Type`, or with an empty one,
        is read as JSON.
        """
        if content_type and not is_json_media_type(content_type):
            raise UnsupportedMediaTypeException

    def read_body(
        self,
        model: type[ModelT],
        body: bytes,
        content_type: str | None,
        scim_ctx: Context,
    ) -> ModelT:
        """Validate a request body with a model, in the context of its operation."""
        self.ensure_json(content_type)
        try:
            return model.model_validate_json(body, scim_ctx=scim_ctx)
        except ValidationError as exception:
            raise scim_exception_of(exception) from exception

    def decode_body(self, body: bytes, content_type: str | None) -> Any:
        """Return the JSON value of a request body, unvalidated."""
        self.ensure_json(content_type)
        try:
            return from_json(body)
        except ValueError as exception:
            raise InvalidSyntaxException(detail=str(exception)) from exception

    # -- Locations ------------------------------------------------------

    def resource_location(
        self, base_url: str, resource_type: ResourceType, resource_id: str
    ) -> str:
        """Return the URL of a resource.

        Override this method to serve the resources at other URLs.
        """
        assert resource_type.endpoint is not None
        return (
            f"{base_url.rstrip('/')}/{resource_type.endpoint.strip('/')}/{resource_id}"
        )

    def publish(self, base_url: str, resource: Resource[Any]) -> Resource[Any]:
        """Return a copy of a stored resource in the form sent to the client.

        Its location is set, and its version is left out when the service does
        not support ETags.
        """
        assert resource.meta is not None
        assert resource.id is not None
        update: dict[str, Any] = {
            "location": self.resource_location(
                base_url, self.get_resource_type_of(resource), resource.id
            )
        }
        if not self.etag_supported:
            update["version"] = None
        return resource.model_copy(
            update={"meta": resource.meta.model_copy(update=update)}
        )

    @staticmethod
    def resource_response(
        resource: Resource[Any],
        scim_ctx: Context,
        response_parameters: ResponseParameters[Any] | None = None,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> ScimResponse:
        """Return the response carrying a published resource, with its ETag.

        Per :rfc:`RFC 7643 §3.1 <7643#section-3.1>`, the
        :mdn:`Content-Location` header holds ``meta.location``.
        """
        assert resource.meta is not None and resource.meta.location is not None
        headers = {"Content-Location": resource.meta.location}
        if resource.meta.version:
            headers["ETag"] = resource.meta.version
        body = resource.model_dump(
            scim_ctx=scim_ctx, response_parameters=response_parameters
        )
        return ScimResponse(status, body, headers)

    # -- Conditional requests -------------------------------------------

    def check_preconditions(
        self, resource: Resource[Any], method: str, conditions: Conditions
    ) -> bool:
        """Evaluate the conditional headers of a request against a resource.

        :return: :data:`False` when a GET should answer 304 Not Modified.
        :raises ~scim2_models.PreconditionFailedException: When the method must
            not be performed.
        """
        assert resource.meta is not None
        version = resource.meta.version if self.etag_supported else None
        return conditions.check(version, method)

    # -- Creation -------------------------------------------------------

    def read_creation(
        self, resource_type: ResourceType, body: bytes, content_type: str | None
    ) -> Resource[Any]:
        """Validate the body of a creation request."""
        return self.read_body(
            self.get_model(resource_type),
            body,
            content_type,
            Context.RESOURCE_CREATION_REQUEST,
        )

    def creation_response(self, base_url: str, created: Resource[Any]) -> ScimResponse:
        """Return the 201 response to a creation, with the location of the resource."""
        resource = self.publish(base_url, created)
        assert resource.meta is not None and resource.meta.location is not None
        result = self.resource_response(
            resource, Context.RESOURCE_CREATION_RESPONSE, status=HTTPStatus.CREATED
        )
        result.headers["Location"] = resource.meta.location
        return result

    # -- Query ----------------------------------------------------------

    def read_response_parameters(
        self, resource_type: ResourceType, query: Mapping[str, str]
    ) -> ResponseParameters[Any]:
        """Read the "attributes" and "excludedAttributes" query parameters."""
        parameters = {
            key: query[key]
            for key in ("attributes", "excludedAttributes")
            if key in query
        }
        try:
            return parametrize(
                ResponseParameters, self.get_model(resource_type)
            ).model_validate(parameters)
        except ValidationError as exception:
            raise scim_exception_of(exception) from exception

    def query_response(
        self,
        base_url: str,
        resource: Resource[Any],
        response_parameters: ResponseParameters[Any],
        conditions: Conditions,
    ) -> ScimResponse:
        """Return the response to the read of a resource, or a 304."""
        published = self.publish(base_url, resource)
        if not self.check_preconditions(published, "GET", conditions):
            assert published.meta is not None
            # RFC 9110 §15.4.5: a 304 carries the ETag a 200 would have
            headers = {"ETag": published.meta.version} if published.meta.version else {}
            return ScimResponse(HTTPStatus.NOT_MODIFIED, None, headers)
        return self.resource_response(
            published, Context.RESOURCE_QUERY_RESPONSE, response_parameters
        )

    # -- Search ---------------------------------------------------------

    def search_models(
        self, resource_types: list[ResourceType]
    ) -> list[type[Resource[Any]]]:
        return [self.get_model(rt) for rt in resource_types]

    def read_search_query(
        self, resource_types: list[ResourceType], query: Mapping[str, str]
    ) -> SearchRequest[Any]:
        """Read the query parameters of a search with GET (:rfc:`RFC 7644 §3.4.2 <7644#section-3.4.2>`)."""
        return self.read_search(
            resource_types,
            {key: query[key] for key in SEARCH_REQUEST_PARAMETERS if key in query},
        )

    def read_search_body(
        self,
        resource_types: list[ResourceType],
        body: bytes,
        content_type: str | None,
    ) -> SearchRequest[Any]:
        """Read the body of a search with POST on ".search" (:rfc:`RFC 7644 §3.4.3 <7644#section-3.4.3>`)."""
        return self.read_search(resource_types, self.decode_body(body, content_type))

    def read_search(
        self, resource_types: list[ResourceType], payload: Any
    ) -> SearchRequest[Any]:
        """Validate a search request, and bound its count by maxResults."""
        # The filters of PATCH paths are part of the PATCH capability: the
        # filter capability of RFC 7643 §5 refers to the search parameter of
        # RFC 7644 §3.4.2.2 only.
        parameters = (
            {key.casefold() for key in payload} if isinstance(payload, dict) else set()
        )
        if "filter" in parameters:
            self.ensure_supported(self.config.filter, "Filtering")
        if parameters & {"sortby", "sortorder"}:
            self.ensure_supported(self.config.sort, "Sorting")

        try:
            search_request = parametrize(
                SearchRequest,
                Union[tuple(self.search_models(resource_types))],  # noqa: UP007
            ).model_validate(payload, scim_ctx=Context.SEARCH_REQUEST)
        except ValidationError as exception:
            raise scim_exception_of(exception) from exception
        search_request.start_index = search_request.start_index or 1
        max_results = self.config.filter.max_results if self.config.filter else None
        if max_results is not None and (
            search_request.count is None or search_request.count > max_results
        ):
            search_request.count = max_results
        return search_request

    def searched_types(self, endpoint: str | None) -> list[ResourceType]:
        """Return the resource types a search covers: those of the endpoint, or all of them at the root."""
        if endpoint is None:
            return list(self.provider.resource_types)
        return [self.resource_type_at(endpoint)]

    def search_response(
        self,
        base_url: str,
        total_results: int,
        resources: list[Resource[Any]],
        search_request: SearchRequest[Any],
    ) -> ScimResponse:
        """Return the response listing a page of found resources."""
        dumped = [
            self.publish(base_url, resource).model_dump(
                scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
                response_parameters=search_request,
            )
            for resource in resources
        ]
        response = parametrize(ListResponse, Union[tuple(self.get_models())])(  # noqa: UP007
            total_results=total_results,
            items_per_page=len(dumped),
            start_index=search_request.start_index,
            resources=dumped,
        )
        return ScimResponse(
            HTTPStatus.OK, response.model_dump(scim_ctx=Context.RESOURCE_QUERY_RESPONSE)
        )

    # -- Replacement ----------------------------------------------------

    def read_replacement(
        self, resource_type: ResourceType, body: bytes, content_type: str | None
    ) -> Resource[Any]:
        """Validate the body of a replacement request."""
        return self.read_body(
            self.get_model(resource_type),
            body,
            content_type,
            Context.RESOURCE_REPLACEMENT_REQUEST,
        )

    def apply_replacement(
        self,
        current: Resource[Any],
        replacement: Resource[Any],
        conditions: Conditions,
    ) -> Resource[Any] | None:
        """Apply a replacement to a stored resource.

        :return: The new state of the resource, or :data:`None` when the
            replacement changes nothing. Such a PUT keeps meta.lastModified and
            the ETag.
        """
        self.check_preconditions(current, "PUT", conditions)
        return replacement if replacement.replace(current) else None

    def replacement_response(
        self,
        base_url: str,
        resource: Resource[Any],
        response_parameters: ResponseParameters[Any],
    ) -> ScimResponse:
        """Return the response to a replacement."""
        return self.resource_response(
            self.publish(base_url, resource),
            Context.RESOURCE_REPLACEMENT_RESPONSE,
            response_parameters,
        )

    # -- Patch ----------------------------------------------------------

    def ensure_patch_supported(self) -> None:
        """Refuse with a 501 a PATCH when the configuration does not support it."""
        self.ensure_supported(self.config.patch, "PATCH")

    def read_patch(
        self, resource_type: ResourceType, body: bytes, content_type: str | None
    ) -> "PatchOp[Any]":
        """Validate the body of a PATCH request."""
        self.ensure_patch_supported()
        return self.read_body(
            parametrize(PatchOp, self.get_model(resource_type)),
            body,
            content_type,
            Context.RESOURCE_PATCH_REQUEST,
        )

    def apply_patch(
        self, current: Resource[Any], patch_op: "PatchOp[Any]", conditions: Conditions
    ) -> Resource[Any] | None:
        """Apply a PATCH to a stored resource.

        :return: The new state of the resource, or :data:`None` when the
            PATCH changes nothing. Such a PATCH keeps meta.lastModified and the
            ETag.
        """
        self.check_preconditions(current, "PATCH", conditions)
        return current if patch_op.patch(current) else None

    def patch_response(
        self,
        base_url: str,
        resource: Resource[Any],
        response_parameters: ResponseParameters[Any],
    ) -> ScimResponse:
        """Return the response to a PATCH.

        :rfc:`RFC 7644 §3.5.2 <7644#section-3.5.2>`: a PATCH MAY answer 204 when
        no attributes were requested.
        """
        published = self.publish(base_url, resource)
        if (
            not response_parameters.attributes
            and not response_parameters.excluded_attributes
        ):
            assert published.meta is not None
            headers = {"ETag": published.meta.version} if published.meta.version else {}
            return ScimResponse(HTTPStatus.NO_CONTENT, None, headers)
        return self.resource_response(
            published, Context.RESOURCE_REPLACEMENT_RESPONSE, response_parameters
        )

    # -- Deletion -------------------------------------------------------

    def check_deletion(self, current: Resource[Any], conditions: Conditions) -> None:
        """Evaluate the conditional headers of a deletion."""
        self.check_preconditions(current, "DELETE", conditions)

    @staticmethod
    def deletion_response() -> ScimResponse:
        """Return the 204 response to a deletion."""
        return ScimResponse(HTTPStatus.NO_CONTENT)

    # -- Bulk -----------------------------------------------------------

    def read_bulk(self, body: bytes, content_type: str | None) -> BulkPlan:
        """Validate a bulk request against the limits of the service, and plan its operations (:rfc:`RFC 7644 §3.7 <7644#section-3.7>`)."""
        self.ensure_supported(self.config.bulk, "Bulk")
        bulk = cast(Bulk, self.config.bulk)
        self.ensure_bulk_payload_size(len(body))

        bulk_request = self.read_body(
            parametrize(BulkRequest, Union[tuple(self.get_models())]),  # noqa: UP007
            body,
            content_type,
            Context.BULK_REQUEST,
        )
        operations = bulk_request.operations or []
        if bulk.max_operations is not None and len(operations) > bulk.max_operations:
            raise PayloadTooLargeException(
                detail=f"The number of operations exceeds the maxOperations ({bulk.max_operations})"
            )
        return BulkPlan(
            cast(list[BulkOperation[Resource[Any]]], operations),
            bulk_request.fail_on_errors,
        )

    @staticmethod
    def bulk_outcome(
        base_url: str, operation: BulkOperation[Resource[Any]]
    ) -> dict[str, Any]:
        """Start the outcome of a bulk operation, before it runs.

        :rfc:`RFC 7644 §3.7.3 <7644#section-3.7.3>` requires a location for
        every operation but a failed POST. Until the operation is located, its
        location is the URL of its path, so that an operation with an unknown
        endpoint or an unresolved reference still has one.
        """
        outcome: dict[str, Any] = {
            "method": operation.method,
            "bulk_id": operation.bulk_id,
        }
        if operation.method != BulkOperation.Method.post:
            path = (operation.path or "").lstrip("/")
            outcome["location"] = f"{base_url.rstrip('/')}/{path}"
        return outcome

    def route_bulk_operation(
        self, operation: BulkOperation[Resource[Any]]
    ) -> ResourceType | None:
        """Return the resource type a bulk operation acts on.

        The path of the operation is routed as the path of a single request,
        so that the operation fails with the same status
        (:rfc:`RFC 7644 §3.7.3 <7644#section-3.7.3>`). An operation without a
        valid method or path keeps its validation error, and has no resource
        type.

        :raises ~scim2_models.NotFoundException: When no endpoint has the path
            of the operation, or no resource type is served at it.
        :raises ~scim2_server.errors.MethodNotAllowedException: When the
            endpoint does not support the method of the operation.
        :raises ~scim2_models.InvalidValueException: When the path is not a
            resource type endpoint or a resource, such as ``/Me`` or a search
            (:rfc:`RFC 7644 §3.7 <7644#section-3.7>`).
        """
        if operation.method is None or operation.path is None:
            return None
        target = match(operation.method, operation.path)
        if target.me or target.operation not in BULK_OPERATIONS.values():
            raise InvalidValueException(
                detail="A bulk operation must target a resource type endpoint or a resource"
            )
        return self.resource_type_at(cast(str, target.endpoint))

    def locate_bulk_operation(
        self,
        base_url: str,
        resource_type: ResourceType | None,
        operation: BulkOperation[Resource[Any]],
        outcome: dict[str, Any],
    ) -> None:
        """Locate the resource of a resolved bulk operation.

        The location is set before the operation runs, so a failure still
        knows it. A POST only gets a location when it succeeds.
        """
        if (
            resource_type is not None
            and operation.resource_id
            and operation.method != BulkOperation.Method.post
        ):
            outcome["location"] = self.resource_location(
                base_url, resource_type, operation.resource_id
            )

    @staticmethod
    def bulk_validation_failure(
        operation: BulkOperation[Resource[Any]], outcome: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return the outcome of a bulk operation that failed its validation, if it did."""
        if not isinstance(operation.response, Error):
            return None
        return {**outcome, "status": operation.status, "response": operation.response}

    def bulk_failure(
        self, outcome: dict[str, Any], exception: Exception
    ) -> dict[str, Any]:
        """Return the outcome of a bulk operation that raised an exception."""
        error = self.error_of(exception)
        return {**outcome, "status": error.status, "response": error}

    def bulk_success(
        self,
        base_url: str,
        operation: BulkOperation[Resource[Any]],
        outcome: dict[str, Any],
        resource: Resource[Any] | None,
    ) -> dict[str, Any]:
        """Return the outcome of a bulk operation that succeeded."""
        assert operation.method is not None
        outcome["status"] = BULK_SUCCESS_STATUS[operation.method]
        if resource is None:
            return outcome
        published = self.publish(base_url, resource)
        assert published.meta is not None
        outcome["location"] = published.meta.location
        outcome["version"] = published.meta.version
        return outcome

    def bulk_response(self, plan: BulkPlan) -> ScimResponse:
        """Return the response listing the outcome of each operation that ran."""
        response = parametrize(BulkResponse, Union[tuple(self.get_models())])  # noqa: UP007
        return ScimResponse(
            HTTPStatus.OK,
            response.model_validate({"operations": plan.outcomes()}).model_dump(
                scim_ctx=Context.BULK_RESPONSE
            ),
        )

    # -- Discovery ------------------------------------------------------

    @staticmethod
    def forbid_filter(query: Mapping[str, str]) -> None:
        """Refuse a filter on a discovery endpoint.

        :rfc:`RFC 7644 §4 <7644#section-4>`: "If a "filter" is provided, the
        service provider SHOULD respond with HTTP status code 403 (Forbidden)".
        """
        if "filter" in query:
            raise ForbiddenException(
                detail="Discovery endpoints do not support filtering"
            )

    @staticmethod
    def locate(resource: DiscoveryResourceT, location: str) -> DiscoveryResourceT:
        """Return a copy of a discovery resource carrying its meta."""
        meta = Meta(resource_type=type(resource).__name__, location=location)
        return resource.model_copy(update={"meta": meta})

    @staticmethod
    def discovery_response(resource: DiscoveryResourceT) -> ScimResponse:
        """Return the response carrying a discovery resource, with its :mdn:`Content-Location`."""
        assert resource.meta is not None and resource.meta.location is not None
        return ScimResponse(
            HTTPStatus.OK,
            resource.model_dump(),
            {"Content-Location": resource.meta.location},
        )

    def service_provider_config(
        self, location: str, query: Mapping[str, str]
    ) -> ScimResponse:
        """Return the ServiceProviderConfig.

        :param location: The URL of the endpoint.
        """
        self.forbid_filter(query)
        return self.discovery_response(self.locate(self.config, location))

    def resource_types(self, location: str, query: Mapping[str, str]) -> ScimResponse:
        """Return the list of the resource types."""
        self.forbid_filter(query)
        resource_types = self.provider.resource_types
        response = ListResponse[ResourceType](
            total_results=len(resource_types),
            items_per_page=len(resource_types),
            start_index=1,
            resources=[self.locate(r, f"{location}/{r.id}") for r in resource_types],
        )
        return ScimResponse(HTTPStatus.OK, response.model_dump())

    def resource_type(
        self, location: str, resource_type_id: str, query: Mapping[str, str]
    ) -> ScimResponse:
        """Return a single resource type."""
        self.forbid_filter(query)
        for resource_type in self.provider.resource_types:
            if resource_type.id == resource_type_id:
                return self.discovery_response(self.locate(resource_type, location))
        raise NotFoundException(detail=f"Resource type {resource_type_id!r} not found")

    def schemas(self, location: str, query: Mapping[str, str]) -> ScimResponse:
        """Return the list of the schemas."""
        self.forbid_filter(query)
        schemas = self.provider.schemas
        response = ListResponse[Schema](
            total_results=len(schemas),
            items_per_page=len(schemas),
            start_index=1,
            resources=[self.locate(s, f"{location}/{s.id}") for s in schemas],
        )
        return ScimResponse(HTTPStatus.OK, response.model_dump())

    def schema(
        self, location: str, schema_id: str, query: Mapping[str, str]
    ) -> ScimResponse:
        """Return a single schema."""
        self.forbid_filter(query)
        for schema in self.provider.schemas:
            if schema.id == schema_id:
                return self.discovery_response(self.locate(schema, location))
        raise NotFoundException(detail=f"Schema {schema_id!r} not found")

    # -- Errors ---------------------------------------------------------

    @staticmethod
    def error_of(exception: Exception) -> Error:
        """Return the SCIM error of an exception raised while serving a request.

        Any other exception than a :class:`~scim2_models.SCIMException` is
        unexpected, and gives a 500, without its message nor its traceback.
        """
        if isinstance(exception, SCIMException):
            return exception.to_error()
        return Error(status=500, detail="Internal server error")

    def error_response(self, exception: Exception) -> ScimResponse:
        """Return the SCIM error response of an exception raised while serving a request.

        A 401 response carries the :mdn:`WWW-Authenticate` header of
        :meth:`www_authenticate`.
        """
        error = self.error_of(exception)
        response = ScimResponse(HTTPStatus(error.status or 500), error.model_dump())
        if isinstance(exception, MethodNotAllowedException):
            # RFC 9110 §15.5.6: a 405 answer lists the supported methods.
            response.headers["Allow"] = ", ".join(exception.allowed)
        if isinstance(exception, SCIMException) and exception.status == 401:
            challenge = self.www_authenticate(exception)
            if challenge:
                response.headers["WWW-Authenticate"] = challenge
        return response

    def www_authenticate(self, exception: SCIMException) -> str | None:
        """Return the :mdn:`WWW-Authenticate` header of a 401 response (:rfc:`RFC 7644 §2 <7644#section-2>`).

        It holds one challenge per authentication scheme of the service
        provider configuration, for the Bearer and Basic schemes. Override this
        method to announce other schemes, or to add parameters to the
        challenges, such as ``resource_metadata`` (:rfc:`RFC 9728 §5.1 <9728#section-5.1>`).
        """
        schemes = self.config.authentication_schemes or []
        challenges = dict.fromkeys(
            CHALLENGES[scheme.type] for scheme in schemes if scheme.type in CHALLENGES
        )
        return ", ".join(challenges) or None
