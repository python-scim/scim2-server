Searching a database
====================

This page explains why a storage that searches a database cannot translate a SCIM filter or a
SCIM sort word for word into SQL. It is for developers who write such a storage, as
:doc:`../how-to/write-a-storage` describes. The examples use SQL, but the same traps exist in
other query languages.

The in-memory storage keeps the resources that
:meth:`ScimFilter.match <scim2_models.ScimFilter.match>` keeps, and orders them with
:meth:`SearchRequest.sort <scim2_models.SearchRequest.sort>`. These two methods apply the rules of
:rfc:`RFC 7644 §3.4.2 <7644#section-3.4.2>`. A database storage must give the same answer, from a
query. :doc:`scim2_models:how-to/write-a-filter-transpiler` shows how to turn a filter into a
``WHERE`` clause. The sections below list where a direct translation gives another answer.

Missing values
--------------

SQL compares nothing to ``NULL``. ``title <> 'Boss'`` is unknown when ``title`` is ``NULL``, and
the row is left out. In SCIM, ``title ne "Boss"`` holds for a user without a title. Write each
condition so that it is true or false, never unknown, for instance with an ``IS NULL`` guard.
Then ``NOT`` keeps its meaning too: ``not (title eq "Boss")`` also holds for a user without a
title.

An empty string is a value. ``title pr`` holds for ``"Boss"``, and not for a missing title. A
storage that keeps an empty string must not take it for ``NULL``.

Multi-valued attributes
-----------------------

A multi-valued attribute usually lives in a table of its own. A filter on it holds when one of
its values matches, which takes a subquery: ``emails co "work"`` becomes an ``EXISTS`` on the
email table.

``ne`` reverses this. ``emails ne "x"`` holds when no email is ``x``, so it becomes a
``NOT EXISTS``. ``emails[value ne "x"]`` holds when one email is not ``x``, so it stays an
``EXISTS``.

A value selection, such as ``emails[type eq "work" and primary eq true]``, applies all its
conditions to the same entry. They go in one subquery. Two subqueries would accept a user with a
work email and another primary email.

Strings
-------

A string attribute compares without its case, unless the schema declares it ``caseExact``.
scim2-models compares strings in the form the
:attr:`comparison_key <scim2_models.ScimPolicy.comparison_key>` of the policy gives them. By
default, it normalizes them to NFC and lowers them. Databases lower strings in their own way:

- the ``lower()`` of SQLite only lowers ASCII letters, so ``ÉLOÏSE`` and ``éloïse`` differ;
- the ``lower()`` of PostgreSQL follows the locale of the database, and differs from Python on a
  few letters, such as ``İ`` and the final sigma.

:doc:`scim2_models:explanation/comparisons` describes what a storage has to reproduce.

``co``, ``sw`` and ``ew`` become a ``LIKE``. Escape the ``%`` and ``_`` of the value, or
``title co "100%"`` also matches ``"1000 Files"``. On SQLite, ``LIKE`` ignores the case of ASCII
letters, even for a ``caseExact`` attribute.

Dates
-----

A ``dateTime`` carries an offset (:rfc:`RFC 7643 §2.3.5 <7643#section-2.3.5>`). Some databases
drop it, and return a naive :class:`~datetime.datetime`. A naive date does not compare with the
date of a filter, so ``meta.lastModified gt "…"`` matches nothing. Give the offset back when
reading the dates.

Attributes the storage does not store
-------------------------------------

A storage that does not store an attribute cannot evaluate a filter on it. An empty result would
be a wrong answer. Raise :class:`~scim2_models.InvalidFilterException` instead, and the server
answers 400. Do the same for a sort, with :class:`~scim2_models.InvalidPathException`.

An attribute the resource type does not declare is different. Per
:rfc:`RFC 7644 §3.4.2.1 <7644#section-3.4.2.1>`, it matches no resource. This happens in a search
at the root, where some resource types declare the attribute and others do not.

Sorting
-------

:rfc:`RFC 7644 §3.4.2.3 <7644#section-3.4.2.3>` gives rules that a bare ``ORDER BY column`` does
not follow:

- **Case.** A string compares without its case, unless it is ``caseExact``. Sort on the same
  form as the filters, such as ``lower(column)``. SQLite sorts with the ``BINARY`` collation,
  where every uppercase letter comes before every lowercase one.
- **Missing values.** They come last when ascending, and first when descending. PostgreSQL does
  this by default. SQLite and MySQL take ``NULL`` as the smallest value, which is wrong when
  ascending. Write ``NULLS LAST`` or ``NULLS FIRST`` in the query. MySQL and MariaDB have no
  such clause, and write ``ORDER BY column IS NULL, column``.
- **Multi-valued attributes.** They sort on the value of their ``primary`` entry, or else of
  their first one. This takes a subquery ordered on ``primary``. "The first one" has no meaning
  for rows in no order, unless a column stores that order.

Then end the order with the identifier of the resource. Without it, two resources with the same
sort value come back in any order, and a resource can appear on two pages, or on none.
:doc:`pagination` explains why pages need a total order.

Checking the translation
------------------------

These mistakes give valid queries, which answer another question than the client asked. A
review rarely catches them. The storage contract of :doc:`../how-to/check-a-storage` runs a list
of filters and sorts over users made to fall into these traps. It compares the answers of the
storage with :meth:`ScimFilter.match <scim2_models.ScimFilter.match>` and
:meth:`SearchRequest.sort <scim2_models.SearchRequest.sort>`.

`scim2-sqlalchemy <https://scim2-sqlalchemy.readthedocs.io>`_ is a storage that handles all of
the above with SQLAlchemy.
