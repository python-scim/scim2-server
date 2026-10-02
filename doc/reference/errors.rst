Errors
======

The errors of the protocol are the exceptions of scim2-models, such as
:class:`~scim2_models.NotFoundException`. scim2-server adds the HTTP errors that
:rfc:`RFC 7644 §3.12 <7644#section-3.12>` does not list.

.. autoclass:: scim2_server.errors.UnsupportedMediaTypeException

.. autoclass:: scim2_server.errors.MethodNotAllowedException
