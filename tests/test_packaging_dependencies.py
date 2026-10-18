import re

import tomllib
from pathlib import Path


def test_pyproject_toml_contains_all_core_requirements():
    """Verify that pyproject.toml defines groq, scipy, and portalocker dependencies."""
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml must exist"

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    dependencies = data.get("project", {}).get("dependencies", [])
    dep_names = [d.split(">=")[0].split("<")[0].split("==")[0].strip() for d in dependencies]

    assert "groq" in dep_names, "groq must be declared in pyproject.toml dependencies"
    assert "scipy" in dep_names, "scipy must be declared in pyproject.toml dependencies"
    assert "portalocker" in dep_names, "portalocker must be declared in pyproject.toml dependencies"


def test_requirements_txt_contains_all_core_requirements():
    """Verify that requirements.txt contains groq, scipy, and portalocker."""
    req_path = Path(__file__).parent.parent / "requirements.txt"
    assert req_path.exists(), "requirements.txt must exist"

    req_text = req_path.read_text(encoding="utf-8")
    lines = [
        line.split("#")[0].strip()
        for line in req_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    req_names = [line.split(">=")[0].split("<")[0].split("==")[0].split(";")[0].strip() for line in lines]

    assert "groq" in req_names, "groq must be declared in requirements.txt"
    assert "scipy" in req_names, "scipy must be declared in requirements.txt"
    assert "portalocker" in req_names, "portalocker must be declared in requirements.txt"


def _req_names():
    req_path = Path(__file__).parent.parent / "requirements.txt"
    out = []
    for line in req_path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        out.append(re.split(r"[<>=!~\[]", line)[0].strip().lower())
    return set(out)


def _pyproject_names():
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    deps = data.get("project", {}).get("dependencies", [])
    return {re.split(r"[<>=!~]", d)[0].strip().lower() for d in deps}


def test_requirements_and_pyproject_declare_the_same_dependencies():
    """The two dependency sources must not drift.

    The workflows install from different files: ci.yml uses
    `pip install -r requirements.txt -r requirements-dev.txt`, while
    agent.yml uses `pip install -e .[dev]` and therefore gets the
    pyproject set. A package present in only one file is installed in one
    CI job and missing in the other.

    Known divergence at the time of writing (recorded as ledger row R4-15):
    only in requirements.txt - asyncpg, psycopg2-binary, starlette, tzdata;
    only in pyproject - watchdog. None are imported by shorts_engine/ or
    tests/, but the two files should be reconciled deliberately rather than
    left to drift, so this asserts the currently-known set.
    """
    only_req = _req_names() - _pyproject_names()
    only_py = _pyproject_names() - _req_names()

    assert only_req == {"asyncpg", "psycopg2-binary", "starlette", "tzdata"}, (
        f"requirements.txt-only packages changed: {sorted(only_req)}. "
        "Reconcile requirements.txt with pyproject.toml, or update this "
        "expected set and the R4-15 ledger row together."
    )
    assert only_py == {"watchdog"}, (
        f"pyproject-only packages changed: {sorted(only_py)}. "
        "Reconcile requirements.txt with pyproject.toml, or update this "
        "expected set and the R4-15 ledger row together."
    )
