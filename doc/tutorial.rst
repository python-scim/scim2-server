Tutorial
========

This tutorial builds a SCIM server for a library. Each member of the library has a card number.
The server serves the users only. It adds the card number to them, and announces the features it
supports. At the end, it passes the conformance checks of
`scim2-tester <https://scim2-tester.readthedocs.io>`_, and serves the users over HTTP.

The tutorial keeps the users in memory, and serves them with the WSGI application of
scim2-server. It assumes Python 3.11 or later. It does not cover the storage of the users in a
database: :doc:`how-to/write-a-storage` does. It does not cover the web frameworks either:
:doc:`how-to/integrate-a-web-framework` does.

Set up the environment
----------------------

Create a virtual environment. Install scim2-server, scim2-tester, and the Werkzeug engine of
scim2-client. scim2-tester sends its requests with this engine, and the tutorial uses the test
client of Werkzeug:

.. code-block:: console

    $ python -m venv venv
    $ . venv/bin/activate
    $ pip install scim2-server scim2-tester scim2-client[werkzeug]

Start a Python interpreter in this environment. Every following step runs in the same
interpreter session.

Add an attribute
----------------

The library needs the card number of each member, which no standard schema has. A schema
extension adds it to the users (:rfc:`RFC 7643 §3.3 <7643#section-3.3>`). Declare it with
scim2-models, under a URN of the library:

.. doctest::

    >>> from scim2_models import Extension
    >>> from scim2_models import URN

    >>> class LibraryUser(Extension):
    ...     __schema__ = URN("urn:example:params:scim:schemas:extension:library:2.0:User")
    ...     card_number: str | None = None

The Python name ``card_number`` becomes the SCIM attribute ``cardNumber``.

Describe the service
--------------------

A :class:`~scim2_models.ScimProvider` describes the service. Its ``models`` list the schemas that
the service knows. Its ``resource_types`` list the resources that it serves. This service serves
the users only, with the extension of the library:

.. doctest::

    >>> from scim2_models import ResourceType
    >>> from scim2_models import ScimProvider
    >>> from scim2_models import User

    >>> resource_types = [ResourceType.from_resource(User[LibraryUser])]

The service also announces the features it supports, in its
:class:`~scim2_models.ServiceProviderConfig` (:rfc:`RFC 7643 §5 <7643#section-5>`). Start from the
default configuration, which supports every feature. The library refuses the bulk requests, and
returns at most 100 users per search:

.. doctest::

    >>> from scim2_server.utils import load_default_service_provider_config

    >>> config = load_default_service_provider_config()
    >>> config.bulk.supported = False
    >>> config.filter.max_results = 100

Build the provider:

.. doctest::

    >>> provider = ScimProvider(
    ...     models=[User, LibraryUser], resource_types=resource_types, config=config
    ... )

Start the server
----------------

The server also needs a storage that keeps the users.
:class:`~scim2_server.memory.InMemoryStorage` keeps them in memory.
:class:`~scim2_server.wsgi.WSGIApplication` serves the storage and the provider as a WSGI
application:

.. doctest::

    >>> from scim2_server.memory import InMemoryStorage
    >>> from scim2_server.wsgi import WSGIApplication

    >>> app = WSGIApplication(InMemoryStorage(), provider)

The test client of Werkzeug sends requests to the application directly, without a network
connection. Ask the server which resources it serves, and with which schemas:

.. doctest::

    >>> from werkzeug.test import Client

    >>> client = Client(app)
    >>> response = client.get("/v2/ResourceTypes")
    >>> [resource_type["name"] for resource_type in response.json["Resources"]]
    ['User']
    >>> for schema in client.get("/v2/Schemas").json["Resources"]:
    ...     print(schema["id"])
    urn:ietf:params:scim:schemas:core:2.0:User
    urn:example:params:scim:schemas:extension:library:2.0:User

Create a member
---------------

Create a user with a card number. The attributes of the extension go under its URN, and the URN
goes in ``schemas``:

.. doctest::

    >>> response = client.post(
    ...     "/v2/Users",
    ...     json={
    ...         "schemas": [
    ...             "urn:ietf:params:scim:schemas:core:2.0:User",
    ...             "urn:example:params:scim:schemas:extension:library:2.0:User",
    ...         ],
    ...         "userName": "bjensen",
    ...         "urn:example:params:scim:schemas:extension:library:2.0:User": {
    ...             "cardNumber": "42-1337"
    ...         },
    ...     },
    ...     content_type="application/scim+json",
    ... )
    >>> response.status_code
    201
    >>> response.json["urn:example:params:scim:schemas:extension:library:2.0:User"]
    {'cardNumber': '42-1337'}

The server validated the user against the schemas of the provider, and filled its ``id`` and
its ``meta`` attribute.

A bulk request now answers 501, as the configuration announces:

.. doctest::

    >>> response = client.post(
    ...     "/v2/Bulk",
    ...     json={"schemas": ["urn:ietf:params:scim:api:messages:2.0:BulkRequest"]},
    ...     content_type="application/scim+json",
    ... )
    >>> response.status_code, response.json["detail"]
    (501, 'Bulk is not supported')

Check the server
----------------

scim2-tester sends the requests an identity provider would send. It reads the description of the
service. Then it creates, reads, modifies, searches and deletes users, with their card number. It
checks each response against the RFCs. Its client calls the application directly:

.. doctest::

    >>> from scim2_client.engines.werkzeug import TestSCIMClient
    >>> from scim2_tester import check_server

    >>> scim_client = TestSCIMClient(Client(app), scim_prefix="/v2")
    >>> scim_client.discover()
    >>> results = check_server(scim_client)
    >>> len(results) > 0
    True
    >>> [result.title for result in results if result.status.name in ("ERROR", "CRITICAL")]
    []

No check fails.

Serve it over HTTP
------------------

Serve the application with the WSGI server of the standard library:

.. code-block:: pycon

    >>> from wsgiref.simple_server import make_server
    >>> make_server("127.0.0.1", 8080, app).serve_forever()  # doctest: +SKIP

In another terminal, read the configuration of the service:

.. code-block:: console

    $ curl http://127.0.0.1:8080/v2/ServiceProviderConfig

The server answers with the configuration of the library: ``bulk`` is not supported, and
``filter`` returns at most 100 results. Press ``Ctrl+C`` to stop it.

What was built
--------------

A SCIM server now serves the members of the library, with their card number, and passes the
checks of scim2-tester. To go further:

- :doc:`how-to/write-a-storage` keeps the users in the database of the application;
- :doc:`how-to/integrate-a-web-framework` serves them from any Python web framework;
- :doc:`how-to/authenticate-the-clients` restricts access to known clients;
- :doc:`explanation/architecture` explains the layers of the server.
