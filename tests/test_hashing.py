from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from sv.errors import SvError
import sv.hashing as hashing_module
from sv.hashing import (
    _safe_relative_path,
    _stat_path,
    sha256_file,
    sha256_skill_directory,
)


def _write_skill_file(skill_dir: Path, relative_path: str, content: str) -> None:
    path = skill_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_sha256_file_hashes_file_content_with_prefix(tmp_path: Path) -> None:
    file_path = tmp_path / "alpha.txt"
    file_bytes = b"alpha\n"
    file_path.write_bytes(file_bytes)

    assert sha256_file(file_path) == f"sha256:{hashlib.sha256(file_bytes).hexdigest()}"


def test_sha256_skill_directory_is_stable_for_identical_trees(tmp_path: Path) -> None:
    first = tmp_path / "alpha"
    second = tmp_path / "copies" / "alpha"
    first.mkdir()
    second.mkdir(parents=True)

    _write_skill_file(first, "SKILL.md", "---\nname: alpha\ndescription: Alpha.\n---\n")
    _write_skill_file(first, "docs/usage.md", "Use alpha.\n")
    _write_skill_file(first, "scripts/run.sh", "#!/bin/sh\necho alpha\n")

    # Create the matching tree in a different order; traversal order must not matter.
    _write_skill_file(second, "scripts/run.sh", "#!/bin/sh\necho alpha\n")
    _write_skill_file(second, "SKILL.md", "---\nname: alpha\ndescription: Alpha.\n---\n")
    _write_skill_file(second, "docs/usage.md", "Use alpha.\n")

    assert sha256_skill_directory(first) == sha256_skill_directory(second)


def test_sha256_skill_directory_changes_when_content_changes(tmp_path: Path) -> None:
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    _write_skill_file(skill_dir, "SKILL.md", "---\nname: alpha\ndescription: Alpha.\n---\n")
    original_hash = sha256_skill_directory(skill_dir)

    _write_skill_file(skill_dir, "SKILL.md", "---\nname: alpha\ndescription: Changed.\n---\n")

    assert sha256_skill_directory(skill_dir) != original_hash


def test_sha256_skill_directory_includes_executable_file_mode(tmp_path: Path) -> None:
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    script = skill_dir / "run.sh"
    script.write_text("#!/bin/sh\necho alpha\n", encoding="utf-8")
    os.chmod(script, 0o644)
    non_executable_hash = sha256_skill_directory(skill_dir)

    os.chmod(script, 0o755)

    assert sha256_skill_directory(skill_dir) != non_executable_hash


def test_sha256_skill_directory_ignores_non_executable_permission_bits(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first" / "alpha"
    second = tmp_path / "second" / "alpha"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    _write_skill_file(first, "SKILL.md", "same\n")
    _write_skill_file(second, "SKILL.md", "same\n")
    os.chmod(first / "SKILL.md", 0o644)
    os.chmod(second / "SKILL.md", 0o664)

    assert sha256_skill_directory(first) == sha256_skill_directory(second)


def test_sha256_skill_directory_rejects_unsafe_skill_folder_name(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".alpha"
    skill_dir.mkdir()

    with pytest.raises(SvError, match="Invalid skill name"):
        sha256_skill_directory(skill_dir)


def test_sha256_file_rejects_missing_and_symlinked_files(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    with pytest.raises(SvError, match="not a regular file"):
        sha256_file(missing)

    real = tmp_path / "real.txt"
    real.write_text("secret\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    with pytest.raises(SvError, match="must not be a symlink"):
        sha256_file(link)


def test_sha256_file_rejects_symlinked_ancestor(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "SKILL.md").write_text("alpha\n", encoding="utf-8")
    link_dir = tmp_path / "linked"
    link_dir.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(SvError, match="File path must not contain symlinks"):
        sha256_file(link_dir / "SKILL.md")


def test_sha256_skill_directory_rejects_symlinked_directory_or_child(tmp_path: Path) -> None:
    real_skill = tmp_path / "alpha"
    real_skill.mkdir()
    (real_skill / "SKILL.md").write_text("alpha\n", encoding="utf-8")
    linked_skill = tmp_path / "linked-alpha"
    linked_skill.symlink_to(real_skill, target_is_directory=True)

    with pytest.raises(SvError, match="contains a symlink"):
        sha256_skill_directory(linked_skill, expected_name="alpha")

    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    child_link = real_skill / "docs-link.md"
    child_link.symlink_to(outside)

    with pytest.raises(SvError, match="contains a symlink"):
        sha256_skill_directory(real_skill)


def test_sha256_skill_directory_allows_valid_expected_name_for_different_folder(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "materialized-temp"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("alpha\n", encoding="utf-8")

    digest = sha256_skill_directory(skill_dir, expected_name="alpha")

    assert digest.startswith("sha256:")


def test_sha256_skill_directory_enforces_file_count_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    _write_skill_file(skill_dir, "one.txt", "one\n")

    monkeypatch.setattr(hashing_module, "_MAX_SKILL_HASH_FILES", 0)

    with pytest.raises(SvError, match="exceeds file limit"):
        sha256_skill_directory(skill_dir)


def test_sha256_skill_directory_enforces_byte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    _write_skill_file(skill_dir, "one.txt", "12345")

    monkeypatch.setattr(hashing_module, "_MAX_SKILL_HASH_BYTES", 4)

    with pytest.raises(SvError, match="exceeds byte limit"):
        sha256_skill_directory(skill_dir)


def test_sha256_skill_directory_enforces_depth_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "alpha"
    skill_dir.mkdir()
    _write_skill_file(skill_dir, "nested/file.txt", "nested\n")

    monkeypatch.setattr(hashing_module, "_MAX_SKILL_HASH_DEPTH", 1)

    with pytest.raises(SvError, match="exceeds depth limit"):
        sha256_skill_directory(skill_dir)


def test_safe_relative_path_rejects_root_and_outside_paths(tmp_path: Path) -> None:
    root = tmp_path / "alpha"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")

    with pytest.raises(SvError, match="Unsafe skill path"):
        _safe_relative_path(root, root)
    with pytest.raises(SvError, match="outside skill directory"):
        _safe_relative_path(outside, root)


def test_hashing_helpers_wrap_os_errors(tmp_path: Path, monkeypatch) -> None:
    file_path = tmp_path / "alpha.txt"
    file_path.write_text("alpha\n", encoding="utf-8")

    def fail_open(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", fail_open)

    with pytest.raises(SvError, match="Failed to hash file"):
        sha256_file(file_path)

    def fail_stat(self):
        raise OSError("stat denied")

    monkeypatch.setattr(Path, "stat", fail_stat)

    with pytest.raises(SvError, match="Failed to inspect mode"):
        _stat_path(file_path, "inspect mode")
