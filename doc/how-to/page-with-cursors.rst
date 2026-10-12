Page the results with cursors
=============================

Use this guide to let the clients page the search results with the cursors of :rfc:`9865`,
instead of ``startIndex`` (:rfc:`RFC 7644 §3.4.2.4 <7644#section-3.4.2.4>`). It is for
developers whose clients read large collections, such as a directory that a client copies page by
page. It assumes a server built as :doc:`integrate-a-web-framework` explains, with
:class:`~scim2_server.memory.InMemoryStorage`. :doc:`support-cursors-in-a-storage` adds the
cursors to another storage.

A client asks for the first page with an empty ``cursor``. Each response gives a
``nextCursor``, which the client sends back to get the next page, until the last page, which has
none. A response may also give a ``previousCursor`` (:rfc:`RFC 9865 §2 <9865#section-2>`).

Install the cursor extra
------------------------

The server encrypts the cursors with the ``cryptography`` library. Install the ``cursor``
extra:

.. code-block:: console

   $ pip install "scim2-server[cursor]"

Announce the cursors
--------------------

Announce cursor pagination with a :class:`~scim2_models.Pagination` in the ``pagination``
attribute of the :class:`~scim2_models.ServiceProviderConfig` of the provider:

.. doctest::

   >>> from scim2_models import Pagination
   >>> from scim2_server.utils import load_default_provider

   >>> provider = load_default_provider()
   >>> provider.config.pagination = Pagination(
   ...     cursor=True,
   ...     index=True,
   ...     default_page_size=100,
   ...     max_page_size=1000,
   ...     cursor_timeout=3600,
   ... )

The attributes of ``pagination`` decide how the server pages:

- ``cursor`` and ``index`` announce the methods the server accepts. Without ``pagination``, the
  server accepts ``startIndex`` only.
- ``default_pagination_method`` is the method of a search without ``cursor`` or
  ``startIndex``. Without it, the server pages by index when ``index`` is ``True``, as
  :rfc:`RFC 9865 §2.4 <9865#section-2.4>` recommends.
- ``default_page_size`` is the ``count`` of a search without ``count``.
- ``max_page_size`` bounds the ``count`` of every search, as ``maxResults`` of ``filter`` does.
- ``cursor_timeout`` is the number of seconds a cursor stays valid. Without it, the cursors do
  not expire.

Give the service a secret
-------------------------

The server encrypts the cursors with keys that it derives from a secret, so that the clients
can neither read nor forge them. Generate a secret once:

.. code-block:: console

   $ python -c "import secrets; print(secrets.token_urlsafe(32))"

Keep it in the configuration of the deployment, such as an environment variable. Pass it to the
:class:`~scim2_server.service.ScimService`, and the service to the application:

.. doctest::

   >>> import os
   >>> from scim2_server.applications.wsgi import WSGIApplication
   >>> from scim2_server.memory import InMemoryStorage
   >>> from scim2_server.service import ScimService

   >>> service = ScimService(provider, secret=os.environ["SCIM_SECRET"])
   >>> application = WSGIApplication(InMemoryStorage(), provider, service)

Every process of the server needs the same secret: a cursor that one worker issues comes back to
another one. A new secret makes the cursors of the old one invalid, so the clients that are
paging start again from the first page.

Without a secret, or without the ``cursor`` extra, the service refuses to start when the
configuration announces cursor pagination.

Check the pages
---------------

The client sends ``cursor`` and ``count``, with the same filter and the same sort on every
page. With the WSGI engine of :doc:`scim2-client <scim2_client:index>`, which calls the
application directly:

.. doctest::

   >>> from scim2_client.engines.wsgi import WSGISCIMClient
   >>> from scim2_models import SearchRequest
   >>> from scim2_models import User

   >>> scim_client = WSGISCIMClient(application, base_url="http://localhost/v2")
   >>> scim_client.discover()
   >>> for user_name in ("alice", "bob", "carol"):
   ...     _ = scim_client.create(User(user_name=user_name))

   >>> first = scim_client.query(
   ...     User, query_parameters=SearchRequest(cursor="", count=2, sort_by="userName")
   ... )
   >>> [user.user_name for user in first.resources]
   ['alice', 'bob']
   >>> second = scim_client.query(
   ...     User,
   ...     query_parameters=SearchRequest(cursor=first.next_cursor, count=2, sort_by="userName"),
   ... )
   >>> [user.user_name for user in second.resources], second.next_cursor
   (['carol'], None)

A cursor only serves the query it comes from: the server refuses it for another filter, sort or
count, or past ``cursor_timeout``. :meth:`~scim2_server.service.ScimService.read_search` lists
these errors.

The cursors give stable pages: a resource that exists during the whole paging is returned once,
even when other resources are created or deleted between two pages.
:doc:`../explanation/pagination` explains what the cursors guarantee.
