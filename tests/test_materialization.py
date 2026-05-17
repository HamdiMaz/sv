from pathlib import Path
import shutil

import pytest

from sv.errors import SvError
from sv import materialization as materialization_module
from sv.materialization import (
    copy_skill_folder_to_temp,
    install_materialized_skill_folder,
    remove_materialization_path,
    replace_with_materialized_skill_folder,
    restore_materialization_backup,
)


def _write_skill(path: Path, content: str) -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(content)


def test_copy_skill_folder_to_temp_copies_source_without_touching_target(tmp_path: Path):
    source = tmp_path / "source" / "alpha"
    target = tmp_path / "project" / "alpha"
    temp_target = target.with_name(".alpha.sv-tmp")
    _write_skill(source, "remote\n")
    _write_skill(target, "local\n")

    result = copy_skill_folder_to_temp(
        source,
        temp_target,
        error_message="Failed to materialize skill 'alpha'",
    )

    assert result == temp_target
    assert (temp_target / "SKILL.md").read_text() == "remote\n"
    assert (target / "SKILL.md").read_text() == "local\n"


def test_copy_skill_folder_to_temp_rejects_symlink_in_source_tree(tmp_path: Path):
    source = tmp_path / "source" / "alpha"
    target = tmp_path / "project" / "alpha"
    _write_skill(source, "remote\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    (source / "outside-link.txt").symlink_to(outside)

    with pytest.raises(SvError, match="contains a symlink"):
        copy_skill_folder_to_temp(
            source,
            target.with_name(".alpha.sv-tmp"),
            error_message="Failed to materialize skill 'alpha'",
        )

    assert not target.exists()


def test_copy_skill_folder_to_temp_rejects_symlink_created_during_copy(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "source" / "alpha"
    temp_target = tmp_path / "project" / ".alpha.sv-tmp"
    _write_skill(source, "remote\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")

    def copy_with_symlink(source_path, destination_path):
        destination_path.mkdir(parents=True)
        (destination_path / "SKILL.md").write_text(
            (source_path / "SKILL.md").read_text()
        )
        (destination_path / "outside-link.txt").symlink_to(outside)

    monkeypatch.setattr(shutil, "copytree", copy_with_symlink)

    with pytest.raises(SvError, match="contains a symlink"):
        copy_skill_folder_to_temp(
            source,
            temp_target,
            error_message="Failed to materialize skill 'alpha'",
        )

    assert not temp_target.exists()
    assert outside.read_text() == "outside\n"


def test_copy_skill_folder_to_temp_rejects_source_trees_over_file_limit(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "source" / "alpha"
    temp_target = tmp_path / "project" / ".alpha.sv-tmp"
    _write_skill(source, "remote\n")

    monkeypatch.setattr(materialization_module, "_MAX_MATERIALIZATION_FILES", 0)

    with pytest.raises(SvError, match="file limit"):
        copy_skill_folder_to_temp(
            source,
            temp_target,
            error_message="Failed to materialize skill 'alpha'",
        )

    assert not temp_target.exists()


def test_copy_skill_folder_to_temp_rejects_source_trees_over_entry_limit(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "source" / "alpha"
    temp_target = tmp_path / "project" / ".alpha.sv-tmp"
    _write_skill(source, "remote\n")
    (source / "empty-dir").mkdir()

    monkeypatch.setattr(materialization_module, "_MAX_MATERIALIZATION_ENTRIES", 1)

    with pytest.raises(SvError, match="entry limit"):
        copy_skill_folder_to_temp(
            source,
            temp_target,
            error_message="Failed to materialize skill 'alpha'",
        )

    assert not temp_target.exists()


def test_copy_skill_folder_to_temp_rejects_source_trees_over_byte_limit(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "source" / "alpha"
    temp_target = tmp_path / "project" / ".alpha.sv-tmp"
    _write_skill(source, "remote\n")

    monkeypatch.setattr(materialization_module, "_MAX_MATERIALIZATION_BYTES", 4)

    with pytest.raises(SvError, match="byte limit"):
        copy_skill_folder_to_temp(
            source,
            temp_target,
            error_message="Failed to materialize skill 'alpha'",
        )

    assert not temp_target.exists()


def test_copy_skill_folder_to_temp_rejects_source_trees_over_depth_limit(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "source" / "alpha"
    nested = source / "docs" / "deep"
    nested.mkdir(parents=True)
    (nested / "usage.md").write_text("remote\n")
    temp_target = tmp_path / "project" / ".alpha.sv-tmp"

    monkeypatch.setattr(materialization_module, "_MAX_MATERIALIZATION_DEPTH", 1)

    with pytest.raises(SvError, match="depth limit"):
        copy_skill_folder_to_temp(
            source,
            temp_target,
            error_message="Failed to materialize skill 'alpha'",
        )

    assert not temp_target.exists()


def test_copy_skill_folder_to_temp_cleans_partial_copy_and_preserves_target(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "source" / "alpha"
    target = tmp_path / "project" / "alpha"
    temp_target = target.with_name(".alpha.sv-tmp")
    _write_skill(source, "remote\n")
    _write_skill(target, "local\n")

    def fail_copytree(source_path, destination_path):
        destination_path.mkdir(parents=True)
        (destination_path / "partial.txt").write_text("partial\n")
        raise OSError("copy failed")

    monkeypatch.setattr(shutil, "copytree", fail_copytree)

    with pytest.raises(SvError, match="copy failed"):
        copy_skill_folder_to_temp(
            source,
            temp_target,
            error_message="Failed to materialize skill 'alpha'",
        )

    assert (target / "SKILL.md").read_text() == "local\n"
    assert not temp_target.exists()


def test_install_materialized_skill_folder_success_runs_callback(tmp_path: Path):
    target = tmp_path / "project" / "alpha"
    materialized = target.with_name(".alpha.sv-add-tmp")
    _write_skill(materialized, "remote\n")
    calls: list[str] = []

    install_materialized_skill_folder(
        materialized,
        target,
        error_message="Failed to add Pi skill 'alpha'",
        after_install=lambda: calls.append("manifest"),
    )

    assert calls == ["manifest"]
    assert (target / "SKILL.md").read_text() == "remote\n"
    assert not materialized.exists()


@pytest.mark.parametrize("exception", [SvError("manifest write failed"), ValueError("callback failed")])
def test_install_materialized_skill_folder_rolls_back_new_target_when_callback_fails(
    tmp_path: Path, exception: Exception
):
    target = tmp_path / "project" / "alpha"
    materialized = target.with_name(".alpha.sv-add-tmp")
    _write_skill(materialized, "remote\n")

    def fail_manifest_update() -> None:
        raise exception

    with pytest.raises(type(exception), match=str(exception)):
        install_materialized_skill_folder(
            materialized,
            target,
            error_message="Failed to add Pi skill 'alpha'",
            after_install=fail_manifest_update,
        )

    assert not target.exists()
    assert not materialized.exists()


def test_replace_with_materialized_skill_folder_success_replaces_target_and_removes_backup(
    tmp_path: Path,
):
    target = tmp_path / "project" / "alpha"
    materialized = target.with_name(".alpha.sv-sync-tmp")
    backup = target.with_name(".alpha.sv-sync-backup")
    _write_skill(target, "local\n")
    _write_skill(materialized, "remote\n")
    calls: list[str] = []

    replace_with_materialized_skill_folder(
        materialized,
        target,
        backup,
        error_message="Failed to sync skill 'alpha'",
        after_replace=lambda: calls.append("manifest"),
    )

    assert calls == ["manifest"]
    assert (target / "SKILL.md").read_text() == "remote\n"
    assert not materialized.exists()
    assert not backup.exists()


@pytest.mark.parametrize("exception", [SvError("manifest write failed"), ValueError("callback failed")])
def test_replace_with_materialized_skill_folder_rolls_back_replacement_when_callback_fails(
    tmp_path: Path, exception: Exception
):
    target = tmp_path / "project" / "alpha"
    materialized = target.with_name(".alpha.sv-sync-tmp")
    backup = target.with_name(".alpha.sv-sync-backup")
    _write_skill(target, "local\n")
    _write_skill(materialized, "remote\n")

    def fail_manifest_update() -> None:
        raise exception

    with pytest.raises(type(exception), match=str(exception)):
        replace_with_materialized_skill_folder(
            materialized,
            target,
            backup,
            error_message="Failed to sync skill 'alpha'",
            after_replace=fail_manifest_update,
        )

    assert (target / "SKILL.md").read_text() == "local\n"
    assert not materialized.exists()
    assert not backup.exists()


def test_restore_materialization_backup_replaces_existing_target(tmp_path: Path):
    target = tmp_path / "project" / "alpha"
    backup = target.with_name(".alpha.sv-backup")
    _write_skill(target, "remote\n")
    _write_skill(backup, "local\n")

    restore_materialization_backup(target, backup)

    assert (target / "SKILL.md").read_text() == "local\n"
    assert not backup.exists()


def test_remove_materialization_path_removes_files_directories_and_symlinks(tmp_path: Path):
    file_path = tmp_path / "file.tmp"
    file_path.write_text("file\n")
    dir_path = tmp_path / "dir.tmp"
    dir_path.mkdir()
    (dir_path / "nested.txt").write_text("nested\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    link_path = tmp_path / "link.tmp"
    link_path.symlink_to(outside)

    remove_materialization_path(file_path)
    remove_materialization_path(dir_path)
    remove_materialization_path(link_path)
    remove_materialization_path(tmp_path / "missing.tmp")

    assert not file_path.exists()
    assert not dir_path.exists()
    assert not link_path.exists()
    assert outside.read_text() == "outside\n"
