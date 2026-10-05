Overview
========

scim2-server serves the **System for Cross-domain Identity Management** (**SCIM**) protocol. It
validates the requests, applies them to the resources, and builds the responses, with
:doc:`scim2-models <scim2_models:index>`.

It does not keep the resources, authenticate the clients, or depend on a web framework: a
storage, the application and an integration do.

The :rfc:`SCIM data model <7643>` and :rfc:`SCIM protocol <7644>` specifications define the
vocabulary used here.

Install scim2-server:

.. code-block:: shell

   pip install scim2-server

This page introduces the parts of a SCIM server, in the order an application meets them. Follow
it in order for a first tour. The :doc:`how-to guides <how-to/index>` cover focused tasks, the
:doc:`explanations <explanation/index>` cover the design of the server, and the
:doc:`reference <reference/index>` lists the complete API.

Describe the service
--------------------

A :class:`~scim2_models.ScimProvider` describes the service: the schemas it knows, the resources
it serves, and the features it supports. :func:`~scim2_server.utils.load_default_provider`
serves the users and the groups of :rfc:`RFC 7643 <7643>`, with every feature:

.. doctest::

    >>> from scim2_server.utils import load_default_provider

    >>> provider = load_default_provider()

Build a provider of its own to serve other attributes. The following service serves the members
of a library. A schema extension adds a card number to the users
(:rfc:`RFC 7643 §3.3 <7643#section-3.3>`). The service refuses the bulk requests, and returns at
most 100 users per search:

.. doctest::

    >>> from scim2_models import URN
    >>> from scim2_models import Extension
    >>> from scim2_models import ResourceType
    >>> from scim2_models import ScimProvider
    >>> from scim2_models import User
    >>> from scim2_server.utils import load_default_service_provider_config

    >>> class LibraryUser(Extension):
    ...     __schema__ = URN("urn:example:params:scim:schemas:extension:library:2.0:User")
    ...     card_number: str | None = None

    >>> config = load_default_service_provider_config()
    >>> config.bulk.supported = False
    >>> config.filter.max_results = 100
    >>> provider = ScimProvider(
    ...     models=[User, LibraryUser],
    ...     resource_types=[ResourceType.from_resource(User[LibraryUser])],
    ...     config=config,
    ... )

The Python name ``card_number`` becomes the SCIM attribute ``cardNumber``.

Serve a request
---------------

A :class:`~scim2_server.handler.ScimHandler` serves the SCIM requests with a
:class:`~scim2_server.service.ScimService` and a storage.
:class:`~scim2_server.memory.InMemoryStorage` keeps the resources in memory. A
:class:`~scim2_server.requests.ScimRequest` holds a request, independent of any web framework:

.. doctest::

    >>> import json
    >>> from scim2_server.handler import ScimHandler
    >>> from scim2_server.memory import InMemoryStorage
    >>> from scim2_server.requests import ScimRequest
    >>> from scim2_server.service import ScimService

    >>> handler = ScimHandler(ScimService(provider), InMemoryStorage())
    >>> member = {
    ...     "schemas": [
    ...         "urn:ietf:params:scim:schemas:core:2.0:User",
    ...         "urn:example:params:scim:schemas:extension:library:2.0:User",
    ...     ],
    ...     "userName": "bjensen",
    ...     "urn:example:params:scim:schemas:extension:library:2.0:User": {
    ...         "cardNumber": "42-1337"
    ...     },
    ... }
    >>> response = handler.handle(
    ...     ScimRequest(
    ...         "POST",
    ...         "https://scim.example/v2",
    ...         "/Users",
    ...         headers={"Content-Type": "application/scim+json"},
    ...         body=json.dumps(member).encode(),
    ...     )
    ... )
    >>> response.status, response.headers["Location"]
    (<HTTPStatus.CREATED: 201>, 'https://scim.example/v2/Users/...')

The server validated the user against the schemas of the provider, and filled its ``id`` and its
``meta`` attribute. Read it back:

.. doctest::

    >>> user_id = response.body["id"]
    >>> response = handler.handle(
    ...     ScimRequest("GET", "https://scim.example/v2", f"/Users/{user_id}")
    ... )
    >>> response.body["urn:example:params:scim:schemas:extension:library:2.0:User"]
    {'cardNumber': '42-1337'}

A failed request raises a :class:`~scim2_models.SCIMException`.
:meth:`~scim2_server.service.ScimService.error_response` turns it into the error response. A bulk
request fails, as the configuration announces:

.. doctest::

    >>> from scim2_models import SCIMException

    >>> try:
    ...     handler.handle(
    ...         ScimRequest(
    ...             "POST",
    ...             "https://scim.example/v2",
    ...             "/Bulk",
    ...             headers={"Content-Type": "application/scim+json"},
    ...             body=b'{"schemas": ["urn:ietf:params:scim:api:messages:2.0:BulkRequest"]}',
    ...         )
    ...     )
    ... except SCIMException as exception:
    ...     response = handler.service.error_response(exception)
    >>> response.status, response.body["detail"]
    (<HTTPStatus.NOT_IMPLEMENTED: 501>, 'Bulk is not supported')

Serve over HTTP
---------------

:class:`~scim2_server.applications.wsgi.WSGIApplication` and
:class:`~scim2_server.applications.asgi.ASGIApplication` serve a storage and a provider over
HTTP, with no other dependency. The ASGI application takes an asynchronous storage, such as
:class:`~scim2_server.memory.AsyncInMemoryStorage`:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. doctest::

          >>> from scim2_server.applications.wsgi import WSGIApplication

          >>> app = WSGIApplication(InMemoryStorage(), provider)

   .. tab-item:: Async
      :sync: async

      .. doctest::

          >>> from scim2_server.applications.asgi import ASGIApplication
          >>> from scim2_server.memory import AsyncInMemoryStorage

          >>> async_app = ASGIApplication(AsyncInMemoryStorage(), provider)

Serve the WSGI application on the local machine with :mod:`wsgiref.simple_server`:

.. code-block:: pycon

    >>> from wsgiref.simple_server import make_server
    >>> make_server("127.0.0.1", 8080, app).serve_forever()  # doctest: +SKIP

In another terminal, read the configuration of the service:

.. tab-set::
   :class: outline

   .. tab-item:: curl
      :sync: curl

      .. code-block:: console

          $ curl -s http://127.0.0.1:8080/v2/ServiceProviderConfig | python -m json.tool
          {
              "schemas": [
                  "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
              ],
              "meta": {
                  "resourceType": "ServiceProviderConfig",
                  "location": "http://127.0.0.1:8080/v2/ServiceProviderConfig"
              },
              "patch": {
                  "supported": true
              },
              "bulk": {
                  "supported": false,
                  "maxOperations": 1000,
                  "maxPayloadSize": 1048576
              },
              "filter": {
                  "supported": true,
                  "maxResults": 100
              },
              "changePassword": {
                  "supported": true
              },
              "sort": {
                  "supported": true
              },
              "etag": {
                  "supported": true
              },
              "authenticationSchemes": []
          }

   .. tab-item:: scim2-cli
      :sync: scim2-cli

      .. code-block:: console

          $ pip install scim2-cli
          $ scim2 --url http://127.0.0.1:8080/v2 query serviceproviderconfig
          {
              "schemas": [
                  "urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"
              ],
              "meta": {
                  "resourceType": "ServiceProviderConfig",
                  "location": "http://127.0.0.1:8080/v2/ServiceProviderConfig"
              },
              "patch": {
                  "supported": true
              },
              "bulk": {
                  "supported": false,
                  "maxOperations": 1000,
                  "maxPayloadSize": 1048576
              },
              "filter": {
                  "supported": true,
                  "maxResults": 100
              },
              "changePassword": {
                  "supported": true
              },
              "sort": {
                  "supported": true
              },
              "etag": {
                  "supported": true
              },
              "authenticationSchemes": []
          }

:doc:`how-to/integrate-a-web-framework` serves SCIM from the web framework of an application,
and :doc:`how-to/deploy-the-server` serves the applications in production.

Keep the resources in a database
--------------------------------

A storage reads and writes the resources. Subclass :class:`~scim2_server.storage.ScimStorage`, or
:class:`~scim2_server.storage.AsyncScimStorage` for an asynchronous server, and write its five
methods. The server validates the requests and applies them before it calls the storage:

.. code-block:: python

    from scim2_server.storage import ScimStorage


    class DatabaseStorage(ScimStorage):
        def get(self, resource_type, resource_id): ...

        def create(self, resource_type, resource): ...

        def update(self, resource_type, resource, *, expected_version=None): ...

        def delete(self, resource_type, resource_id, *, expected_version=None): ...

        def search(self, resource_types, search_request): ...

:doc:`how-to/write-a-storage` writes a complete storage over a SQLite table, and
:doc:`how-to/serve-an-existing-data-model` serves the tables that an application already has.

Check a server
--------------

:func:`scim2_tester.check_server` sends the requests an identity provider would send, and checks
each response against the RFCs. Install it with the Werkzeug engine of scim2-client, which calls
the application directly:

.. code-block:: console

    $ pip install scim2-tester "scim2-client[werkzeug]"

.. doctest::

    >>> from scim2_client.engines.werkzeug import TestSCIMClient
    >>> from scim2_tester import check_server
    >>> from werkzeug.test import Client

    >>> scim_client = TestSCIMClient(Client(app), scim_prefix="/v2")
    >>> scim_client.discover()
    >>> results = check_server(scim_client)
    >>> [result.title for result in results if result.status.name in ("ERROR", "CRITICAL")]
    []

A storage has its own test suite. Subclass
:class:`~scim2_server.testing.ScimStorageContract` in the tests of the storage, and give it a
``storage`` fixture:

.. code-block:: python

    import pytest

    from scim2_server.testing import ScimStorageContract


    class TestDatabaseStorage(ScimStorageContract):
        @pytest.fixture
        def storage(self, provider):
            return DatabaseStorage(provider)

:doc:`how-to/check-a-storage` runs this suite.

Run a test server
-----------------

The ``scim2-server`` command serves an in-memory SCIM server, for the tests of a SCIM client in
any language:

.. code-block:: console

    $ scim2-server --port 8080

Each release also publishes an image on the GitHub container registry:

.. code-block:: console

    $ docker run --publish 8080:8080 ghcr.io/python-scim/scim2-server

`pytest-scim2-server <https://github.com/pytest-dev/pytest-scim2-server>`_ serves the same server
as a pytest fixture. :doc:`reference/cli` lists the options of the command.
