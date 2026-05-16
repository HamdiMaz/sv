from importlib.metadata import version
from pathlib import Path
import tomllib


def test_importing_package_exposes_version():
    import sv
    import sv.cli

    assert isinstance(sv.__version__, str)
    assert sv.__version__
    assert callable(sv.cli.main)


def test_console_entry_points_include_sv_and_svx():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject_data = tomllib.loads(pyproject.read_text())

    scripts = pyproject_data["project"]["scripts"]
    assert scripts["sv"] == "sv.cli:main"
    assert scripts["svx"] == "sv.cli:svx_main"


def test_declared_package_version_is_public_version():
    import sv

    assert version("sv") == sv.__version__
