"""The werkzeug adapter of the SCIM server, and the ``scim2-server`` command.

It needs the ``werkzeug`` extra: ``pip install scim2-server[werkzeug]``.
"""

from scim2_server.werkzeug.app import SCIMApplication
from scim2_server.werkzeug.tenants import TenantDispatcher

__all__ = ["SCIMApplication", "TenantDispatcher"]
