from collections.abc import Iterable
from typing import Any

from scim2_models import SCIMException


class UnsupportedMediaTypeException(SCIMException):
    """The request body is not JSON.

    Corresponds to HTTP status 415, with no scimType. SCIM payloads are
    ``application/scim+json`` (:rfc:`RFC 7644 §8.1 <7644#section-8.1>`), and
    ``application/json`` is accepted as well.
    """

    status = 415
    _default_detail = "The request body must be application/scim+json"


class MethodNotAllowedException(SCIMException):
    """The endpoint does not support the method of the request.

    Corresponds to HTTP status 405, with no scimType. The response lists the
    supported methods in its :mdn:`Allow` header (:rfc:`RFC 9110 §15.5.6 <9110#section-15.5.6>`).

    :param allowed: The methods the endpoint supports.
    """

    status = 405
    _default_detail = "The endpoint does not support this method"

    def __init__(self, *, allowed: Iterable[str] = (), **kwargs: Any):
        super().__init__(**kwargs)
        self.allowed = sorted(set(allowed))
