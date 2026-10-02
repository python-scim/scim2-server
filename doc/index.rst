scim2-server
============

scim2-server serves the SCIM protocol of :rfc:`RFC 7643 <7643>` and :rfc:`RFC 7644 <7644>` over
a storage that the application chooses. It validates the requests, applies them to the
resources, and builds the responses, with
`scim2-models <https://scim2-models.readthedocs.io>`_. The application only reads and writes the
resources, in a database or anywhere else.

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

The library has three layers, and an application uses as many as it needs:

- a core, independent of any web framework, which turns a
  :class:`~scim2_server.requests.ScimRequest` into a
  :class:`~scim2_server.responses.ScimResponse`. :class:`~scim2_server.service.ScimService`
  applies the rules of SCIM, and :class:`~scim2_server.handler.ScimHandler` serves each
  operation, or :class:`~scim2_server.handler.AsyncScimHandler` for an asynchronous
  application;
- a storage interface, :class:`~scim2_server.storage.ScimStorage`, which reads and writes the
  resources, with :class:`~scim2_server.memory.InMemoryStorage` for tests, debugging and demos;
- a WSGI application, :class:`~scim2_server.wsgi.WSGIApplication`, an ASGI application,
  :class:`~scim2_server.asgi.ASGIApplication`, and the ``scim2-server`` command, which serves a
  test server. They need no other dependency.

.. code-block:: shell

   pip install scim2-server

Choose a path
-------------

:doc:`Tutorial <tutorial>` builds a SCIM server with an attribute of its own, and checks it
with scim2-tester.

:doc:`How-to guides <how-to/index>` show how to complete a specific task, such as connecting a
database or integrating a web framework.

:doc:`Explanation <explanation/index>` gives the reasons behind the design: the split between
the service and the storage, the handling of concurrent writes, and the bulk requests.

:doc:`Reference <reference/index>` lists the public API.

:doc:`Test server <cli>` runs a SCIM server from the command line, for the tests of a SCIM
client in any language.

.. toctree::
    :maxdepth: 2
    :hidden:

    Tutorial <tutorial>
    How-to guides <how-to/index>
    Explanation <explanation/index>
    Reference <reference/index>
    Test server <cli>
    Contributing <contributing>
    Changelog <changelog>
