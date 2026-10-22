"""The encryption key for stored job-site passwords (ADR 0031): a random 32-byte file, mode 0600, outside the database.

OWASP Cryptographic Storage: keep keys apart from the data they protect, generate them with a CSPRNG, never hard-code
them or keep them in environment variables or git. `data/` is gitignored and `docker/backup.sh` bundles only
`data/vault` and the database dump, so neither the repository nor a backup bundle contains the key."""

from __future__ import annotations

import binascii
import os
import secrets
import stat
from pathlib import Path

from ..domain.secretbox import KEY_BYTES, SecretBox

KEY_FILE = ".vault-key"
CURRENT_KEY_ID = 1


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("The vault key must be a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            os.fchmod(handle.fileno(), 0o600)
        try:
            key = binascii.unhexlify(handle.read().strip())
        except (binascii.Error, ValueError):
            raise RuntimeError("The vault key file is not valid hex") from None
    if len(key) != KEY_BYTES:
        raise RuntimeError("The vault key file has the wrong length")
    return key


def load_vault_key(data_dir: Path) -> bytes:
    """Read the key, creating it on first use. Creation is atomic (write a private temp file, hard-link it into place), so
    two containers starting together never see a half-written key and exactly one key wins."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / KEY_FILE
    if not path.exists():
        temp = data_dir / f".{KEY_FILE}.{secrets.token_hex(8)}.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_bytes(KEY_BYTES).hex())
            try:
                os.link(temp, path)
            except FileExistsError:
                pass  # another process created it first; read theirs
        finally:
            temp.unlink(missing_ok=True)
    return _read(path)


def vault_box(data_dir: Path) -> SecretBox:
    return SecretBox({CURRENT_KEY_ID: load_vault_key(data_dir)}, CURRENT_KEY_ID)
