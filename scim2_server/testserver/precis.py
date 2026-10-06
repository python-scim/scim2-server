"""The PRECIS comparison of the ``--precis`` option of the ``scim2-server`` command."""

from typing import Any

from precis_i18n import get_profile
from scim2_models import ScimPolicy
from scim2_models import default_comparison_key

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"

PROFILES = {
    f"{USER_SCHEMA}:userName": get_profile("UsernameCaseMapped"),
    f"{USER_SCHEMA}:password": get_profile("OpaqueString"),
}


def precis_comparison_key(binding: Any, value: str) -> str:
    """Compare the usernames and the passwords with the PRECIS profiles of RFC 8265.

    Per RFC 7644 §5, the usernames and the passwords are prepared with PRECIS
    before they are compared. The other strings are compared as by default.
    """
    profile = PROFILES.get(binding.urn)
    if profile is None:
        return default_comparison_key(binding, value)
    return str(profile.enforce(value))


PRECIS_POLICY = ScimPolicy(comparison_key=precis_comparison_key)
