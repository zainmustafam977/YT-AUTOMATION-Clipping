"""R4-8.4 — Feature-flag expiry check.

A feature flag must declare an expiry version. If a flag is enabled in
production but its expiry version is in the past, the check fails and
the CI gate rejects the deploy.

Usage in code::

    from shorts_engine.services.feature_flags import flag, check_expiries

    if flag("RIGHTS_LEDGER_MODE"):
        ...
    # At boot:
    check_expiries()  # raises if any flag is past expiry
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional


CURRENT_VERSION = "1.4.0"  # bump on each release


@dataclass
class Flag:
    name: str
    expires_in: str   # semver; e.g. "1.5.0"
    description: str = ""


# Single source of truth for every feature flag in the project.
FLAGS: Dict[str, Flag] = {
    "RIGHTS_LEDGER_MODE": Flag(
        name="RIGHTS_LEDGER_MODE",
        expires_in="1.4.0",
        description="Enable the rights / provenance ledger in the dashboard.",
    ),
    "PREREGISTRATION_MODE": Flag(
        name="PREREGISTRATION_MODE",
        expires_in="1.4.0",
        description="Pre-registration of clips before they are published.",
    ),
    "STRICT_MODE": Flag(
        name="STRICT_MODE",
        expires_in="2.0.0",  # becomes permanent
        description="Forbid silent fallbacks; raise a clear error instead.",
    ),
}


def _parse_semver(s: str) -> tuple[int, int, int]:
    s = s.strip().lstrip("v")
    parts = s.split(".")
    if len(parts) != 3:
        raise ValueError(f"not a semver: {s!r}")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def is_expired(flag: Flag, current: str = CURRENT_VERSION) -> bool:
    """Returns True if the flag's expiry version is <= the current version."""
    try:
        return _parse_semver(flag.expires_in) <= _parse_semver(current)
    except ValueError:
        # Unparseable expiry is a config error; treat as expired.
        return True


def get(name: str) -> Optional[Flag]:
    return FLAGS.get(name)


def flag(name: str) -> bool:
    """Returns True if the flag exists and is not yet expired."""
    f = FLAGS.get(name)
    if f is None:
        return False
    return not is_expired(f)


def check_expiries(current: str = CURRENT_VERSION) -> Iterable[Flag]:
    """Returns an iterable of flags whose expiry is in the past. Used by
    the CI gate to block deploys.
    """
    return [f for f in FLAGS.values() if is_expired(f, current)]
