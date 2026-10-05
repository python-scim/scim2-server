from dataclasses import dataclass
from dataclasses import field
from http import HTTPStatus
from typing import Any

SCIM_MEDIA_TYPE = "application/scim+json"


@dataclass
class ScimResponse:
    """The response to a SCIM request, independent of any web framework."""

    status: HTTPStatus
    """The HTTP status of the response."""

    body: dict[str, Any] | None = None
    """The JSON body of the response, if any."""

    headers: dict[str, str] = field(default_factory=dict)
    """The SCIM headers of the response, such as :mdn:`ETag` or :mdn:`Location`."""

    def __post_init__(self) -> None:
        self.headers.setdefault("Content-Type", SCIM_MEDIA_TYPE)
