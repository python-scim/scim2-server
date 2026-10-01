import itertools
import json
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING
from typing import Any
from typing import TypeVar
from typing import Union
from typing import cast
from urllib.parse import urljoin

from pydantic import ValidationError
from scim2_models import Bulk
from scim2_models import BulkOperation
from scim2_models import BulkRequest
from scim2_models import BulkResponse
from scim2_models import Context
from scim2_models import Error
from scim2_models import Filter
from scim2_models import InvalidValueException
from scim2_models import ListResponse
from scim2_models import Meta
from scim2_models import Patch
from scim2_models import PatchOp
from scim2_models import Resource
from scim2_models import ResourceType
from scim2_models import ResponseParameters
from scim2_models import Schema
from scim2_models import SCIMException
from scim2_models import ScimProvider
from scim2_models import SearchRequest
from scim2_models import ServiceProviderConfig
from scim2_models import Sort
from werkzeug import Request
from werkzeug import Response
from werkzeug.datastructures import ETags
from werkzeug.exceptions import Forbidden
from werkzeug.exceptions import HTTPException
from werkzeug.exceptions import NotFound
from werkzeug.exceptions import NotImplemented as WerkzeugNotImplemented
from werkzeug.exceptions import PreconditionFailed
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.exceptions import Unauthorized
from werkzeug.http import parse_etags
from werkzeug.http import unquote_etag
from werkzeug.routing import BaseConverter
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.routing.exceptions import RequestRedirect

from scim2_server.backend import Backend
from scim2_server.bulk import BulkJob
from scim2_server.bulk import Resolver
from scim2_server.utils import load_default_service_provider_config
from scim2_server.utils import parametrize

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIEnvironment

SEARCH_REQUEST_PARAMETERS = (
    "attributes",
    "excludedAttributes",
    "filter",
    "sortBy",
    "sortOrder",
    "startIndex",
    "count",
)

DiscoveryResourceT = TypeVar(
    "DiscoveryResourceT", ResourceType, Schema, ServiceProviderConfig
)

BULK_SUCCESS_STATUS = {
    BulkOperation.Method.post: 201,
    BulkOperation.Method.put: 200,
    BulkOperation.Method.patch: 200,
    BulkOperation.Method.delete: 204,
}


class ResourceEndpointConverter(BaseConverter):
    """Match a resource endpoint, but not the endpoints RFC 7644 reserves nor the version prefix.

    A request with a method a reserved endpoint does not support then gets a
    405 answer, instead of being routed to a resource type of that name.
    """

    # A reserved name followed by the end of the path segment is refused.
    regex = (
        r"(?!(?:ServiceProviderConfig|ResourceTypes|Schemas|Bulk|Me|v2)(?![^/]))[^/]+"
    )
    part_isolating = True


class SCIMApplication:
    """A WSGI application implementing a SCIM provider (server)."""

    def __init__(self, backend: Backend, provider: ScimProvider):
        self.bearer_tokens: set[str] = set()
        self.backend = backend
        self.provider = provider
        self.config = provider.config or load_default_service_provider_config()
        self.log = logging.getLogger("SCIMApplication")

        # Register the URL mapping. The endpoint refers to the name of the function to be called in this SCIMApplication ("call_" + endpoint).
        rules = itertools.chain.from_iterable(
            [
                Rule(
                    f"{prefix}/ServiceProviderConfig",
                    endpoint="service_provider_config",
                    methods=("GET",),
                ),
                Rule(
                    f"{prefix}/ResourceTypes",
                    endpoint="resource_types",
                    methods=("GET",),
                ),
                Rule(
                    f"{prefix}/ResourceTypes/<string:resource_type>",
                    endpoint="resource_type",
                    methods=("GET",),
                ),
                Rule(
                    f"{prefix}/Schemas",
                    endpoint="schemas",
                    methods=("GET",),
                ),
                Rule(
                    f"{prefix}/Schemas/<string:schema_id>",
                    endpoint="schema",
                    methods=("GET",),
                ),
                Rule(
                    f"{prefix}/Me",
                    endpoint="me",
                    methods=("GET", "POST", "PUT", "PATCH", "DELETE"),
                ),
                Rule(
                    f"{prefix}/<resource_endpoint:resource_endpoint>",
                    endpoint="resource",
                    methods=("GET", "POST"),
                ),
                Rule(
                    f"{prefix}/<resource_endpoint:resource_endpoint>/.search",
                    endpoint="resource_search",
                    methods=("POST",),
                ),
                Rule(
                    f"{prefix}/<resource_endpoint:resource_endpoint>/<string:resource_id>",
                    endpoint="single_resource",
                    methods=("GET", "PUT", "PATCH", "DELETE"),
                ),
                Rule(
                    f"{prefix}/Bulk",
                    endpoint="bulk",
                    methods=("POST",),
                ),
                Rule(f"{prefix}/", endpoint="query_all", methods=("GET",)),
                Rule(f"{prefix}/.search", endpoint="query_all", methods=("POST",)),
            ]
            for prefix in ("", "/v2")
        )

        self.url_map = Map(
            rules, converters={"resource_endpoint": ResourceEndpointConverter}
        )

    def get_model(self, resource_type: ResourceType) -> type[Resource[Any]]:
        """Return the model of a resource type, its extensions included."""
        return cast(type[Resource[Any]], self.provider.model_for(resource_type))

    def get_models(self) -> list[type[Resource[Any]]]:
        """Return the models of every resource type."""
        return [self.get_model(rt) for rt in self.provider.resource_types]

    def get_resource_type_by_endpoint(self, endpoint: str) -> ResourceType | None:
        """Return the resource type an endpoint serves."""
        return next(
            (
                resource_type
                for resource_type in self.provider.resource_types
                if (resource_type.endpoint or "").lstrip("/").casefold()
                == endpoint.lstrip("/").casefold()
            ),
            None,
        )

    @property
    def etag_supported(self) -> bool:
        """Whether the configuration declares the resources versioned with ETags."""
        return bool(self.config.etag and self.config.etag.supported)

    def publish(self, request: Request, resource: Resource[Any]) -> Resource[Any]:
        """Return a copy of a resource in the form sent to the client.

        Its location is made absolute from the URL the client requested, and
        its version is left out when the service does not support ETags.
        """
        assert resource.meta is not None
        update: dict[str, Any] = {
            "location": urljoin(request.url + "/", resource.meta.location)
        }
        if not self.etag_supported:
            update["version"] = None
        return resource.model_copy(
            update={"meta": resource.meta.model_copy(update=update)}
        )

    @staticmethod
    def etag_header(resource: Resource[Any]) -> dict[str, str]:
        """Return the ETag header of a published resource, if it has a version."""
        assert resource.meta is not None
        return {"ETag": resource.meta.version} if resource.meta.version else {}

    def check_preconditions(
        self,
        resource: Resource[Any],
        method: str,
        if_match: ETags | None = None,
        if_none_match: ETags | None = None,
    ) -> bool:
        """Evaluate the "If-Match" and "If-None-Match" conditions against a resource.

        RFC 7232 §6 evaluates "If-Match" first: a failed "If-Match" answers
        412 whatever the method, a failed "If-None-Match" answers 304 to a GET
        and 412 otherwise.

        :return: :data:`False` when a GET should answer 304 Not Modified.
        :raises PreconditionFailed: When the method must not be performed.
        """
        assert resource.meta is not None
        # A service that does not support ETags has no tag to match: RFC 7232
        # §3.1 fails an If-Match listing tags, and lets "*" pass.
        version, _ = (
            unquote_etag(resource.meta.version) if self.etag_supported else (None, None)
        )
        version = version or ""
        # RFC 7232 §3.1 compares If-Match strongly, which would never match
        # the weak ETags RFC 7644 §3.14 recommends and sends in its example.
        if if_match and not if_match.contains_weak(version):
            raise PreconditionFailed

        if if_none_match and if_none_match.contains_weak(version):
            if method == "GET":
                return False
            raise PreconditionFailed

        return True

    def get_existing_resource(
        self, resource_type: ResourceType, resource_id: str
    ) -> Resource[Any]:
        """Return a stored resource.

        :raises NotFound: When no resource of this type has this identifier.
        """
        resource = self.backend.get_resource(resource_type, resource_id)
        if resource is None:
            raise NotFound
        return resource

    def create(self, resource_type: ResourceType, payload: Any) -> Resource[Any]:
        """Validate a creation payload and store the new resource."""
        resource = self.get_model(resource_type).model_validate(
            payload, scim_ctx=Context.RESOURCE_CREATION_REQUEST
        )
        return self.backend.create_resource(resource_type, resource)

    def replace(
        self,
        resource_type: ResourceType,
        resource_id: str,
        payload: Any,
        if_match: ETags | None = None,
        if_none_match: ETags | None = None,
    ) -> Resource[Any]:
        """Replace a stored resource with a payload and return the stored result."""
        resource = self.get_existing_resource(resource_type, resource_id)
        self.check_preconditions(resource, "PUT", if_match, if_none_match)

        replacement = self.get_model(resource_type).model_validate(
            payload, scim_ctx=Context.RESOURCE_REPLACEMENT_REQUEST
        )
        # A PUT that changes nothing keeps meta.lastModified and the ETag.
        if not replacement.replace(resource):
            return resource
        return self.update(resource_type, replacement)

    def patch(
        self,
        resource_type: ResourceType,
        resource_id: str,
        payload: Any,
        if_match: ETags | None = None,
        if_none_match: ETags | None = None,
    ) -> Resource[Any]:
        """Apply a PATCH payload to a stored resource and return the stored result."""
        self.ensure_supported(self.config.patch, "PATCH")
        patch_operation = parametrize(
            PatchOp, self.get_model(resource_type)
        ).model_validate(payload)
        resource = self.get_existing_resource(resource_type, resource_id)
        self.check_preconditions(resource, "PATCH", if_match, if_none_match)

        # A PATCH that changes nothing keeps meta.lastModified and the ETag.
        if not patch_operation.patch(resource):
            return resource
        return self.update(resource_type, resource)

    def update(
        self, resource_type: ResourceType, resource: Resource[Any]
    ) -> Resource[Any]:
        """Store an updated resource and return the stored result.

        :raises NotFound: When the backend no longer has the resource.
        """
        updated = self.backend.update_resource(resource_type, resource)
        if updated is None:
            raise NotFound
        return updated

    def delete(
        self,
        resource_type: ResourceType,
        resource_id: str,
        if_match: ETags | None = None,
        if_none_match: ETags | None = None,
    ) -> None:
        """Delete a stored resource."""
        resource = self.get_existing_resource(resource_type, resource_id)
        self.check_preconditions(resource, "DELETE", if_match, if_none_match)
        self.backend.delete_resource(resource_type, resource_id)

    def call_single_resource(
        self, request: Request, resource_endpoint: str, resource_id: str, **kwargs: Any
    ) -> Response:
        resource_type = self.get_resource_type_by_endpoint(resource_endpoint)
        if not resource_type:
            raise NotFound

        match request.method:
            case "GET":
                resource = self.publish(
                    request, self.get_existing_resource(resource_type, resource_id)
                )
                if not self.check_preconditions(
                    resource, "GET", request.if_match, request.if_none_match
                ):
                    # RFC 7232 §4.1: a 304 carries the ETag a 200 would have
                    return self.make_response(
                        None, status=304, headers=self.etag_header(resource)
                    )

                response_parameters = self.get_response_parameters(
                    request, self.get_model(resource_type)
                )
                return self.make_response(
                    resource.model_dump(
                        scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
                        response_parameters=response_parameters,
                    )
                )
            case "DELETE":
                self.delete(
                    resource_type, resource_id, request.if_match, request.if_none_match
                )
                return self.make_response(None, 204)
            case "PUT":
                response_parameters = self.get_response_parameters(
                    request, self.get_model(resource_type)
                )
                resource = self.replace(
                    resource_type,
                    resource_id,
                    request.json,
                    request.if_match,
                    request.if_none_match,
                )
                resource = self.publish(request, resource)
                return self.make_response(
                    resource.model_dump(
                        scim_ctx=Context.RESOURCE_REPLACEMENT_RESPONSE,
                        response_parameters=response_parameters,
                    )
                )
            case _:  # "PATCH"
                response_parameters = self.get_response_parameters(
                    request, self.get_model(resource_type)
                )
                resource = self.patch(
                    resource_type,
                    resource_id,
                    request.json,
                    request.if_match,
                    request.if_none_match,
                )
                resource = self.publish(request, resource)
                if (
                    not response_parameters.attributes
                    and not response_parameters.excluded_attributes
                ):
                    # RFC 7644 §3.5.2: a PATCH MAY answer 204 when no
                    # attributes were requested.
                    return self.make_response(
                        None, 204, headers=self.etag_header(resource)
                    )

                return self.make_response(
                    resource.model_dump(
                        scim_ctx=Context.RESOURCE_REPLACEMENT_RESPONSE,
                        response_parameters=response_parameters,
                    )
                )

    @staticmethod
    def get_response_parameters(
        request: Request, model: type[Resource[Any]]
    ) -> ResponseParameters[Any]:
        """Parse the "attributes" and "excludedAttributes" HTTP request parameters."""
        return parametrize(ResponseParameters, model).model_validate(
            {
                key: request.args[key]
                for key in ("attributes", "excludedAttributes")
                if key in request.args
            }
        )

    def build_search_request(
        self, request: Request, models: list[type[Resource[Any]]]
    ) -> SearchRequest[Any]:
        """Construct a SearchRequest object from a werkzeug request.

        :param request: werkzeug request
        :param models: The resource models the queried endpoint serves.
        :return: SearchRequest instance
        """
        if request.method == "POST":
            # This was a POST against /.search, see RFC 7644, Section 3.4.3
            payload = request.json
        else:
            payload = {
                key: request.args[key]
                for key in SEARCH_REQUEST_PARAMETERS
                if key in request.args
            }

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

        search_request = parametrize(
            SearchRequest,
            Union[tuple(models)],  # noqa: UP007
        ).model_validate(payload, scim_ctx=Context.SEARCH_REQUEST)
        search_request.start_index = search_request.start_index or 1
        max_results = self.config.filter.max_results if self.config.filter else None
        if max_results is not None and (
            search_request.count is None or search_request.count > max_results
        ):
            search_request.count = max_results
        return search_request

    def query_resource(
        self, request: Request, resource: ResourceType | None
    ) -> ListResponse[Resource[Any]]:
        models = self.get_models() if resource is None else [self.get_model(resource)]
        search_request = self.build_search_request(request, models)

        total_results, results = self.backend.query_resources(
            search_request=search_request, resource_type=resource
        )
        results = [self.publish(request, r) for r in results]

        resources = [
            s.model_dump(
                scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
                response_parameters=search_request,
            )
            for s in results
        ]

        return parametrize(ListResponse, Union[tuple(self.get_models())])(  # noqa: UP007
            total_results=total_results,
            items_per_page=len(resources),
            start_index=search_request.start_index,
            resources=resources,
        )

    def call_resource(
        self, request: Request, resource_endpoint: str, **kwargs: Any
    ) -> Response:
        resource_type = self.get_resource_type_by_endpoint(resource_endpoint)
        if not resource_type:
            raise NotFound

        match request.method:
            case "GET":
                return self.make_response(
                    self.query_resource(request, resource_type).model_dump(
                        scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
                    )
                )
            case _:  # "POST"
                created_resource = self.publish(
                    request, self.create(resource_type, request.json)
                )
                assert created_resource.meta is not None
                return self.make_response(
                    created_resource.model_dump(
                        scim_ctx=Context.RESOURCE_CREATION_RESPONSE
                    ),
                    status=201,
                    headers={"Location": created_resource.meta.location},
                )

    def call_query_all(self, request: Request, **kwargs: Any) -> Response:
        return self.make_response(
            self.query_resource(request, None).model_dump(
                scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
            )
        )

    def call_resource_search(
        self, request: Request, resource_endpoint: str, **kwargs: Any
    ) -> Response:
        resource_type = self.get_resource_type_by_endpoint(resource_endpoint)
        if not resource_type:
            raise NotFound
        return self.make_response(
            self.query_resource(request, resource_type).model_dump(
                scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
            )
        )

    @staticmethod
    def ensure_supported(
        capability: Patch | Bulk | Filter | Sort | None, operation: str
    ) -> None:
        """Refuse with a 501 an operation the configuration does not declare supported.

        RFC 7644 §3.12 answers 501 when the service provider does not support
        the request operation.
        """
        if capability is None or not capability.supported:
            raise WerkzeugNotImplemented(f"{operation} is not supported")

    @staticmethod
    def ensure_payload_size(request: Request, max_payload_size: int) -> None:
        """Refuse with a 413 a payload larger than max_payload_size, without reading more than that.

        :raises RequestEntityTooLarge: When the payload is too large.
        """
        # Werkzeug refuses a Content-Length above the limit, but silently cuts a
        # streamed body at the limit. One extra byte tells a cut body apart.
        request.max_content_length = max_payload_size + 1
        try:
            too_large = len(request.get_data()) > max_payload_size
        except RequestEntityTooLarge:
            too_large = True

        if too_large:
            raise RequestEntityTooLarge(
                f"The payload exceeds the maxPayloadSize ({max_payload_size} bytes)"
            )

    def call_bulk(self, request: Request, **kwargs: Any) -> Response:
        """Implement the /Bulk endpoint (RFC 7644 §3.7)."""
        self.ensure_supported(self.config.bulk, "Bulk")
        bulk = cast(Bulk, self.config.bulk)

        if bulk.max_payload_size is not None:
            self.ensure_payload_size(request, bulk.max_payload_size)

        bulk_request = parametrize(
            BulkRequest,
            Union[tuple(self.get_models())],  # noqa: UP007
        ).model_validate(request.json, scim_ctx=Context.BULK_REQUEST)
        operations = cast(list[BulkOperation[Resource[Any]]], bulk_request.operations)
        if bulk.max_operations is not None and len(operations) > bulk.max_operations:
            raise RequestEntityTooLarge(
                f"The number of operations exceeds the maxOperations ({bulk.max_operations})"
            )

        results = BulkJob(
            operations,
            bulk_request.fail_on_errors,
            lambda operation, resolve: self.run_bulk_operation(
                request, operation, resolve
            ),
        ).run()
        return self.make_response(
            parametrize(BulkResponse, Union[tuple(self.get_models())])  # noqa: UP007
            .model_validate({"operations": results})
            .model_dump(scim_ctx=Context.BULK_RESPONSE)
        )

    def run_bulk_operation(
        self,
        request: Request,
        operation: BulkOperation[Resource[Any]],
        resolve: Resolver,
    ) -> tuple[dict[str, Any], Resource[Any] | None]:
        """Apply one operation of a bulk job.

        An operation that failed its validation keeps its error, once its
        references are resolved to locate it.

        :return: The outcome of the operation, and the resource it created or updated.
        """
        result: dict[str, Any] = {
            "method": operation.method,
            "bulk_id": operation.bulk_id,
        }

        try:
            operation = resolve(operation)
            resource_type = self.get_resource_type_by_endpoint(operation.endpoint or "")
            if resource_type is not None and operation.resource_id:
                assert resource_type.endpoint is not None
                result["location"] = urljoin(
                    request.url,
                    f"{resource_type.endpoint.strip('/')}/{operation.resource_id}",
                )
            if isinstance(operation.response, Error):
                return {
                    **result,
                    "status": operation.status,
                    "response": operation.response,
                }, None
            resource = self.apply_bulk_operation(
                cast(ResourceType, resource_type), operation
            )
        except Exception as exception:
            error = self.error_from(exception)
            return {**result, "status": error.status, "response": error}, None

        assert operation.method is not None
        result["status"] = BULK_SUCCESS_STATUS[operation.method]
        if resource is None:
            return result, None

        resource = self.publish(request, resource)
        assert resource.meta is not None
        result["location"] = resource.meta.location
        result["version"] = resource.meta.version
        return result, resource

    def apply_bulk_operation(
        self, resource_type: ResourceType, operation: BulkOperation[Resource[Any]]
    ) -> Resource[Any] | None:
        """Apply a validated bulk operation, and return the resource it acted on.

        The data of the operation is already validated, and the resource
        operations take it as it is.
        """
        resource_id = operation.resource_id
        if (operation.method == BulkOperation.Method.post) == bool(resource_id):
            raise InvalidValueException(
                detail="A POST path must target a resource type endpoint, other methods a resource"
            )

        if operation.method == BulkOperation.Method.post or resource_id is None:
            return self.create(resource_type, operation.data)

        if_match = parse_etags(operation.version) if operation.version else None
        match operation.method:
            case BulkOperation.Method.put:
                return self.replace(
                    resource_type, resource_id, operation.data, if_match
                )
            case BulkOperation.Method.patch:
                return self.patch(resource_type, resource_id, operation.data, if_match)
            case _:  # DELETE
                self.delete(resource_type, resource_id, if_match)
                return None

    def call_me(self, request: Request, **kwargs: Any) -> Response:
        """Implement the /Me endpoint.

        RFC 7644, Section 3.11 allows raising a 501 (Not Implemented) if
        the endpoint does not provide this feature.
        """
        raise WerkzeugNotImplemented

    def register_bearer_token(self, token: str) -> None:
        """Register a static bearer token for authentication.

        :param token: Bearer token
        """
        self.bearer_tokens.add(token)

    def check_auth(self, request: Request) -> None:
        """Check the authorization headers."""
        if not self.bearer_tokens:
            return
        if (
            not request.authorization
            or request.authorization.token not in self.bearer_tokens
        ):
            raise Unauthorized

    @staticmethod
    def make_response(content: Any, status: int = 200, **kwargs: Any) -> Response:
        """Construct a werkzeug response from any JSON-serializable content."""
        etag = None
        if content is not None:
            etag = content.get("meta", {}).get("version")
            content = json.dumps(content)
        kwargs.setdefault("headers", {})
        if etag:
            kwargs["headers"].setdefault("ETag", etag)
        kwargs["headers"].setdefault("Cache-Control", "no-cache")
        kwargs["headers"].setdefault("Server", "scim-provider")
        return Response(
            content,
            status=status,
            content_type="application/scim+json",
            **kwargs,
        )

    def error_from(self, exception: Exception) -> Error:
        """Log an exception raised while serving a request and return its SCIM Error."""
        self.log.exception(exception)
        match exception:
            case HTTPException():
                return Error(status=exception.code, detail=exception.description)
            case SCIMException():
                return exception.to_error()
            case ValidationError():
                return Error.from_validation_errors(exception)[0]
            case _:
                return Error(status=500, detail="Internal server error")

    def make_error(self, error: Error) -> Response:
        """Construct a werkzeug response from a SCIM Error."""
        return self.make_response(error.model_dump(), status=int(error.status or 500))

    @staticmethod
    def forbid_filter(request: Request) -> None:
        """RFC 7644, Section 4: "If a "filter" is provided, the service provider SHOULD respond with HTTP status code 403 (Forbidden)"."""
        if "filter" in request.args:
            raise Forbidden

    def call_service_provider_config(self, request: Request, **kwargs: Any) -> Response:
        """Return the ServiceProviderConfig."""
        self.forbid_filter(request)
        return self.make_response(
            self.locate(self.config, request.base_url).model_dump()
        )

    @staticmethod
    def locate(resource: DiscoveryResourceT, location: str) -> DiscoveryResourceT:
        """Return a copy of a discovery resource carrying its meta."""
        meta = Meta(resource_type=type(resource).__name__, location=location)
        return resource.model_copy(update={"meta": meta})

    def call_resource_type(
        self, request: Request, resource_type: str, **kwargs: Any
    ) -> Response:
        """Return a single resource type."""
        self.forbid_filter(request)
        for res in self.provider.resource_types:
            if res.id == resource_type:
                return self.make_response(
                    self.locate(res, request.base_url).model_dump()
                )
        raise NotFound

    def call_schema(self, request: Request, schema_id: str) -> Response:
        """Return a single schema."""
        self.forbid_filter(request)
        for res in self.provider.schemas:
            if res.id == schema_id:
                return self.make_response(
                    self.locate(res, request.base_url).model_dump()
                )
        raise NotFound

    def call_resource_types(self, request: Request, **kwargs: Any) -> Response:
        """Return a ListResponse of all known resource types."""
        self.forbid_filter(request)
        results = self.provider.resource_types
        resp = ListResponse[ResourceType](
            total_results=len(results),
            items_per_page=len(results),
            start_index=1,
            resources=[self.locate(s, f"{request.base_url}/{s.id}") for s in results],
        ).model_dump()
        return self.make_response(resp)

    def call_schemas(self, request: Request, **kwargs: Any) -> Response:
        """Return a ListResponse of all known schemas."""
        self.forbid_filter(request)
        results = self.provider.schemas
        resp = ListResponse[Schema](
            total_results=len(results),
            items_per_page=len(results),
            start_index=1,
            resources=[self.locate(s, f"{request.base_url}/{s.id}") for s in results],
        ).model_dump()
        return self.make_response(resp)

    def wsgi_app(self, request: Request, environ: "WSGIEnvironment") -> Response:
        try:
            urls = self.url_map.bind_to_environ(environ)
            endpoint, args = urls.match()

            if endpoint != "service_provider_config":
                # RFC7643, Section 5: skip authentication for ServiceProviderConfig
                self.check_auth(request)

            # Wrap the entire call in a transaction. Should probably be optimized (use transaction only when necessary).
            # The provider makes its policy the one every payload is read under.
            with self.provider, self.backend:
                response: Response = getattr(self, f"call_{endpoint}")(request, **args)
            return response
        except RequestRedirect as e:
            # urls.match may cause a redirect, handle it as a special case of HTTPException
            self.log.exception(e)
            return e.get_response(environ)
        except Exception as e:
            return self.make_error(self.error_from(e))

    def __call__(
        self, environ: "WSGIEnvironment", start_response: "StartResponse"
    ) -> Iterable[bytes]:
        """Return the actual WSGI server implementation."""
        if environ.get("PATH_INFO", "").endswith(".scim"):
            # RFC 7644, Section 3.8
            # Just strip .scim suffix, the provider always returns application/scim+json
            environ["PATH_INFO"], _, _ = environ["PATH_INFO"].rpartition(".scim")
        request = Request(environ)
        response = self.wsgi_app(request, environ)
        if "Location" not in response.headers:
            # The spec is not explicit about requiring the "Location" header in all responses,
            # but the examples in RFC 7644 include the "Location" header even for responses that
            # did not create a new resource
            response.headers.add("Location", request.url)
        if self.bearer_tokens and not request.authorization:
            # RFC 7644, Section 2
            response.headers.add("WWW-Authenticate", 'Bearer realm="SCIM Provider"')
        return response(environ, start_response)
