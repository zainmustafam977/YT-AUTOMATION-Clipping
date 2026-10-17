"""Settings export/import (E-009).

* export_settings() — serialises a known allowlist of
  operator-controlled config to a JSON document. Secrets and
  tokens are NEVER included in the export.
* import_settings(doc, dry_run=True) — validates the
  document against the schema, returns a diff of what would
  change. If dry_run is False, applies the changes.
* Cross-environment key mismatch: the document carries the
  encrypted master key fingerprint; we refuse to import if it
  doesn't match our current key.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from shorts_engine.db.models import SettingsExport


logger = logging.getLogger(__name__)


SCHEMA_VERSION = 1


# Operator-controllable settings we DO export.
EXPORTABLE_KEYS = {
    "ENABLE_YOUTUBE_PUBLISH",
    "ENABLE_TIKTOK_PUBLISH",
    "ENABLE_INSTAGRAM_PUBLISH",
    "QUOTA_DAILY_UNITS",
    "RIGHTS_LEDGER_MODE",
    "PREREGISTRATION_MODE",
    "CIRCUIT_BREAKER_THRESHOLD",
    "LOG_LEVEL",
    "FEATURE_FLAG_CALENDAR",
    "FEATURE_FLAG_AUTOPUBLISH",
}


# Secret-bearing settings we NEVER export.
SECRET_KEYS = {
    "DASHBOARD_SECRET_KEY",
    "DASHBOARD_MASTER_KEY",
    "DATABASE_URL",
    "WEBHOOK_SIGN_KEY",
    "OAUTH_CLIENT_SECRET",
    "ENCRYPTION_KEY",
    "FERNET_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
}


class SettingsIOError(Exception):
    def __init__(self, message: str, code: str = "SETTINGS_IO_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass
class SettingsDiff:
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)
    blocked: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "blocked": self.blocked,
        }


def get_exportable_settings(actor: str = "system") -> Dict[str, Any]:
    """Snapshot the current exportable settings from env + overrides."""
    import os
    snap: Dict[str, Any] = {}
    for key in EXPORTABLE_KEYS:
        snap[key] = os.getenv(key)
    snap["_actor"] = actor
    snap["_exported_at"] = datetime.utcnow().isoformat() + "Z"
    return snap


def _fingerprint(master_key: str) -> str:
    return hashlib.sha256(master_key.encode("utf-8")).hexdigest()[:16]


def export_settings(
    master_key: str,
    actor: str = "system",
    include_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build the export document.

    Includes:
    * the exportable settings snapshot
    * the master-key fingerprint (so the receiving instance
      can refuse to import under a different key)
    * the schema version
    """
    snap = get_exportable_settings(actor=actor)
    if include_keys is not None:
        snap = {k: v for k, v in snap.items() if k in set(include_keys) | {"_actor", "_exported_at"}}
    doc = {
        "schema_version": SCHEMA_VERSION,
        "fingerprint": _fingerprint(master_key),
        "settings": snap,
    }
    return doc


def _validate_doc(doc: Dict[str, Any]) -> None:
    if not isinstance(doc, dict):
        raise SettingsIOError("document must be an object", code="SETTINGS_BAD_SHAPE")
    if "schema_version" not in doc:
        raise SettingsIOError(
            "document missing schema_version", code="SETTINGS_NO_VERSION"
        )
    if int(doc.get("schema_version", 0)) != SCHEMA_VERSION:
        raise SettingsIOError(
            f"unsupported schema_version: {doc.get('schema_version')}",
            code="SETTINGS_VERSION_MISMATCH",
        )
    if "settings" not in doc or not isinstance(doc["settings"], dict):
        raise SettingsIOError(
            "document missing 'settings' object", code="SETTINGS_NO_PAYLOAD"
        )
    if "fingerprint" not in doc:
        raise SettingsIOError("document missing fingerprint", code="SETTINGS_NO_FINGERPRINT")


def _block_secrets(settings: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    blocked: List[str] = []
    safe: Dict[str, Any] = {}
    for k, v in settings.items():
        if k.upper() in SECRET_KEYS:
            blocked.append(k)
            continue
        safe[k] = v
    return safe, blocked


def preview_import(
    doc: Dict[str, Any],
    master_key: str,
) -> SettingsDiff:
    """Dry-run: return a diff of what would change."""
    _validate_doc(doc)
    if doc["fingerprint"] != _fingerprint(master_key):
        raise SettingsIOError(
            "fingerprint does not match current master key (cross-environment key mismatch)",
            code="SETTINGS_KEY_MISMATCH",
        )
    safe, blocked = _block_secrets(doc["settings"])
    diff = SettingsDiff(blocked=blocked)
    import os
    current = {k: os.getenv(k) for k in safe.keys()}
    for k, v in safe.items():
        if k.startswith("_"):
            continue
        if k not in current:
            diff.added.append(k)
            diff.after[k] = v
        elif str(current[k]) != str(v):
            diff.changed.append(k)
            diff.before[k] = current[k]
            diff.after[k] = v
    return diff


def import_settings(
    doc: Dict[str, Any],
    master_key: str,
    dry_run: bool = True,
    actor: str = "system",
    db: Optional[Session] = None,
) -> SettingsDiff:
    """Apply or dry-run an import."""
    diff = preview_import(doc, master_key=master_key)
    if dry_run:
        return diff
    import os
    safe, _ = _block_secrets(doc["settings"])
    for k, v in safe.items():
        if k.startswith("_"):
            continue
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    if db is not None:
        db.add(SettingsExport(
            id=str(hashlib.md5(json.dumps(doc, sort_keys=True).encode()).hexdigest()),
            payload_json=json.dumps(doc, sort_keys=True),
            schema_version=SCHEMA_VERSION,
            created_by=actor,
        ))
        try:
            db.commit()
        except Exception:
            db.rollback()
    return diff
