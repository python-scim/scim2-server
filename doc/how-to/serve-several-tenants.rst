Serve several tenants
=====================

Use this guide to serve several independent sets of resources from a single application, one
per tenant. It assumes an integration built with :doc:`integrate-a-web-framework`.

Each tenant has its own resources: uniqueness, filters and paging only consider the resources
of the tenant. The schemas, the resource types and the service are shared. Give each tenant its
own storage, or a storage bound to the tenant, and build the handler upon it.

Select the tenant from the URL
------------------------------

With the URL prefix method of :rfc:`RFC 7644 §6.1 <7644#section-6.1>`, the first segment of the
path selects the tenant: ``/a/scim/v2/Users`` and ``/b/scim/v2/Users`` hold separate users.

The following sketch routes the requests with Flask and FastAPI. ``storage_of`` stands for the
code that returns the storage of a tenant, and raises :class:`~scim2_models.NotFoundException`
for an unknown tenant. ``scim_request`` builds the request as :doc:`integrate-a-web-framework`
does, with the tenant in the root URL:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          @app.route("/<tenant>/scim/v2/<path:path>", methods=METHODS)
          def serve(tenant, path):
              handler = ScimHandler(service, storage_of(tenant))
              return to_response(handler.handle(scim_request(tenant)))

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          @app.api_route("/{tenant}/scim/v2/{path:path}", methods=METHODS)
          async def serve(tenant: str, request: Request):
              handler = AsyncScimHandler(service, storage_of(tenant))
              return to_response(await handler.handle(await scim_request(request, tenant)))

The root URL includes the tenant, such as ``https://example.com/a/scim/v2``, so that the
locations of the resources point to their tenant.

Select the tenant from the client
---------------------------------

Per :rfc:`RFC 7644 §6.1 <7644#section-6.1>`, the service provider can also infer the tenant
from the authenticated client. The URLs then hold no tenant. Build the storage from the client, as
:doc:`authenticate-the-clients` does to restrict the access to some resources.
