"""
Encryption at rest for sensitive fields (e.g. refresh_token). Uses Fernet (AES-128-CBC).
ENCRYPTION_KEY must be a valid Fernet key (e.g. from cryptography.fernet.Fernet.generate_key()).
"""

from __future__ import annotations

from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


def encrypt_refresh_token(plaintext: str, encryption_key: str | None) -> str:
    """Encrypt refresh token for storage. If encryption_key is None (dev), returns plaintext."""
    if not encryption_key:
        return plaintext
    f = Fernet(encryption_key.encode("ascii"))
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_refresh_token(ciphertext: str, encryption_key: str | None) -> Optional[str]:
    """Decrypt refresh token. If encryption_key is None (dev), returns ciphertext as-is."""
    if not ciphertext:
        return None
    if not encryption_key:
        return ciphertext
    try:
        f = Fernet(encryption_key.encode("ascii"))
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return None
    except Exception:
        return None
