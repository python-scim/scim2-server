Bulk requests
=============

This page explains in which order the server runs the operations of a bulk request, and how it
resolves the references between them. :rfc:`RFC 7644 §3.7 <7644#section-3.7>` defines the bulk
requests.

The order of the operations
---------------------------

A bulk request holds several operations. An operation can reference a resource that a POST of
the same request creates, with a ``bulkId:`` value
(:rfc:`RFC 7644 §3.7.2 <7644#section-3.7.2>`). For instance, a request can create a user and a
group, and add the user to the group.

The server runs the operations in the order of the request, except that a POST runs before the
first operation that references it. The response lists the results in the order of the
request.

The server computes this order before it runs anything, in a :class:`~scim2_server.bulk.BulkPlan`.
The plan does no input or output: the synchronous and the asynchronous handlers run the same
plan with the same loop. It walks the references without recursion, so a long chain of
references cannot exhaust the Python stack.

Circular references
-------------------

Two operations can reference each other: group A has group B as a member, and B has A.
:rfc:`RFC 7644 §3.7.1 <7644#section-3.7.1>` says that the server "MUST try to resolve circular
cross-references between resources in a single bulk job but MAY stop after a failed attempt and
instead return HTTP status code 409 (Conflict)".

scim2-server answers 409 to the operations of the cycle. A later version can resolve the cycles:
create the resources without their circular references, then complete them with a PATCH. The
plan is ready for it. It is a list of steps, and a step is not an operation: one operation can
take several steps.

Failures
--------

A failed operation does not fail the request. Its result carries a status and an error, and the
next operations run. ``failOnErrors`` caps the number of failures the client accepts. Once it
is reached, the remaining operations do not run, and the response lists the results of the
operations that ran.

A failed operation keeps the location of its resource, when it has one.
:rfc:`RFC 7644 §3.7.3 <7644#section-3.7.3>` requires it for every operation but a failed POST.

The limits
----------

The :class:`~scim2_models.ServiceProviderConfig` announces two limits: ``maxOperations`` and
``maxPayloadSize``. A request beyond either limit fails as a whole, with a 413. The service
checks the size of the body it receives. The integration also stops reading a body larger than
the limit, so that a large body does not fill the memory first.
