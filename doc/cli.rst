Test server
===========

The ``scim2-server`` command runs a SCIM server for the tests or the demo of a SCIM client,
whatever the language of the client. For the tests of a Python project,
`pytest-scim2-server <https://github.com/pytest-dev/pytest-scim2-server>`_ provides the same
server as a pytest fixture.

The command serves a :class:`~scim2_server.wsgi.WSGIApplication` over an
:class:`~scim2_server.memory.InMemoryStorage`, with the WSGI server of the standard library. The
server keeps the resources in memory. They are lost when it stops. `Options`_ lists every
option of the command.

Start the server
----------------

#. Install scim2-server:

   .. code-block:: console

       $ pip install scim2-server

#. Start the server. It listens on ``127.0.0.1:8080`` by default:

   .. code-block:: console

       $ scim2-server --hostname 0.0.0.0 --port 8080

#. Point the client at ``http://<HOST>:8080/v2``, where ``<HOST>`` is the address of the
   machine.

Serve other resource types
--------------------------

By default, the server serves the users and the groups of :rfc:`RFC 7643 <7643>`, with the
enterprise user extension. To serve other resources, pass JSON files in the format of the
discovery endpoints:

- ``--schema``: a list of :class:`~scim2_models.Schema` objects;
- ``--resource-type``: a list of :class:`~scim2_models.ResourceType` objects;
- ``--service-provider-config``: a :class:`~scim2_models.ServiceProviderConfig` object, to turn
  a feature such as the bulk requests or the sorting on or off.

.. code-block:: console

    $ scim2-server --schema schemas.json --resource-type resource-types.json

Require a bearer token
----------------------

Without bearer token, every endpoint is open. Pass each accepted token with
``--bearer-token``. The option can be repeated, and announces the bearer token scheme in the
service provider configuration:

.. code-block:: console

    $ scim2-server --bearer-token s3cret

A request without ``Authorization`` header gets a 401 response, with the
``WWW-Authenticate: Bearer`` header. The ``/ServiceProviderConfig`` endpoint stays open.

Serve several tenants
---------------------

A tenant is an independent set of resources. The first segment of the URL path selects it, as
the URL prefix method of :rfc:`RFC 7644 §6.1 <7644#section-6.1>` describes: ``/a/v2/Users``
and ``/b/v2/Users`` hold separate users. The schemas, the resource types, the configuration
and the bearer tokens are shared.

Pass each tenant with ``--tenant``:

.. code-block:: console

    $ scim2-server --tenant a --tenant b

A request to another tenant gets a 404 response. ``v2`` cannot be a tenant name.

To create the tenants on demand, pass ``--dynamic-tenants``. The first request to an unknown
tenant creates it, with no resources:

.. code-block:: console

    $ scim2-server --dynamic-tenants

Each test of a test suite can then pick a random tenant, and get an empty server. Any client can
create tenants, even without a valid bearer token, and every tenant stays in memory until the
server stops. Serve this option to trusted clients only.

Keep the resources of a run
---------------------------

Pass ``--dump-resources`` with a file name. When the server stops normally, with ``Ctrl+C``, it
writes every resource to the file, as a JSON list. With tenants, the file holds one entry per
tenant:

.. code-block:: console

    $ scim2-server --dump-resources resources.json

Run the server in a container
-----------------------------

Each release publishes an image on the GitHub container registry. The server listens on
``0.0.0.0:8080`` in the container, and the arguments are passed to ``scim2-server``:

.. code-block:: console

    $ docker run --publish 8080:8080 ghcr.io/python-scim/scim2-server --bearer-token s3cret

To build the image, run ``docker build --file Containerfile .`` or ``podman build .`` in the
repository.

Run the server behind a reverse proxy
-------------------------------------

Pass ``--reverse-proxy``. The server then reads the ``X-Forwarded-For``, ``X-Forwarded-Proto``,
``X-Forwarded-Host``, ``X-Forwarded-Port`` and ``X-Forwarded-Prefix`` headers, and builds the
``meta.location`` of the resources from the URL the client used.

Debug the server
----------------

Pass ``--debug`` to log the WSGI environment of each request, with its headers.

.. warning::

   The logged environment holds the bearer tokens. Never use ``--debug`` on a server reachable by
   others.

Options
-------

.. argparse::
   :module: scim2_server.cli
   :func: build_parser
   :prog: scim2-server
   :nodescription:
   :nodefault:
