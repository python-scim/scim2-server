import re
from dataclasses import dataclass

from scim2_models import PreconditionFailedException

# A member of an entity tag list: an optional weak prefix, then a quoted or a
# bare tag, up to the next comma. Quoted tags may hold commas, so the header
# cannot be split on commas first.
ENTITY_TAG = re.compile(r'\s*(?:[Ww]/)?(?:"([^"]*)"|([^,]*?))\s*(?:,|$)')


def opaque_tag(entity_tag: str) -> str:
    """Return the opaque part of an entity tag, without its weak prefix and its quotes."""
    entity_tag = entity_tag.strip()
    if entity_tag[:2] in ("W/", "w/"):
        entity_tag = entity_tag[2:]
    if len(entity_tag) >= 2 and entity_tag[0] == entity_tag[-1] == '"':
        entity_tag = entity_tag[1:-1]
    return entity_tag


@dataclass(frozen=True)
class EntityTags:
    """The entity tags of an "If-Match" or an "If-None-Match" header."""

    tags: frozenset[str] = frozenset()
    """The opaque part of each listed tag."""

    any: bool = False
    """Whether the header is "*", which matches any current representation."""

    @classmethod
    def parse(cls, header: str | None) -> "EntityTags | None":
        """Read the value of a conditional header.

        :return: :data:`None` when the header is missing or empty.
        """
        if not header or not header.strip():
            return None

        tags = set()
        for match in ENTITY_TAG.finditer(header):
            quoted, bare = match.groups()
            if quoted is None and bare == "*":
                return cls(any=True)
            if quoted is not None or bare:
                tags.add(quoted if quoted is not None else bare)
        return cls(tags=frozenset(tags))

    def matches(self, version: str | None) -> bool:
        """Tell whether a resource version matches the header, with the weak comparison.

        RFC 9110 §13.1.1 compares "If-Match" strongly. That would never match
        the weak ETags RFC 7644 §3.14 recommends and sends in its example, so
        both headers are compared weakly.

        :param version: The version of the resource, or :data:`None` when the
            service does not version its resources.
        """
        if self.any:
            return True
        return version is not None and opaque_tag(version) in self.tags


@dataclass(frozen=True)
class Conditions:
    """The raw values of the conditional headers of a request."""

    if_match: str | None = None
    if_none_match: str | None = None

    def check(self, version: str | None, method: str) -> bool:
        """Evaluate the conditions against the version of a resource.

        RFC 9110 §13.2.2 evaluates "If-Match" first: a failed "If-Match"
        answers 412 whatever the method, and a failed "If-None-Match" answers
        304 to a GET and 412 otherwise.

        :param version: The version of the resource, or :data:`None` when the
            service does not version its resources. Such a resource matches no
            listed tag, and "*" still matches it (RFC 9110 §13.1.1).
        :return: :data:`False` when a GET should answer 304 Not Modified.
        :raises ~scim2_models.PreconditionFailedException: When the method must not be performed.
        """
        if_match = EntityTags.parse(self.if_match)
        if if_match is not None and not if_match.matches(version):
            raise PreconditionFailedException

        if_none_match = EntityTags.parse(self.if_none_match)
        if if_none_match is not None and if_none_match.matches(version):
            if method == "GET":
                return False
            raise PreconditionFailedException

        return True


NO_CONDITIONS = Conditions()
"""The conditions of a request without conditional header."""
