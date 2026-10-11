"""
Pipeline Failure Taxonomy and Error Hierarchy.
Distinguishes Transient errors (retriable with exponential backoff + jitter)
from Permanent errors (immediate quarantine without wasting retries).
"""

from typing import Optional, Dict, Any


class PipelineError(Exception):
    """Base exception for all pipeline orchestration and stage execution failures."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return self.message


class TransientError(PipelineError):
    """
    Transient failure: network timeouts, rate limit throttles (HTTP 429), temporary
    worker OOM/CUDA pressure, or non-deterministic sampling glitches.
    Eligible for retry with exponential backoff and jitter.
    """
    pass


class PermanentError(PipelineError):
    """
    Permanent failure: missing or deleted source video, revoked/invalid API keys,
    unsupported codecs, filtergraph syntax errors, quota exhaustion, or policy violations.
    Immediately quarantines the job with zero retries burned.
    """
    pass


# Domain-specific Transient Errors
class NetworkTransientError(TransientError):
    """Temporary network socket timeout, connection reset, or 503 gateway error."""
    pass


class RateLimitTransientError(TransientError):
    """Provider API rate limit (RPM/TPM HTTP 429) that clears after backoff."""
    pass


class ComputeTransientError(TransientError):
    """Temporary GPU/CPU worker process exhaustion, CUDA allocation blip, or OOM."""
    pass


# Domain-specific Permanent Errors
class AuthPermanentError(PermanentError):
    """Revoked OAuth tokens, invalid API keys, or forbidden 401/403 access."""
    pass


class MediaPermanentError(PermanentError):
    """Missing media file, corrupted video container, 0-length audio, or invalid codec."""
    pass


class QuotaPermanentError(PermanentError):
    """Exhausted platform daily quota (e.g. YouTube Data API 10,000 units/day limit)."""
    pass


class PolicyPermanentError(PermanentError):
    """Video blocked by automated policy gate, age restriction, or copyright strike."""
    pass


def classify_exception(exc: Exception) -> PipelineError:
    """
    Classifies an arbitrary exception according to the pipeline failure taxonomy.
    If already a PipelineError, returns it as-is.
    Unknown/unexpected exceptions are treated as TransientError by default.
    """
    if isinstance(exc, PipelineError):
        return exc

    msg = str(exc) or exc.__class__.__name__

    # Check for known permanent indicators in standard exceptions
    permanent_signatures = [
        "FileNotFoundError",
        "quotaExceeded",
        "invalid_grant",
        "token_revoked",
        "unauthorized",
        "forbidden",
        "video unavailable",
        "private video",
        "copyright",
    ]
    lower_msg = msg.lower()
    if any(sig in lower_msg for sig in permanent_signatures):
        return PermanentError(f"Permanent error: {msg}")

    # Default to transient retry
    return TransientError(f"Transient error: {msg}")
