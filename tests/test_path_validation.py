import pytest

from sv.errors import SvError
from sv.path_validation import normalize_executable_metadata_path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/abs.py",
        "scripts//run.py",
        "scripts/./run.py",
        "./run.py",
        "scripts/../run.py",
        "../run.py",
        "scripts\\run.py",
        "bad:name.py",
        "scripts/bad\x1b.py",
        "scripts/zero\u200bwidth.py",
    ],
)
def test_normalize_executable_metadata_path_rejects_invalid_paths(path: str) -> None:
    with pytest.raises(SvError):
        normalize_executable_metadata_path(path)


def test_normalize_executable_metadata_path_accepts_relative_posix_path() -> None:
    assert normalize_executable_metadata_path("scripts/run.py") == "scripts/run.py"
