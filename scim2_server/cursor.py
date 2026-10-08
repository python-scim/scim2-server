"""The cursors of :rfc:`RFC 9865 <9865>`, which hide the position of a page from the client.

This module needs the ``cursor`` extra, which installs ``cryptography``.
"""

import base64
import json
import os
import time
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from scim2_models import ExpiredCursorException
from scim2_models import InvalidCountException
from scim2_models import InvalidCursorException

VERSION = b"\x01"
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
HEADER_SIZE = len(VERSION) + SALT_SIZE + NONCE_SIZE


class Cursors:
    """Seal the position of a page into a cursor, and open the cursor back.

    A cursor is encrypted and authenticated with AES-GCM, so the client can
    neither read nor forge it (:rfc:`RFC 9865 §5.2 <9865#section-5.2>`). It
    holds the query it belongs to, the ``count`` of the query, and the time it
    was issued.

    Each cursor has its own key, derived from the secret and a random salt.
    The number of cursors a secret seals is then not bounded by the number of
    messages AES-GCM allows for a single key.

    :param secret: The secret the keys of the cursors derive from. Every
        process of the server needs the same secret.
    """

    def __init__(self, secret: str | bytes):
        self.secret = secret.encode() if isinstance(secret, str) else secret

    def _aead(self, salt: bytes) -> AESGCM:
        """Return the cipher of the cursors sealed with a salt."""
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=b"scim2-server cursor",
        ).derive(self.secret)
        return AESGCM(key)

    def seal(self, query: str, count: int | None, position: Any) -> str:
        """Return the cursor of a position, for the query and the count it belongs to."""
        content = json.dumps(
            {"q": query, "n": count, "t": int(time.time()), "p": position},
            separators=(",", ":"),
        ).encode()
        salt = os.urandom(SALT_SIZE)
        nonce = os.urandom(NONCE_SIZE)
        sealed = self._aead(salt).encrypt(nonce, content, VERSION)
        token = VERSION + salt + nonce + sealed
        return base64.urlsafe_b64encode(token).rstrip(b"=").decode()

    def open(
        self, cursor: str, query: str, count: int | None, timeout: int | None
    ) -> Any:
        """Return the position a cursor holds.

        :param timeout: The number of seconds a cursor stays valid, if any.
        :raises ~scim2_models.InvalidCursorException: When the cursor was not
            issued by this server, or for another query.
        :raises ~scim2_models.InvalidCountException: When the cursor was
            issued for another ``count``.
        :raises ~scim2_models.ExpiredCursorException: When the cursor is
            older than ``timeout``.
        """
        try:
            token = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        except ValueError:
            raise InvalidCursorException from None

        if token[:1] != VERSION or len(token) < HEADER_SIZE + TAG_SIZE:
            raise InvalidCursorException

        salt = token[len(VERSION) : len(VERSION) + SALT_SIZE]
        nonce = token[len(VERSION) + SALT_SIZE : HEADER_SIZE]
        try:
            content = json.loads(
                self._aead(salt).decrypt(nonce, token[HEADER_SIZE:], VERSION)
            )
        except InvalidTag:
            raise InvalidCursorException from None

        if content["q"] != query:
            raise InvalidCursorException

        if content["n"] != count:
            raise InvalidCountException

        if timeout is not None and time.time() - content["t"] > timeout:
            raise ExpiredCursorException

        return content["p"]
