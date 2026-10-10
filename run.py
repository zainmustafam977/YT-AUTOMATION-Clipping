"""
Entry point for the YouTube Shorts Automation Platform.
Initialises Sentry error tracking (if configured) before handing off to the CLI.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _init_sentry() -> None:
    """Initialise Sentry SDK once at startup. No-ops if SENTRY_DSN is not set."""
    try:
        from shorts_engine.config.settings import settings
        if settings.SENTRY_DSN:
            import sentry_sdk  # type: ignore
            sentry_sdk.init(
                dsn=settings.SENTRY_DSN,
                traces_sample_rate=0.1,
                environment=settings.ENV,
                release="shorts-engine@2.0.0",
            )
    except Exception:
        pass  # Never let Sentry init crash the application


_init_sentry()

from cli import app  # noqa: E402

if __name__ == "__main__":
    app()
