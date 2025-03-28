from collections.abc import Iterable


def compare_dicts(a: dict, b: dict, ignore_keys: Iterable | None = None):
    """Assert that the dictionary a is a subset of b."""
    if ignore_keys is None:
        ignore_keys = set()
    for k, v in a.items():
        if k in ignore_keys:
            continue
        if isinstance(v, dict):
            compare_dicts(v, b[k])
            continue
        assert b[k] == v, f"b[{k}] is '{b[k]}' != '{v}"
