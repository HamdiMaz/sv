from __future__ import annotations

from sv.config import RepoConfig, SvPaths
from sv.source import FakeSourceBackend as CompatFakeSourceBackend
from sv.source import SourceBackend as CompatSourceBackend
from sv.source import source_backends_for_repo as compat_source_backends_for_repo
from sv.source_backends import FakeSourceBackend as PackageFakeSourceBackend
from sv.source_backends.base import SourceBackend
from sv.source_backends.factory import source_backends_for_repo


def test_source_backend_protocol_is_available_from_package():
    assert SourceBackend is CompatSourceBackend


def test_source_backend_package_reexports_fake_backend_for_tests():
    assert PackageFakeSourceBackend is CompatFakeSourceBackend


def test_source_backend_factory_is_available_from_package(tmp_path):
    repo = RepoConfig(id="Org/Repo", url="https://github.com/Org/Repo.git")
    paths = SvPaths.from_home(tmp_path)

    packaged = [
        backend.name
        for backend in source_backends_for_repo(repo, paths, update=False)
    ]
    compat = [
        backend.name
        for backend in compat_source_backends_for_repo(repo, paths, update=False)
    ]

    assert packaged == compat
    assert packaged == [
        "github-gh-api",
        "github-https-api",
        "git-treeless-partial",
        "git-blobless-sparse",
    ]
