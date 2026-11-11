"""Shared guard for tests that need a real Google OAuth client configuration.

``storage/client_secret.enc`` is an operator-supplied credential: it is not
committed to the repository and is not distributed to CI, so it does not exist
on a fresh clone. Tests that drive the live OAuth client therefore *skip* when
no configuration is provisioned instead of failing with ``FileNotFoundError``,
which keeps the suite meaningful on any checkout.

To make these tests run, either place the vault in ``storage/`` or export the
client configuration as JSON:

    export YOUTUBE_CLIENT_SECRET_JSON='{"web": {"client_id": "...", ...}}'
"""

import os

import pytest

from shorts_engine.services.publishing.oauth_manager import ChannelOAuthManager


def client_secrets_provisioned() -> bool:
    """True when ``ChannelOAuthManager`` can resolve a Google OAuth client config."""
    if os.environ.get("YOUTUBE_CLIENT_SECRET_JSON", "").strip().startswith("{"):
        return True
    try:
        return ChannelOAuthManager().secrets_path.is_file()
    except Exception:
        # A settings/path failure is indistinguishable from "not provisioned"
        # for the purpose of deciding whether the test can run.
        return False


requires_client_secrets = pytest.mark.skipif(
    not client_secrets_provisioned(),
    reason=(
        "needs a Google OAuth client configuration: set YOUTUBE_CLIENT_SECRET_JSON "
        "or provision storage/client_secret.enc (a real credential, not distributed to CI)"
    ),
)
