"""Configuration for the end-to-end suite.

Everything under ``tests/e2e`` is marked ``e2e`` here, so the unit-test jobs can
select ``-m "not e2e"`` and trust the marker instead of hard-coding paths. The
browser-driven modules additionally skip themselves when Playwright is not
installed (see their ``pytest.importorskip``), which means a plain
``pytest tests`` on a checkout without browsers reports skips, not errors.
"""

from pathlib import Path

import pytest

_E2E_DIR = Path(__file__).parent.resolve()


def pytest_collection_modifyitems(config, items):
    # This hook is session-wide once the conftest is loaded, so restrict the
    # marker to items that actually live under tests/e2e.
    for item in items:
        path = Path(str(getattr(item, "path", item.fspath))).resolve()
        if _E2E_DIR == path.parent or _E2E_DIR in path.parents:
            item.add_marker(pytest.mark.e2e)
