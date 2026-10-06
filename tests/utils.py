from collections.abc import Iterable


def compare_dicts(a: dict, b: dict, ignore_keys: Iterable | None = None):
    """Assert that the dictionary a is a subset of b.

    Empty values of a are ignored: a SCIM response omits them.
    """
    if ignore_keys is None:
        ignore_keys = set()
    for k, v in a.items():
        if k in ignore_keys or v in ([], {}):
            continue
        if isinstance(v, dict):
            compare_dicts(v, b[k])
            continue
        assert b[k] == v, f"b[{k}] is '{b[k]}' != '{v}"
