Serve an existing data model
============================

Use this guide to serve over SCIM the data an application already keeps in its own tables. It
assumes :doc:`write-a-storage`, which explains the rules each storage method follows. The
examples serve the members and the books of a library.

Describe the users
------------------

Start from the tables of the application, unchanged. The example starts from this table of
members:

.. literalinclude:: ../_examples/library_storage.py
   :language: python
   :start-at: MEMBERS =
   :end-before: BOOKS =

Map each column of the table to a SCIM attribute. In the example, most columns have a standard
attribute of :class:`~scim2_models.User`:

.. list-table::
   :header-rows: 1

   * - Column
     - SCIM attribute
   * - ``id``
     - ``id``
   * - ``login``
     - ``userName``
   * - ``email``
     - ``emails``, as the primary email
   * - ``first_name``, ``last_name``
     - ``name.givenName``, ``name.familyName``
   * - ``active``
     - ``active``
   * - ``created_at``, ``updated_at``
     - ``meta.created``, ``meta.lastModified``

Declare the columns without a standard attribute in an :class:`~scim2_models.Extension`. Mark the
values that the application assigns itself as read-only: a client cannot set them. In the
example, the library assigns the card numbers:

.. literalinclude:: ../_examples/library_storage.py
   :language: python
   :start-at: class LibraryMember
   :end-before: class Book

Serve a custom resource type
----------------------------

Give the data that fits no standard resource a resource type of its own. In the example, the
library also keeps its books:

.. literalinclude:: ../_examples/library_storage.py
   :language: python
   :start-at: BOOKS =
   :end-before: class LibraryMember

Subclass :class:`~scim2_models.Resource`, with a schema URN of the application. Declare the
attributes with their SCIM characteristics, such as :class:`~scim2_models.Required` or
:class:`~scim2_models.Uniqueness`:

.. literalinclude:: ../_examples/library_storage.py
   :language: python
   :start-at: class Book
   :end-before: Member =

Declare the service
-------------------

Declare every model and every resource type in the provider. The server then serves the users
under ``/Users`` and the books under ``/Books``, and describes them in ``/Schemas`` and
``/ResourceTypes``:

.. doctest::

    >>> from myapp.scim import Book
    >>> from myapp.scim import LibraryMember
    >>> from scim2_models import ResourceType
    >>> from scim2_models import ScimProvider
    >>> from scim2_models import User
    >>> from scim2_server.utils import load_default_service_provider_config

    >>> provider = ScimProvider(
    ...     models=[User, LibraryMember, Book],
    ...     resource_types=[
    ...         ResourceType.from_resource(User[LibraryMember]),
    ...         ResourceType.from_resource(Book),
    ...     ],
    ...     config=load_default_service_provider_config(),
    ... )

Turn a row into a resource
--------------------------

Build the resource from the columns of a row. Use the identifier of the row as the ``id`` of the
resource. Build the version from a column that changes on every write, such as ``updated_at``
in the example:

.. literalinclude:: ../_examples/library_storage.py
   :language: python
   :start-at: def meta
   :end-before: def user_to_row

Each model has its own function. The books only have a title and an ISBN:

.. literalinclude:: ../_examples/library_storage.py
   :language: python
   :pyobject: row_to_book

Turn a resource into a row
--------------------------

Return the columns that the resource sets. Leave out the attributes without a column, such as
``phoneNumbers``: the next read returns the resource without them. Leave out the read-only
attributes too, and assign them in a separate function:

.. literalinclude:: ../_examples/library_storage.py
   :language: python
   :start-at: def user_to_row
   :end-before: def row_to_book

.. literalinclude:: ../_examples/library_storage.py
   :language: python
   :pyobject: book_to_row

Choose the table of a resource type
-----------------------------------

Every storage method receives the resource type of the request. Describe the table of each
resource type: its name, its two conversions, and the function that returns the values the
application assigns on a creation, such as the card numbers of the members. Choose the table
from the ``name`` of the resource type:

.. literalinclude:: ../_examples/library_storage.py
   :language: python
   :start-at: @dataclass
   :end-before: class LibraryStorage

Write the storage
-----------------

On a creation, choose the table, and build the row from the resource and from the assigned
values. Let the table choose the identifier. The column names come from the code, never from the
client, so the query can hold them:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/library_storage.py
         :language: python
         :pyobject: LibraryStorage.create
         :dedent: 4

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/async_library_storage.py
         :language: python
         :pyobject: AsyncLibraryStorage.create
         :dedent: 4

On an update, write the columns of the new state. The assigned values keep their stored value.
The query checks ``expected_version`` against the ``updated_at`` column, as
:doc:`write-a-storage` does with its version column:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. literalinclude:: ../_examples/library_storage.py
         :language: python
         :pyobject: LibraryStorage.update
         :dedent: 4

   .. tab-item:: Async
      :sync: async

      .. literalinclude:: ../_examples/async_library_storage.py
         :language: python
         :pyobject: AsyncLibraryStorage.update
         :dedent: 4

Write the other methods as in :doc:`write-a-storage`, with the table of the resource type:
``get`` reads a row, ``delete`` removes it, and ``search`` reads the rows of each resource type
it receives.

Serve the storage
-----------------

Pass the storage and the provider to the application, with the database of the application:

.. tab-set::
   :class: outline

   .. tab-item:: Sync
      :sync: sync

      .. code-block:: python

          connection = sqlite3.connect("library.sqlite")
          app = WSGIApplication(LibraryStorage(connection), provider)

   .. tab-item:: Async
      :sync: async

      .. code-block:: python

          app = ASGIApplication(AsyncLibraryStorage("library.sqlite"), provider)

The rows already in the tables are served at once: the member of row 1 is the user
``/v2/Users/1``.

:doc:`integrate-a-web-framework` serves the storage from the web framework of the application.
