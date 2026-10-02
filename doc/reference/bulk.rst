Bulk requests
=============

The order of the operations of a bulk request, and the ``bulkId:`` references between them.

.. autoclass:: scim2_server.bulk.BulkPlan
   :members: plan_steps, operation, resolve, record, outcomes, stopped

.. autoclass:: scim2_server.bulk.BulkStep
   :members:
