from scim2_models import SCIMException


class UnsupportedMediaTypeException(SCIMException):
    """The request body is not JSON.

    Corresponds to HTTP status 415, with no scimType. SCIM payloads are
    ``application/scim+json`` (:rfc:`RFC 7644 §8.1 <7644#section-8.1>`), and
    ``application/json`` is accepted as well.
    """

    status = 415
    _default_detail = "The request body must be application/scim+json"
