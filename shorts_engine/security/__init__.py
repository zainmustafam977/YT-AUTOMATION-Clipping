"""
Security and cryptographic package for YouTube Shorts Automation Engine.
Provides centralized Fernet encryption via secretbox module.
"""

from shorts_engine.security.secretbox import (
    encrypt,
    decrypt,
    rotate,
    get_master_key,
    get_fernet,
    decrypt_legacy_aes_gcm,
)

__all__ = [
    "encrypt",
    "decrypt",
    "rotate",
    "get_master_key",
    "get_fernet",
    "decrypt_legacy_aes_gcm",
]
