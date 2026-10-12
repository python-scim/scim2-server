scim2-server
============

scim2-server serves the SCIM protocol of :rfc:`RFC 7643 <7643>` and :rfc:`RFC 7644 <7644>` over
a storage that the application chooses. It validates the requests, applies them to the
resources, and builds the responses, with :doc:`scim2-models <scim2_models:index>`. The application only reads and writes the
resources, in a database or anywhere else.

scim2-server is for projects that need to:

- add SCIM to a web application: :doc:`how-to/integrate-a-web-framework` explains how to
  serve SCIM from its web framework, and :doc:`how-to/serve-an-existing-data-model` how to map
  its tables to SCIM resources;
- test a SCIM client against a working server: the :doc:`scim2-server command <reference/cli>`
  serves one from the command line or from a container, and
  `pytest-scim2-server <https://github.com/pytest-dev/pytest-scim2-server>`_ serves one as a
  pytest fixture.

.. doctest::

    >>> import os
    >>> from scim2_server.handler import ScimHandler
    >>> from scim2_server.memory import InMemoryStorage
    >>> from scim2_server.requests import ScimRequest
    >>> from scim2_server.service import ScimService
    >>> from scim2_server.utils import load_default_provider

    >>> service = ScimService(load_default_provider(), secret=os.environ["SCIM_SECRET"])
    >>> handler = ScimHandler(service, InMemoryStorage())
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

scim2-server covers four themes. An application uses the ones it needs, and the guides, the
explanations and the reference follow the same themes:

- **HTTP**: a handler, :class:`~scim2_server.handler.ScimHandler` or
  :class:`~scim2_server.handler.AsyncScimHandler`, turns a
  :class:`~scim2_server.requests.ScimRequest` into a
  :class:`~scim2_server.responses.ScimResponse`. An integration turns the requests of a web
  framework into :class:`~scim2_server.requests.ScimRequest`.
  :class:`~scim2_server.applications.wsgi.WSGIApplication` and
  :class:`~scim2_server.applications.asgi.ASGIApplication` serve SCIM without a framework, for
  tests and demos.
- **Service**: :class:`~scim2_server.service.ScimService` applies the rules of SCIM,
  independently of any web framework.
- **Storage**: a :class:`~scim2_server.storage.ScimStorage` reads and writes the resources.
  :class:`~scim2_server.memory.InMemoryStorage` keeps them in memory, for tests, debugging and
  demos.
- **Test server**: the :doc:`scim2-server command <reference/cli>` serves a SCIM server from the
  command line, for the tests of a SCIM client in any language.

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

.. toctree::
    :hidden:
    :titlesonly:

    Overview <overview>

.. toctree::
    :maxdepth: 2
    :hidden:

    How-to guides <how-to/index>
    Explanation <explanation/index>
    Reference <reference/index>

.. toctree::
    :hidden:
    :titlesonly:

    Contributing <contributing>
    Changelog <changelog>
