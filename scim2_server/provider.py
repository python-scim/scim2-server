import itertools
import json
import logging
import traceback
from typing import Union
from typing import cast
from urllib.parse import urljoin

from pydantic import ValidationError
from scim2_models import Context
from scim2_models import Error
from scim2_models import Filter
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
from werkzeug.exceptions import Forbidden
from werkzeug.exceptions import HTTPException
from werkzeug.exceptions import NotFound
from werkzeug.exceptions import NotImplemented as WerkzeugNotImplemented
from werkzeug.exceptions import PreconditionFailed
from werkzeug.exceptions import Unauthorized
from werkzeug.http import unquote_etag
from werkzeug.routing import Map
from werkzeug.routing import Rule
from werkzeug.routing.exceptions import RequestRedirect

from scim2_server.backend import Backend
from scim2_server.operators import patch_resource
from scim2_server.utils import load_default_service_provider_config

SEARCH_REQUEST_PARAMETERS = (
    "attributes",
    "excludedAttributes",
    "filter",
    "sortBy",
    "sortOrder",
    "startIndex",
    "count",
)


class SCIMApplication:
    """A WSGI application implementing a SCIM provider (server)."""

    def __init__(self, backend: Backend, provider: ScimProvider):
        self.bearer_tokens = set()
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
                    f"{prefix}/<string:resource_endpoint>",
                    endpoint="resource",
                    methods=("GET", "POST"),
                ),
                Rule(
                    f"{prefix}/<string:resource_endpoint>/.search",
                    endpoint="resource_search",
                    methods=("POST",),
                ),
                Rule(
                    f"{prefix}/<string:resource_endpoint>/<string:resource_id>",
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

        self.url_map = Map(rules)

    def get_model(self, resource_type: ResourceType) -> type[Resource]:
        """Return the model of a resource type, its extensions included."""
        return cast(type[Resource], self.provider.model_for(resource_type))

    def get_models(self) -> list[type[Resource]]:
        """Return the models of every resource type."""
        return [self.get_model(rt) for rt in self.provider.resource_types]

    def get_resource_type_by_endpoint(self, endpoint: str) -> ResourceType | None:
        """Return the resource type an endpoint serves."""
        return next(
            (
                resource_type
                for resource_type in self.provider.resource_types
                if resource_type.endpoint.lstrip("/").casefold()
                == endpoint.lstrip("/").casefold()
            ),
            None,
        )

    @staticmethod
    def adjust_location(request: Request, resource: Resource):
        """Make the "meta.location" of a resource absolute, from the URL the client requested."""
        resource.meta.location = urljoin(request.url + "/", resource.meta.location)

    def apply_patch_operation(self, resource: Resource, patch_operation):
        """Apply a PATCH operation to a resource."""
        for op in patch_operation.operations:
            patch_resource(resource, op)

    @staticmethod
    def continue_etag(request: Request, resource: Resource) -> bool:
        """Given a request and a resource, checks whether the ETag matches and allows continuing with the request.

        If the HTTP header "If-Match" is set, the request may only
        continue if the ETag matches. If the HTTP header "If-None-Match"
        is set, the request may only continue if the ETag does not
        match.
        """
        cont = True
        resource_version, _ = unquote_etag(resource.meta.version)
        if request.if_none_match:
            cont &= not request.if_none_match.contains_weak(resource_version)
        if request.if_match:
            cont &= request.if_match.contains_weak(resource_version)
        return cont

    def call_single_resource(
        self, request: Request, resource_endpoint: str, resource_id: str, **kwargs
    ) -> Response:
        resource_type = self.get_resource_type_by_endpoint(resource_endpoint)
        if not resource_type:
            raise NotFound

        match request.method:
            case "GET":
                if resource := self.backend.get_resource(resource_type, resource_id):
                    if self.continue_etag(request, resource):
                        response_parameters = self.get_response_parameters(
                            request, self.get_model(resource_type)
                        )
                        self.adjust_location(request, resource)
                        return self.make_response(
                            resource.model_dump(
                                scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
                                response_parameters=response_parameters,
                            )
                        )
                    else:
                        return self.make_response(None, status=304)
                raise NotFound
            case "DELETE":
                if self.backend.delete_resource(resource_type, resource_id):
                    return self.make_response(None, 204)
                else:
                    raise NotFound
            case "PUT":
                response_parameters = self.get_response_parameters(
                    request, self.get_model(resource_type)
                )
                resource = self.backend.get_resource(resource_type, resource_id)
                if resource is None:
                    raise NotFound
                if not self.continue_etag(request, resource):
                    raise PreconditionFailed

                replacement = self.get_model(resource_type).model_validate(
                    request.json, scim_ctx=Context.RESOURCE_REPLACEMENT_REQUEST
                )
                replacement.replace(resource)
                updated = self.backend.update_resource(resource_type, replacement)
                self.adjust_location(request, updated)
                return self.make_response(
                    updated.model_dump(
                        scim_ctx=Context.RESOURCE_REPLACEMENT_RESPONSE,
                        response_parameters=response_parameters,
                    )
                )
            case _:  # "PATCH"
                self.ensure_supported(self.config.patch, "PATCH")
                payload = request.json
                # MS Entra sometimes passes a "id" attribute
                if "id" in payload:
                    del payload["id"]
                operations = payload.get("Operations", [])
                for operation in operations:
                    if "name" in operation:
                        # MS Entra sometimes passes a "name" attribute
                        del operation["name"]

                ResourceModel = self.get_model(resource_type)
                patch_operation = PatchOp[ResourceModel].model_validate(payload)
                response_parameters = self.get_response_parameters(
                    request, ResourceModel
                )
                resource = self.backend.get_resource(resource_type, resource_id)
                if resource is None:
                    raise NotFound
                if not self.continue_etag(request, resource):
                    raise PreconditionFailed

                self.apply_patch_operation(resource, patch_operation)
                updated = self.backend.update_resource(resource_type, resource)

                if (
                    response_parameters.attributes
                    or response_parameters.excluded_attributes
                ):
                    self.adjust_location(request, updated)
                    return self.make_response(
                        updated.model_dump(
                            scim_ctx=Context.RESOURCE_REPLACEMENT_RESPONSE,
                            response_parameters=response_parameters,
                        )
                    )
                else:
                    # RFC 7644, section 3.5.2:
                    # A PATCH operation MAY return a 204 (no content)
                    # if no attributes were requested
                    return self.make_response(
                        None, 204, headers={"ETag": updated.meta.version}
                    )

    @staticmethod
    def get_response_parameters(
        request: Request, model: type[Resource]
    ) -> ResponseParameters:
        """Parse the "attributes" and "excludedAttributes" HTTP request parameters."""
        return ResponseParameters[model].model_validate(
            {
                key: request.args[key]
                for key in ("attributes", "excludedAttributes")
                if key in request.args
            }
        )

    def build_search_request(
        self, request: Request, models: list[type[Resource]]
    ) -> SearchRequest:
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
        parameters = {key.casefold() for key in payload}
        if "filter" in parameters:
            self.ensure_supported(self.config.filter, "Filtering")
        if parameters & {"sortby", "sortorder"}:
            self.ensure_supported(self.config.sort, "Sorting")

        search_request = SearchRequest[Union[tuple(models)]].model_validate(  # noqa: UP007
            payload, scim_ctx=Context.SEARCH_REQUEST
        )
        search_request.start_index = search_request.start_index or 1
        max_results = self.config.filter.max_results if self.config.filter else None
        if max_results is not None and (
            search_request.count is None or search_request.count > max_results
        ):
            search_request.count = max_results
        return search_request

    def query_resource(self, request: Request, resource: ResourceType | None):
        models = self.get_models() if resource is None else [self.get_model(resource)]
        search_request = self.build_search_request(request, models)

        total_results, results = self.backend.query_resources(
            search_request=search_request, resource_type=resource
        )
        for r in results:
            self.adjust_location(request, r)

        resources = [
            s.model_dump(
                scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
                response_parameters=search_request,
            )
            for s in results
        ]

        return ListResponse[Union[tuple(self.get_models())]](  # noqa: UP007
            total_results=total_results,
            items_per_page=len(resources),
            start_index=search_request.start_index,
            resources=resources,
        )

    def call_resource(
        self, request: Request, resource_endpoint: str, **kwargs
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
                payload = request.json
                resource = self.get_model(resource_type).model_validate(
                    payload, scim_ctx=Context.RESOURCE_CREATION_REQUEST
                )
                created_resource = self.backend.create_resource(resource_type, resource)
                self.adjust_location(request, created_resource)
                return self.make_response(
                    created_resource.model_dump(
                        scim_ctx=Context.RESOURCE_CREATION_RESPONSE
                    ),
                    status=201,
                    headers={"Location": created_resource.meta.location},
                )

    def call_query_all(self, request: Request, **kwargs) -> Response:
        return self.make_response(
            self.query_resource(request, None).model_dump(
                scim_ctx=Context.RESOURCE_QUERY_RESPONSE,
            )
        )

    def call_resource_search(
        self, request: Request, resource_endpoint: str, **kwargs
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
    def ensure_supported(capability: Patch | Filter | Sort | None, operation: str):
        """Refuse with a 501 an operation the configuration does not declare supported.

        RFC 7644 §3.12 answers 501 when the service provider does not support
        the request operation.
        """
        if capability is None or not capability.supported:
            raise WerkzeugNotImplemented(f"{operation} is not supported")

    def call_bulk(self, request: Request, **kwargs):
        """Implement the /Bulk endpoint, which this server does not support."""
        raise WerkzeugNotImplemented("Bulk operations are not supported")

    def call_me(self, request: Request, **kwargs):
        """Implement the /Me endpoint.

        RFC 7644, Section 3.11 allows raising a 501 (Not Implemented) if
        the endpoint does not provide this feature.
        """
        raise WerkzeugNotImplemented

    def register_bearer_token(self, token: str):
        """Register a static bearer token for authentication.

        :param token: Bearer token
        """
        self.bearer_tokens.add(token)

    def check_auth(self, request: Request):
        """Check the authorization headers."""
        if not self.bearer_tokens:
            return
        if (
            not request.authorization
            or request.authorization.token not in self.bearer_tokens
        ):
            raise Unauthorized

    @staticmethod
    def make_response(content, status=200, **kwargs) -> Response:
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

    def make_error(self, error: Error):
        """Construct a werkzeug response from a SCIM Error."""
        return self.make_response(error.model_dump(), status=int(error.status))

    @staticmethod
    def forbid_filter(request: Request):
        """RFC 7644, Section 4: "If a "filter" is provided, the service provider SHOULD respond with HTTP status code 403 (Forbidden)"."""
        if "filter" in request.args:
            raise Forbidden

    def call_service_provider_config(self, request: Request, **kwargs):
        """Return the ServiceProviderConfig."""
        self.forbid_filter(request)
        return self.make_response(
            self.locate(self.config, request.base_url).model_dump()
        )

    @staticmethod
    def locate(resource: ResourceType | Schema | ServiceProviderConfig, location: str):
        """Return a copy of a discovery resource carrying its meta."""
        meta = Meta(resource_type=type(resource).__name__, location=location)
        return resource.model_copy(update={"meta": meta})

    def call_resource_type(self, request: Request, resource_type: str, **kwargs):
        """Return a single resource type."""
        self.forbid_filter(request)
        for res in self.provider.resource_types:
            if res.id == resource_type:
                return self.make_response(
                    self.locate(res, request.base_url).model_dump()
                )
        raise NotFound

    def call_schema(self, request: Request, schema_id: str):
        """Return a single schema."""
        self.forbid_filter(request)
        for res in self.provider.schemas:
            if res.id == schema_id:
                return self.make_response(
                    self.locate(res, request.base_url).model_dump()
                )
        raise NotFound

    def call_resource_types(self, request: Request, **kwargs):
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

    def call_schemas(self, request: Request, **kwargs):
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

    def wsgi_app(self, request: Request, environ):
        try:
            urls = self.url_map.bind_to_environ(environ)
            endpoint, args = urls.match()

            if endpoint != "service_provider_config":
                # RFC7643, Section 5: skip authentication for ServiceProviderConfig
                self.check_auth(request)

            # Wrap the entire call in a transaction. Should probably be optimized (use transaction only when necessary).
            with self.backend:
                response = getattr(self, f"call_{endpoint}")(request, **args)
            return response
        except RequestRedirect as e:
            # urls.match may cause a redirect, handle it as a special case of HTTPException
            self.log.exception(e)
            return e.get_response(environ)
        except HTTPException as e:
            self.log.exception(e)
            return self.make_error(Error(status=e.code, detail=e.description))
        except SCIMException as e:
            self.log.exception(e)
            return self.make_error(e.to_error())
        except ValidationError as e:
            self.log.exception(e)
            return self.make_error(Error.from_validation_errors(e)[0])
        except Exception as e:
            self.log.exception(e)
            tb = traceback.format_exc()
            return self.make_error(Error(status=500, detail=str(e) + "\n" + tb))

    def __call__(self, environ, start_response):
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
