scim2-server
============

scim2-server serves the SCIM protocol of :rfc:`RFC 7643 <7643>` and :rfc:`RFC 7644 <7644>` over
a storage that the application chooses. It validates the requests, applies them to the
resources, and builds the responses, with :doc:`scim2-models <scim2_models:index>`. The application only reads and writes the
resources, in a database or anywhere else.

scim2-server is for projects that need to:

- serve a standalone SCIM server: the :doc:`overview` builds one, and
  :doc:`how-to/deploy-the-server` puts it in production;
- add SCIM to an existing application, with SCIM models that describe its data:
  :doc:`how-to/serve-an-existing-data-model` maps its tables to SCIM resources, and
  :doc:`how-to/integrate-a-web-framework` serves them from its web framework;
- test a SCIM client against a working server: the :doc:`scim2-server command <reference/cli>`
  serves one from the command line or from a container, and
  `pytest-scim2-server <https://github.com/pytest-dev/pytest-scim2-server>`_ serves one as a
  pytest fixture.

.. doctest::

    >>> from scim2_server.handler import ScimHandler
    >>> from scim2_server.memory import InMemoryStorage
    >>> from scim2_server.requests import ScimRequest
    >>> from scim2_server.service import ScimService
    >>> from scim2_server.utils import load_default_provider

    >>> handler = ScimHandler(ScimService(load_default_provider()), InMemoryStorage())
    >>> response = handler.handle(
    ...     ScimRequest(
    ...         "POST",
    ...         "https://scim.example/v2",
    ...         "/Users",
    ...         headers={"Content-Type": "application/scim+json"},
    ...         body=b'{"userName": "bjensen"}',
    ...     )
    ... )
    >>> response.status, response.headers["Location"]
    (<HTTPStatus.CREATED: 201>, 'https://scim.example/v2/Users/...')

The library has four layers. An application uses as many as it needs:

- a service, :class:`~scim2_server.service.ScimService`, which applies the rules of SCIM,
  independently of any web framework;
- a handler, :class:`~scim2_server.handler.ScimHandler`, or
  :class:`~scim2_server.handler.AsyncScimHandler` for an asynchronous application, which turns a
  :class:`~scim2_server.requests.ScimRequest` into a
  :class:`~scim2_server.responses.ScimResponse`;
- a storage, :class:`~scim2_server.storage.ScimStorage`, which reads and writes the resources,
  with :class:`~scim2_server.memory.InMemoryStorage` for tests, debugging and demos;
- an integration, such as :class:`~scim2_server.applications.wsgi.WSGIApplication` or
  :class:`~scim2_server.applications.asgi.ASGIApplication`, which need no other dependency.

The ``scim2-server`` command serves a test server.

.. code-block:: shell

   pip install scim2-server

Choose a path
-------------

:doc:`Overview <overview>` introduces the parts of a SCIM server, from the description of the
service to the test server.

:doc:`How-to guides <how-to/index>` show how to complete a specific task, such as connecting a
database or integrating a web framework.

:doc:`Explanation <explanation/index>` gives the reasons behind the design: the split between
the service and the storage, the handling of concurrent writes, and the bulk requests.

:doc:`Reference <reference/index>` lists the public API.

:doc:`Test server <reference/cli>` runs a SCIM server from the command line, for the tests of a SCIM
client in any language.

.. toctree::
    :maxdepth: 2
    :hidden:

    Overview <overview>
    How-to guides <how-to/index>
    Explanation <explanation/index>
    Reference <reference/index>
    Contributing <contributing>
    Changelog <changelog>
