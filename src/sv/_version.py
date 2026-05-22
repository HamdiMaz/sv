from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib

_UNKNOWN_VERSION = "0+unknown"


def resolve_version(
    *, distribution_name: str = "skill-vault", project_root: Path | None = None
) -> str:
    """Return the package version from source-tree or installed metadata."""

    root = project_root or Path(__file__).resolve().parents[2]
    source_tree_version = _version_from_pyproject(root, distribution_name)
    if source_tree_version is not None:
        return source_tree_version

    try:
        return version(distribution_name)
    except PackageNotFoundError:
        return _UNKNOWN_VERSION


def _version_from_pyproject(root: Path, distribution_name: str) -> str | None:
    pyproject_path = root / "pyproject.toml"
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None

    project = pyproject.get("project")
    if not isinstance(project, dict):
        return None
    if project.get("name") != distribution_name:
        return None

    project_version = project.get("version")
    if not isinstance(project_version, str) or not project_version:
        return None
    return project_version
