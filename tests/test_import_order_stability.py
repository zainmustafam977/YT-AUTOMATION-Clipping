"""
Import-order stability regression tests.

`shorts_engine.services.publishing.worker` participates in a cycle:

    worker -> orchestration.exceptions -> orchestration/__init__
           -> orchestration.runner -> publishing.client -> worker

Because `orchestration/__init__` imports `PipelineRunner` at module scope,
importing `publishing.client` (or anything that reaches it) before `worker`
has finished initialising raised:

    ImportError: cannot import name 'build_youtube_client' from partially
    initialized module '...publishing.worker' (most likely due to a circular
    import)

That surfaced as a collection error in
tests/test_analytics_poller_and_winner_daemon.py, which imports
analytics.poller first.

Each test below imports one entry point in a FRESH interpreter so the failure
cannot be masked by modules another test already imported.
"""

import subprocess
import sys

import pytest

# Entry points that previously triggered the cycle, each as a separate module.
IMPORT_TARGETS = [
    "shorts_engine.services.analytics.poller",
    "shorts_engine.services.publishing.client",
    "shorts_engine.services.publishing.worker",
    "shorts_engine.orchestration.runner",
    "shorts_engine.orchestration.exceptions",
]


@pytest.mark.parametrize("module", IMPORT_TARGETS)
def test_module_imports_cleanly_in_fresh_interpreter(module):
    """Importing `module` first must not raise ImportError from the cycle."""
    code = f"import importlib; importlib.import_module({module!r})"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, (
        f"Importing {module} in a fresh interpreter failed.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "circular import" not in (proc.stderr or ""), (
        f"Circular import detected while importing {module}:\n{proc.stderr}"
    )
