from pathlib import Path

import pytest

from sv.errors import SvError
from sv.tomlutil import (
    atomic_write_text,
    load_toml_document,
    require_schema_version,
    toml_escape,
)


def test_toml_escape_round_trips_special_characters():
    value = 'quoted "value" with \\ slash\nnewline\rreturn\ttab and \x01control\x7fdel'

    escaped = toml_escape(value)
    parsed = load_toml_document_text(f'value = "{escaped}"\n')["value"]

    assert parsed == value


def test_load_toml_document_reports_parse_errors_with_file_context(tmp_path: Path):
    path = tmp_path / "index.toml"
    path.write_text("skills = [\n")

    with pytest.raises(SvError) as exc_info:
        load_toml_document(path, "sv index")

    message = str(exc_info.value)
    assert "Failed to read sv index" in message
    assert str(path) in message


def test_require_schema_version_rejects_future_versions_actionably(tmp_path: Path):
    path = tmp_path / "manifest.toml"
    data = {"schema_version": 99}

    with pytest.raises(SvError) as exc_info:
        require_schema_version(
            data,
            path=path,
            document_name="sv manifest",
            current_version=1,
        )

    message = str(exc_info.value)
    assert "Unsupported sv manifest schema_version 99" in message
    assert "update sv" in message.lower()
    assert str(path) in message


def test_require_schema_version_applies_old_schema_migration_hooks(tmp_path: Path):
    path = tmp_path / "manifest.toml"
    original = {"schema_version": 0, "skills": []}

    migrated = require_schema_version(
        original,
        path=path,
        document_name="sv manifest",
        current_version=1,
        migrations={0: lambda data: {**data, "schema_version": 1, "migrated": True}},
    )

    assert migrated == {"schema_version": 1, "skills": [], "migrated": True}
    assert original == {"schema_version": 0, "skills": []}


def test_require_schema_version_rejects_unmigratable_old_schema(tmp_path: Path):
    path = tmp_path / "manifest.toml"

    with pytest.raises(SvError, match="schema_version 0"):
        require_schema_version(
            {"schema_version": 0},
            path=path,
            document_name="sv manifest",
            current_version=1,
        )


def test_require_schema_version_rejects_non_advancing_migration(tmp_path: Path):
    path = tmp_path / "manifest.toml"

    with pytest.raises(SvError, match="did not advance"):
        require_schema_version(
            {"schema_version": 0},
            path=path,
            document_name="sv manifest",
            current_version=1,
            migrations={0: lambda data: {**data, "unchanged": True}},
        )


def test_require_schema_version_rejects_migration_without_explicit_version(
    tmp_path: Path,
):
    path = tmp_path / "manifest.toml"

    with pytest.raises(SvError, match="must set schema_version"):
        require_schema_version(
            {"schema_version": 0},
            path=path,
            document_name="sv manifest",
            current_version=1,
            migrations={0: lambda data: {"skills": data.get("skills", [])}},
        )


def test_atomic_write_text_preserves_existing_file_when_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "state.toml"
    path.write_text("old\n")

    def fail_temp_write(*args, **kwargs):  # noqa: ANN002, ANN003
        raise OSError("disk full")

    monkeypatch.setattr("sv.tomlutil._write_new_file_no_follow", fail_temp_write)

    with pytest.raises(SvError, match="Failed to write sv state"):
        atomic_write_text(path, "new\n", document_name="sv state")

    assert path.read_text() == "old\n"
    assert not list(tmp_path.glob(".state.toml.*.tmp"))


def test_atomic_write_text_refuses_symlinked_temp_file(tmp_path: Path):
    path = tmp_path / "state.toml"
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    temp = tmp_path / ".state.toml.tmp"
    temp.symlink_to(outside)

    with pytest.raises(SvError, match="temporary sv state path"):
        atomic_write_text(
            path,
            "new\n",
            document_name="sv state",
            temp_name=".state.toml.tmp",
        )

    assert outside.read_text() == "outside\n"
    assert not path.exists()


def load_toml_document_text(text: str) -> dict[str, object]:
    import tomllib

    return tomllib.loads(text)
