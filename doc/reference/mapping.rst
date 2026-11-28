Mapping core
============

The base of the storages that map SCIM resources to the records of an ORM. It holds the SCIM
rules, and a backend gives the primitives of its ORM. scim2-django and scim2-sqlalchemy build on
it.

.. warning::

   This API follows the needs of these backends, and may change in any minor version.

.. automodule:: scim2_server.mapping

Backend hooks
-------------

A backend subclasses :class:`~scim2_server.mapping.ResourceMapping` and overrides these methods:

- ``_is_target(value)`` tells whether a value is a target of the ORM, such as a field;
- ``_check_declared(path, binding, value, holder)`` refuses a value the backend cannot map, by
  raising :class:`ValueError`;
- ``_build_column(path, binding, value, owner)`` and ``_build_collection(path, binding,
  declaration)`` build the descriptors, and yield a :class:`~scim2_server.mapping.MappingProblem`
  for a target that does not fit;
- ``_check_record()`` and ``_fields_needing_value()`` give the problems and the fields of the
  record;
- ``_links_to(collection, target)`` tells whether a Link holds the records of a target mapping;
- ``_read(column, record)``, ``_related(collection, record)``, ``_counter(record)`` and
  ``_holds_id(record, value)`` read the records.

The backend calls ``_resolve()`` once its models are ready, and gets the problems of the mapping.
A storage then calls ``_targets(mappings)`` on every mapping, then ``_bind(targets)``, so that a
refused mapping binds nothing.

Declaration
-----------

.. automodule:: scim2_server.mapping.declaration
   :members: Attribute, Many, Entry, Link

Resolution
----------

.. autoclass:: scim2_server.mapping.ResourceMapping
   :members: schemas, unique_attributes

.. autoclass:: scim2_server.mapping.MappingProblem

.. automodule:: scim2_server.mapping.descriptors
   :members: Storage, Column, Collection, EntryColumns

Filters and sorts
-----------------

.. automodule:: scim2_server.mapping.filters
   :members: Predicates, where, compare, present, entry_present, sort_target, holds

Reading and writing
-------------------

.. automodule:: scim2_server.mapping.conversion
   :members: to_scim, version_hash, check_storable, write_plan, keeps, dates, entries, link_entries, assign_links, same_type, comparable
