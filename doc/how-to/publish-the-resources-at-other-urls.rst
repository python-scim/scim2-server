Publish the resources at other URLs
===================================

Use this guide when the clients reach the resources at another URL than the one the server
receives. For example, an API gateway publishes the resources under another path or another
domain. It assumes a server built as :doc:`integrate-a-web-framework` explains.

Behind a reverse proxy that only forwards the requests, this guide is not needed. Configure the
web framework to read the ``X-Forwarded-*`` headers of the proxy instead: the integration then
passes the URL of the client to the handler.

Set the URL of the resources
----------------------------

The service builds every URL of a resource in one method:
:meth:`~scim2_server.service.ScimService.resource_location`. The ``meta.location`` attribute,
the :mdn:`Location` header of a creation, and the ``location`` of the bulk results all come from
it. Subclass :class:`~scim2_server.service.ScimService`, and override the method. It receives the
root URL of the SCIM endpoints, the resource type, and the identifier of the resource:

.. doctest::

    >>> import os
    >>> from scim2_server.service import ScimService

    >>> class PublicService(ScimService):
    ...     def resource_location(self, base_url, resource_type, resource_id):
    ...         return f"https://scim.example{resource_type.endpoint}/{resource_id}"

Pass the service to the handler, built upon the same provider. The example below passes it to
the test application of scim2-server, which builds the handler:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. doctest::

          >>> from scim2_server.applications.wsgi import WSGIApplication
          >>> from scim2_server.memory import InMemoryStorage
          >>> from scim2_server.utils import load_default_provider

          >>> provider = load_default_provider()
          >>> service = PublicService(provider, secret=os.environ["SCIM_SECRET"])
          >>> app = WSGIApplication(InMemoryStorage(), provider, service=service)

   .. tab-item:: Async
      :sync: async

      .. doctest::

          >>> from scim2_server.applications.asgi import ASGIApplication
          >>> from scim2_server.memory import AsyncInMemoryStorage

          >>> async_app = ASGIApplication(AsyncInMemoryStorage(), provider, service=service)

A created resource then carries the URL of the service:

.. doctest::

    >>> from scim2_client.engines.wsgi import WSGISCIMClient
    >>> from scim2_models import User

    >>> scim_client = WSGISCIMClient(app, base_url="http://localhost/v2")
    >>> scim_client.discover()
    >>> user = scim_client.create(User(user_name="bjensen"))
    >>> user.meta.location == f"https://scim.example/Users/{user.id}"
    True

The routes of the server do not change: they stay those of
:rfc:`RFC 7644 §3.2 <7644#section-3.2>`.
