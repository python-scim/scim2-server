"""Check the server with scim2-tester.

https://github.com/python-scim/scim2-tester
"""

import pytest
from scim2_client.engines.wsgi import WSGISCIMClient
from scim2_models import Bulk
from scim2_models import ChangePassword
from scim2_models import ETag
from scim2_models import Filter
from scim2_models import Patch
from scim2_models import ScimProvider
from scim2_models import Sort
from scim2_tester.testing import ScimServerContract


class TestServer(ScimServerContract):
    """The server passes the checks of scim2-tester.

    The checks run with the sync and the async handlers, under the default
    configuration, then under a configuration that announces no optional
    feature.
    """

    @pytest.fixture(params=["default", "minimal"])
    def scim_provider(self, request, scim_provider):
        if request.param == "default":
            return scim_provider

        config = scim_provider.config.model_copy(
            update={
                "patch": Patch(supported=False),
                "bulk": Bulk(supported=False),
                "filter": Filter(supported=False),
                "change_password": ChangePassword(supported=False),
                "sort": Sort(supported=False),
                "etag": ETag(supported=False),
            }
        )
        return ScimProvider(
            models=scim_provider.models,
            resource_types=scim_provider.resource_types,
            config=config,
        )

    @pytest.fixture
    def scim_client(self, app):
        app.propagate_exceptions = True
        return WSGISCIMClient(app, base_url="http://localhost/v2")
