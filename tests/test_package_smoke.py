from importlib.metadata import version
from pathlib import Path
import tomllib

import sv
import sv.cli


def test_importing_package_exposes_version():
    assert isinstance(sv.__version__, str)
    assert sv.__version__


def test_console_entry_point_is_sv_cli_main():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject_data = tomllib.loads(pyproject.read_text())

    scripts = pyproject_data["project"]["scripts"]
    assert scripts["sv"] == "sv.cli:main"


def test_declared_package_version_is_public_version():
    assert version("sv") == sv.__version__

