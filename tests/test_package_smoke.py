from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
import tomllib

from sv import _version


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


def test_package_public_version_is_not_hardcoded_in_source():
    src_root = Path(__file__).resolve().parents[1] / "src" / "sv"
    package_source = "\n".join(
        [
            (src_root / "__init__.py").read_text(encoding="utf-8"),
            (src_root / "_version.py").read_text(encoding="utf-8"),
        ]
    )

    assert not re.search(r"__version__\s*=\s*[\"'][^\"']+[\"']", package_source)
    assert '"0.1.0"' not in package_source


def test_version_resolver_prefers_source_tree_pyproject_over_stale_metadata(
    monkeypatch, tmp_path
):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sv"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(_version, "version", lambda distribution_name: "1.2.3")

    assert _version.resolve_version(project_root=tmp_path) == "9.8.7"


def test_version_resolver_uses_source_tree_pyproject_when_metadata_is_missing(
    monkeypatch, tmp_path
):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sv"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )

    def raise_missing(distribution_name: str) -> str:
        raise PackageNotFoundError(distribution_name)

    monkeypatch.setattr(_version, "version", raise_missing)

    assert _version.resolve_version(project_root=tmp_path) == "9.8.7"


def test_version_resolver_uses_installed_metadata_without_source_tree(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(_version, "version", lambda distribution_name: "1.2.3")

    assert _version.resolve_version(project_root=tmp_path) == "1.2.3"


def test_version_resolver_reports_unknown_when_metadata_and_source_are_missing(
    monkeypatch, tmp_path
):
    def raise_missing(distribution_name: str) -> str:
        raise PackageNotFoundError(distribution_name)

    monkeypatch.setattr(_version, "version", raise_missing)

    assert _version.resolve_version(project_root=tmp_path) == "0+unknown"


def test_version_resolver_ignores_malformed_source_tree_metadata(
    monkeypatch, tmp_path
):
    (tmp_path / "pyproject.toml").write_text("not toml", encoding="utf-8")
    monkeypatch.setattr(_version, "version", lambda distribution_name: "1.2.3")

    assert _version.resolve_version(project_root=tmp_path) == "1.2.3"


def test_version_resolver_ignores_pyproject_without_project_table(
    monkeypatch, tmp_path
):
    (tmp_path / "pyproject.toml").write_text('[tool.sv]\n', encoding="utf-8")
    monkeypatch.setattr(_version, "version", lambda distribution_name: "1.2.3")

    assert _version.resolve_version(project_root=tmp_path) == "1.2.3"


def test_version_resolver_ignores_pyproject_without_matching_project(
    monkeypatch, tmp_path
):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "other"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(_version, "version", lambda distribution_name: "1.2.3")

    assert _version.resolve_version(project_root=tmp_path) == "1.2.3"


def test_version_resolver_ignores_non_string_source_tree_version(
    monkeypatch, tmp_path
):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sv"\nversion = 42\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(_version, "version", lambda distribution_name: "1.2.3")

    assert _version.resolve_version(project_root=tmp_path) == "1.2.3"
