WSGI and ASGI applications
==========================

Two integrations with no dependency, for a WSGI or an ASGI server. Their hooks take a
:class:`~scim2_server.requests.ScimRequest`, and return a
:class:`~scim2_server.responses.ScimResponse`.

.. autoclass:: scim2_server.application.BaseApplication
   :members: check_auth, get_subject, handle_exception, finalize_response, split_path

.. autoclass:: scim2_server.wsgi.WSGIApplication
   :members: dispatch_request, serve, read_request, read_body, get_base_url

.. autoclass:: scim2_server.asgi.ASGIApplication
   :members: dispatch_request, serve, read_request, get_base_url

.. autoclass:: scim2_server.wsgi.TenantDispatcher
   :members: select_tenant, is_valid_tenant, get_application

.. autoclass:: scim2_server.wsgi.ForwardedHeaders
