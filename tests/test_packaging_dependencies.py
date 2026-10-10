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
