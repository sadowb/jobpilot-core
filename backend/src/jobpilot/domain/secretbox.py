"""Authenticated encryption for stored job-site passwords (ADR 0031): AES-256-GCM, random 96-bit nonce per seal.

Pure computation: keys are handed in, never read from disk here. The caller binds each ciphertext to its row with
`aad` (origin and username), so a ciphertext copied to another row fails to open."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12


@dataclass(frozen=True, slots=True)
class Sealed:
    key_id: int
    nonce: bytes
    ciphertext: bytes


class SecretBox:
    def __init__(self, keys: Mapping[int, bytes], current: int) -> None:
        if current not in keys:
            raise ValueError("the current key id has no key")
        for key_id, key in keys.items():
            if len(key) != KEY_BYTES:
                raise ValueError(f"key {key_id} must be {KEY_BYTES} bytes")
        self._keys = {key_id: AESGCM(key) for key_id, key in keys.items()}
        self.current = current

    def seal(self, plaintext: str, aad: str) -> Sealed:
        nonce = secrets.token_bytes(NONCE_BYTES)
        return Sealed(self.current, nonce, self._keys[self.current].encrypt(nonce, plaintext.encode(), aad.encode()))

    def open(self, sealed: Sealed, aad: str) -> str:
        """Plaintext, or ValueError when the key id is unknown, the data was altered, or the row binding differs."""
        box = self._keys.get(sealed.key_id)
        if box is None or len(sealed.nonce) != NONCE_BYTES:
            raise ValueError("the stored password cannot be decrypted")
        try:
            return box.decrypt(sealed.nonce, sealed.ciphertext, aad.encode()).decode()
        except (InvalidTag, UnicodeDecodeError):
            raise ValueError("the stored password cannot be decrypted") from None
