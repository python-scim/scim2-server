from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from typing import Any


@dataclass
class ScimRequest:
    """A request to a SCIM server, independent of any web framework."""

    method: str
    """The HTTP method of the request, such as ``PATCH``, whatever its case."""

    base_url: str
    """The root URL of the SCIM endpoints, as the client sees it, such as
    ``https://example.com/scim/v2``."""

    path: str
    """The path of the request, relative to :attr:`base_url`, such as
    ``/Users/2819c223``."""

    query: Mapping[str, str] = field(default_factory=dict)
    """The query parameters of the request."""

    headers: Mapping[str, str] | Iterable[tuple[str, str]] = field(default_factory=dict)
    """The headers of the request, as a mapping or as a list of pairs. Their
    names are case insensitive."""

    body: bytes = b""
    """The raw body of the request."""

    subject: Any = None
    """The authenticated subject of the request, as the integration sees it.

    The service does not read it, but passes it to the steps an application
    overrides, such as :meth:`~scim2_server.service.ScimService.me_target`.
    It can be the user of the web framework, the claims of a token, or any
    other object.
    """

    @property
    def url(self) -> str:
        """The URL of the request, without its query string."""
        base_url = self.base_url.rstrip("/")
        path = self.path.strip("/")
        return f"{base_url}/{path}" if path else base_url

    def header(self, name: str) -> str | None:
        """Return the value of a header, whatever the case of its name.

        Repeated headers are joined with commas (RFC 9110 §5.3).
        """
        pairs = (
            self.headers.items() if isinstance(self.headers, Mapping) else self.headers
        )
        values = [value for key, value in pairs if key.lower() == name.lower()]
        return ", ".join(values) if values else None
