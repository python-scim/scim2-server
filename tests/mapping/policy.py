from scim2_models import ScimPolicy


def refuse_exclamation_marks(binding, value):
    if "!" in value:
        raise ValueError("The policy cannot prepare this string")

    return value.lower()


REFUSING = ScimPolicy(comparison_key=refuse_exclamation_marks)
"""A policy that cannot prepare the strings holding an exclamation mark."""
