from email.message import Message
from io import BytesIO
from pathlib import Path
import os
import shutil
import subprocess
import threading

import pytest

from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv import materialization as materialization_module
import sv.process as process_module
import sv.source_backends.base as base_module
import sv.source_backends.github as github_module
import sv.source_backends.git as git_module
from sv.source import (
    GitBloblessSparseBackend,
    GitHubGhApiBackend,
    GitLocalSourceBackend,
    GitHubHttpsApiBackend,
    GitHubHttpResponse,
    GitHubRepoRef,
    GitTreelessPartialBackend,
    LocalGitSourceBackend,
    SourceBackendError,
    SourceBackendFailure,
    default_runner,
    ensure_source_repo,
    ensure_source_repos,
    FakeSourceBackend,
    list_source_skills,
    parse_github_repo_ref,
    reject_symlinked_source_cache_path,
    source_backends_for_repo,
)


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, cwd=None):
        self.calls.append((list(args), cwd))
        if not self.results:
            raise AssertionError(f"unexpected command: {args}")
        return self.results.pop(0)


class FakeHttpGet:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if not self.responses:
            raise AssertionError(f"unexpected HTTP request: {url}")
        return self.responses.pop(0)


def completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=args, returncode=returncode, stdout=stdout, stderr=stderr
    )


def assert_hint_contains(error: SourceBackendError, *expected_parts: str) -> None:
    assert error.hint is not None
    for expected in expected_parts:
        assert expected in error.hint


def test_source_star_import_preserves_source_and_process_compat_exports():
    namespace: dict[str, object] = {}

    exec("from sv.source import *", namespace)

    assert namespace["FakeSourceBackend"] is FakeSourceBackend
    assert namespace["ensure_source_repo"] is ensure_source_repo
    assert namespace["default_runner"] is default_runner


def test_parse_github_repo_ref_accepts_supported_github_repo_urls():
    assert parse_github_repo_ref("https://github.com/Org/Skills.git") == GitHubRepoRef(
        owner="Org", repo="Skills"
    )
    assert parse_github_repo_ref("git@github.com:Org/Skills.git") == GitHubRepoRef(
        owner="Org", repo="Skills"
    )
    assert parse_github_repo_ref("ssh://git@github.com/Org/Skills") == GitHubRepoRef(
        owner="Org", repo="Skills"
    )
    assert parse_github_repo_ref("Org/Skills") == GitHubRepoRef(
        owner="Org", repo="Skills"
    )
    assert parse_github_repo_ref("https://example.com/Org/Skills.git") is None


def test_parse_github_repo_ref_rejects_cleartext_github_urls():
    assert parse_github_repo_ref("http://github.com/Org/Skills.git") is None


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("API rate limit exceeded", "raise GitHub API limits"),
        ("HTTP 404 Not Found", "private repo"),
        ("authentication required", "access private repos"),
        ("not logged in", "access private repos"),
        ("HTTP 403 forbidden", "access private repos"),
    ],
)
def test_github_failure_hints_cover_actionable_edge_messages(detail, expected):
    hint = github_module._gh_failure_hint(detail)

    assert hint is not None
    assert expected in hint


def test_github_header_lookup_is_case_insensitive_and_handles_missing_names():
    assert (
        github_module._header_value(
            {"X-RateLimit-Remaining": "0"}, "x-ratelimit-remaining"
        )
        == "0"
    )
    assert github_module._header_value({"content-type": "application/json"}, "etag") is None


def test_github_item_helpers_reject_malformed_directory_items():
    assert github_module._github_item_type(object()) is None
    assert github_module._github_item_type({"type": 123}) is None

    with pytest.raises(SourceBackendError, match="missing a name"):
        github_module._github_item_name(object(), "listing")
    with pytest.raises(SourceBackendError, match="missing a name"):
        github_module._github_item_name({"name": 123}, "listing")
    with pytest.raises(SourceBackendError, match="missing a path"):
        github_module._github_item_path(object(), "listing")
    with pytest.raises(SourceBackendError, match="missing a path"):
        github_module._github_item_path({"path": 123}, "listing")
    with pytest.raises(SourceBackendError, match="control characters"):
        github_module._github_item_path({"path": "bad\x1fname"}, "listing")


def test_normalize_backend_relative_path_rejects_unsafe_edge_cases():
    for value in (
        "bad\x1fname",
        "/absolute",
        "windows\\path",
        "bad:name",
        "",
        ".",
        "../up",
        "skills/zero\u200bwidth/SKILL.md",
        "skills/rtl\u202eoverride/SKILL.md",
    ):
        with pytest.raises(SvError):
            base_module._normalize_backend_relative_path(value)


def test_source_backends_for_repo_selects_lightweight_backends_in_plan_order(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")

    backends = source_backends_for_repo(repo, paths, runner=FakeRunner([]))

    assert [backend.name for backend in backends] == [
        "github-gh-api",
        "github-https-api",
        "git-treeless-partial",
        "git-blobless-sparse",
    ]


def test_source_backends_for_repo_keeps_github_api_first_when_cache_exists(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")
    paths.source_repo_for(repo.id).mkdir(parents=True)

    backends = source_backends_for_repo(repo, paths, runner=FakeRunner([]))

    assert [backend.name for backend in backends] == [
        "github-gh-api",
        "github-https-api",
        "git-treeless-partial",
        "git-blobless-sparse",
    ]


def test_source_backends_for_repo_uses_direct_backend_for_local_path_sources(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path / "home")
    source = tmp_path / "skill-source"
    (source / ".git").mkdir(parents=True)
    runner = FakeRunner([])
    repo = RepoConfig(id="local-skill-source", url=str(source))

    backends = source_backends_for_repo(repo, paths, runner=runner)

    assert [backend.name for backend in backends] == ["git-local-source"]
    assert backends[0].list_candidate_skill_files() == []
    assert runner.calls == []


def test_local_source_repo_path_detects_file_urls_and_rejects_remote_file_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "skill-source"
    source.mkdir()
    monkeypatch.chdir(tmp_path)

    assert git_module._local_source_repo_path(source.as_uri()) == source.resolve()
    assert git_module._local_source_repo_path("file://example.com/tmp/repo") is None
    assert git_module._local_source_repo_path("file://") is None
    assert git_module._local_source_repo_path("ssh://git@github.com/Org/Skills.git") is None
    assert git_module._local_source_repo_path("missing-source") is None


def test_local_git_source_backend_materializes_and_reports_invalid_roots(
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    skill_dir = repo_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha.\n---\n")
    (skill_dir / "notes.md").write_text("alpha notes\n")
    destination = tmp_path / "materialized"

    backend = LocalGitSourceBackend(repo_path)

    backend.materialize_folder("skills/alpha", destination)
    assert (destination / "notes.md").read_text() == "alpha notes\n"

    with pytest.raises(SourceBackendError, match="not a directory"):
        LocalGitSourceBackend(tmp_path / "missing").list_candidate_skill_files()

    not_git = tmp_path / "not-git"
    not_git.mkdir()
    with pytest.raises(SourceBackendError, match="not a Git working tree"):
        LocalGitSourceBackend(not_git).read_file("skills/alpha/SKILL.md")


def test_local_git_source_backend_rejects_symlink_created_during_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = tmp_path / "repo"
    skill_dir = repo_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha.\n---\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    destination = tmp_path / "materialized"

    def copy_with_symlink(source_path, destination_path, **kwargs):
        destination_path.mkdir(parents=True)
        (destination_path / "SKILL.md").write_text(
            (source_path / "SKILL.md").read_text()
        )
        (destination_path / "outside-link.txt").symlink_to(outside)

    monkeypatch.setattr(git_module.shutil, "copytree", copy_with_symlink)

    with pytest.raises(SvError, match="contains a symlink"):
        LocalGitSourceBackend(repo_path).materialize_folder("skills/alpha", destination)

    assert not destination.exists()
    assert outside.read_text() == "outside\n"


def test_local_git_source_backend_rejects_source_symlink_added_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = tmp_path / "repo"
    skill_dir = repo_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (repo_path / ".git").mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha.\n---\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    destination = tmp_path / "materialized"
    real_validate = git_module.validate_materialization_source_tree
    validation_calls = 0

    def add_source_symlink_after_first_validation(path: Path) -> None:
        nonlocal validation_calls
        validation_calls += 1
        real_validate(path)
        if validation_calls == 1:
            (skill_dir / "outside-link.txt").symlink_to(outside)

    monkeypatch.setattr(
        git_module,
        "validate_materialization_source_tree",
        add_source_symlink_after_first_validation,
    )

    with pytest.raises(SvError, match="contains a symlink"):
        LocalGitSourceBackend(repo_path).materialize_folder("skills/alpha", destination)

    assert validation_calls == 2
    assert not destination.exists()
    assert outside.read_text() == "outside\n"


def test_local_git_source_backend_rejects_symlinked_git_metadata(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    repo_path = tmp_path / "repo"
    git_target = tmp_path / "outside-git"
    repo_path.mkdir()
    git_target.mkdir()
    (repo_path / ".git").symlink_to(git_target, target_is_directory=True)
    backend = LocalGitSourceBackend(repo_path)

    with pytest.raises(SourceBackendError, match="Local source Git metadata path"):
        backend.read_index()


@pytest.mark.parametrize(
    ("repo_url", "match"),
    [
        ("http://github.com/Org/Skills.git", "repo URL must use HTTPS"),
        ("HTTP://github.com/Org/Skills.git", "repo URL must use HTTPS"),
        ("https://token@github.com/Org/Skills.git", "repo URL cannot contain credentials"),
        ("HTTPS://token@github.com/Org/Skills.git", "repo URL cannot contain credentials"),
        ("ssh://git:secret@github.com/Org/Skills.git", "repo URL cannot contain credentials"),
        ("SSH://git:secret@github.com/Org/Skills.git", "repo URL cannot contain credentials"),
    ],
)
def test_source_backends_for_repo_rejects_unsafe_urls_before_backend_selection(
    tmp_path: Path, repo_url: str, match: str
):
    paths = SvPaths.from_home(tmp_path)
    repo = RepoConfig(id="Org/Skills", url=repo_url)

    with pytest.raises(SvError, match=match):
        source_backends_for_repo(repo, paths, runner=FakeRunner([]))


def test_github_gh_api_backend_reads_index_candidates_and_materializes_folder(
    tmp_path: Path,
):
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout="c2NoZW1hX3ZlcnNpb24gPSAxCg==\n",
            ),
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"team","path":"team","type":"dir"},'
                    '{"name":".pi","path":".pi","type":"dir"},'
                    '{"name":"README.md","path":"README.md","type":"file"}]'
                ),
            ),
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"alpha","path":"skills/alpha","type":"dir"},'
                    '{"name":".hidden","path":"skills/.hidden","type":"dir"},'
                    '{"name":"README.md","path":"skills/README.md","type":"file"}]'
                ),
            ),
            completed(
                ["gh", "api"],
                stdout='[{"name":"SKILL.md","path":"skills/alpha/SKILL.md","type":"file"}]',
            ),
            completed(
                ["gh", "api"],
                stdout='[{"name":"beta","path":"team/skills/beta","type":"dir"}]',
            ),
            completed(
                ["gh", "api"],
                stdout='[{"name":"SKILL.md","path":"team/skills/beta/SKILL.md","type":"file"}]',
            ),
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"deep","path":"packages/agents/pi/skills/deep",'
                    '"type":"dir"}]'
                ),
            ),
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"SKILL.md",'
                    '"path":"packages/agents/pi/skills/deep/SKILL.md",'
                    '"type":"file"}]'
                ),
            ),
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"SKILL.md","path":"skills/alpha/SKILL.md",'
                    '"type":"file"},'
                    '{"name":"nested","path":"skills/alpha/nested","type":"dir"}]'
                ),
            ),
            completed(["gh", "api"], stdout="YWxwaGEgc2tpbGwK\n"),
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"README.md","path":"skills/alpha/nested/README.md",'
                    '"type":"file"}]'
                ),
            ),
            completed(["gh", "api"], stdout="bmVzdGVkCg==\n"),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)
    destination = tmp_path / "materialized"

    assert backend.read_index() == b"schema_version = 1\n"
    assert backend.list_candidate_skill_files(("packages/agents/pi/skills",)) == [
        "packages/agents/pi/skills/deep/SKILL.md",
        "skills/alpha/SKILL.md",
        "team/skills/beta/SKILL.md",
    ]
    backend.materialize_folder("skills/alpha", destination)

    assert (destination / "SKILL.md").read_bytes() == b"alpha skill\n"
    assert (destination / "nested" / "README.md").read_bytes() == b"nested\n"
    assert runner.calls == [
        (
            [
                "gh",
                "api",
                "/repos/Org/Skills/contents/.sv/index.toml",
                "--jq",
                ".content",
            ],
            None,
        ),
        (["gh", "api", "/repos/Org/Skills/contents"], None),
        (["gh", "api", "/repos/Org/Skills/contents/skills"], None),
        (["gh", "api", "/repos/Org/Skills/contents/skills/alpha"], None),
        (["gh", "api", "/repos/Org/Skills/contents/team/skills"], None),
        (["gh", "api", "/repos/Org/Skills/contents/team/skills/beta"], None),
        (
            [
                "gh",
                "api",
                "/repos/Org/Skills/contents/packages/agents/pi/skills",
            ],
            None,
        ),
        (
            [
                "gh",
                "api",
                "/repos/Org/Skills/contents/packages/agents/pi/skills/deep",
            ],
            None,
        ),
        (["gh", "api", "/repos/Org/Skills/contents/skills/alpha"], None),
        (
            [
                "gh",
                "api",
                "/repos/Org/Skills/contents/skills/alpha/SKILL.md",
                "--jq",
                ".content",
            ],
            None,
        ),
        (["gh", "api", "/repos/Org/Skills/contents/skills/alpha/nested"], None),
        (
            [
                "gh",
                "api",
                "/repos/Org/Skills/contents/skills/alpha/nested/README.md",
                "--jq",
                ".content",
            ],
            None,
        ),
    ]


def test_github_gh_api_backend_applies_executable_modes_from_tree_api(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"SKILL.md","path":"skills/alpha/SKILL.md","type":"file"},'
                    '{"name":"scripts","path":"skills/alpha/scripts","type":"dir"}]'
                ),
            ),
            completed(["gh", "api"], stdout="LS0tXG5uYW1lOiBhbHBoYVxuZGVzY3JpcHRpb246IEFscGhhIHNraWxsLlxuLS0tXG4="),
            completed(
                ["gh", "api"],
                stdout='[{"name":"run.py","path":"skills/alpha/scripts/run.py","type":"file"}]',
            ),
            completed(["gh", "api"], stdout="cHJpbnQoJ2FscGhhJylcbiA="),
            completed(
                ["gh", "api"],
                stdout=(
                    '{"sha":"tree", "truncated": false, "tree": ['
                    '{"path":"SKILL.md","mode":"100644","type":"blob"},'
                    '{"path":"scripts/run.py","mode":"100755","type":"blob"}'
                    ']} '
                ),
            ),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)
    destination = tmp_path / "materialized"

    backend.materialize_folder("skills/alpha", destination)
    assert not os.access(destination / "scripts" / "run.py", os.X_OK)

    backend.apply_executable_modes("skills/alpha", destination)

    assert os.access(destination / "scripts" / "run.py", os.X_OK)
    assert runner.calls[-1] == (
        ["gh", "api", "/repos/Org/Skills/git/trees/HEAD%3Askills%2Falpha?recursive=1"],
        None,
    )


def test_github_https_api_backend_applies_tree_modes(tmp_path: Path) -> None:
    tree_body = (
        '{"sha":"tree", "truncated": false, "tree": ['
        '{"path":"SKILL.md","mode":"100644","type":"blob"},'
        '{"path":"scripts/run.py","mode":"100755","type":"blob"}'
        ']}'
    ).encode("utf-8")
    http = FakeHttpGet([GitHubHttpResponse(status=200, body=tree_body)])
    backend = GitHubHttpsApiBackend(
        GitHubRepoRef(owner="Org", repo="Skills"),
        http_get=http,
        env={},
    )
    destination = tmp_path / "alpha"
    (destination / "scripts").mkdir(parents=True)
    (destination / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill.\n---\n", encoding="utf-8"
    )
    script = destination / "scripts" / "run.py"
    script.write_text("print('alpha')\n", encoding="utf-8")

    backend.apply_executable_modes("skills/alpha", destination)

    assert os.access(script, os.X_OK)
    assert http.calls[-1][0] == (
        "https://api.github.com/repos/Org/Skills/git/trees/HEAD%3Askills%2Falpha?recursive=1"
    )


def test_github_tree_endpoint_escapes_repo_and_path_components() -> None:
    endpoint = github_module._github_tree_endpoint(
        GitHubRepoRef(owner="Org.Name", repo="Skills Repo"), "skills/alpha space"
    )

    assert (
        endpoint
        == "/repos/Org.Name/Skills%20Repo/git/trees/HEAD%3Askills%2Falpha%20space?recursive=1"
    )


@pytest.mark.parametrize(
    ("tree_response", "expected_message"),
    [
        ("{", "not valid JSON"),
        ('{"sha":"tree","truncated":false}', "missing tree entries"),
        ('{"sha":"tree","truncated":true,"tree":[]}', "truncated"),
        (
            '{"sha":"tree","truncated":false,"tree":['
            '{"path":"scripts/run.py","mode":"100755","type":"commit"}'
            "]}",
            "unsupported type",
        ),
        (
            '{"sha":"tree","truncated":false,"tree":['
            '{"path":"scripts/run.py","mode":"120000","type":"blob"}'
            "]}",
            "unsupported file mode",
        ),
        (
            '{"sha":"tree","truncated":false,"tree":['
            '{"mode":"100755","type":"blob"}'
            "]}",
            "missing a path",
        ),
        (
            '{"sha":"tree","truncated":false,"tree":['
            '{"path":"scripts/./run.py","mode":"100755","type":"blob"}'
            "]}",
            "unsafe path components",
        ),
    ],
)
def test_github_gh_api_backend_rejects_invalid_tree_mode_responses(
    tmp_path: Path, tree_response: str, expected_message: str
) -> None:
    runner = FakeRunner([completed(["gh", "api"], stdout=tree_response)])
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match=expected_message):
        backend.apply_executable_modes("skills/alpha", tmp_path / "alpha")


def test_github_gh_api_backend_rejects_oversized_materialization_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_MATERIALIZATION_BYTES", 4)
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout='[{"name":"SKILL.md","path":"skills/alpha/SKILL.md","type":"file"}]',
            ),
            completed(["gh", "api"], stdout="bGFyZ2UK"),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)
    destination = tmp_path / "materialized"

    with pytest.raises(SourceBackendError, match="exceeds GitHub API materialization byte limit"):
        backend.materialize_folder("skills/alpha", destination)

    assert not destination.exists()
    assert not (tmp_path / ".materialized.sv-github-api-tmp").exists()


def test_github_gh_api_backend_rejects_advertised_oversized_file_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_MATERIALIZATION_BYTES", 4)
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"SKILL.md","path":"skills/alpha/SKILL.md",'
                    '"type":"file","size":5}]'
                ),
            ),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="exceeds GitHub API materialization byte limit"):
        backend.materialize_folder("skills/alpha", tmp_path / "materialized")

    assert runner.calls == [
        (["gh", "api", "/repos/Org/Skills/contents/skills/alpha"], None)
    ]


def test_github_gh_api_backend_rejects_encoded_content_before_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_MATERIALIZATION_BYTES", 4)

    def fail_decode(*_args, **_kwargs):
        raise AssertionError("oversized content should be rejected before base64 decode")

    monkeypatch.setattr(github_module.base64, "b64decode", fail_decode)
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout='[{"name":"SKILL.md","path":"skills/alpha/SKILL.md","type":"file"}]',
            ),
            completed(["gh", "api"], stdout="bGFyZ2UK"),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="exceeds GitHub API materialization byte limit"):
        backend.materialize_folder("skills/alpha", tmp_path / "materialized")


def test_github_gh_api_backend_limits_materialization_directory_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_MATERIALIZATION_ENTRIES", 0)
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"one","path":"skills/alpha/one","type":"dir"},'
                    '{"name":"two","path":"skills/alpha/two","type":"dir"}]'
                ),
            ),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="exceeds GitHub API materialization entry limit"):
        backend.materialize_folder("skills/alpha", tmp_path / "materialized")


def test_github_gh_api_backend_cleans_stale_temp_file_and_replaces_existing_directory(
    tmp_path: Path,
):
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"SKILL.md","path":"skills/alpha/SKILL.md",'
                    '"type":"file","size":6}]'
                ),
            ),
            completed(["gh", "api"], stdout="YWxwaGE="),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)
    destination = tmp_path / "materialized"
    destination.mkdir()
    (destination / "old.txt").write_text("old")
    stale_temp = tmp_path / ".materialized.sv-github-api-tmp"
    stale_temp.write_text("stale")

    backend.materialize_folder("skills/alpha", destination)

    assert not stale_temp.exists()
    assert not (destination / "old.txt").exists()
    assert (destination / "SKILL.md").read_text() == "alpha"


def test_github_gh_api_backend_rejects_materialization_depth_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_MATERIALIZATION_DEPTH", 0)
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout='[{"name":"nested","path":"skills/alpha/nested","type":"dir"}]',
            ),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="exceeds GitHub API materialization depth limit"):
        backend.materialize_folder("skills/alpha", tmp_path / "materialized")


def test_github_gh_api_backend_rejects_materialization_file_count_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_MATERIALIZATION_FILES", 0)
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout='[{"name":"SKILL.md","path":"skills/alpha/SKILL.md","type":"file"}]',
            ),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="exceeds GitHub API materialization file limit"):
        backend.materialize_folder("skills/alpha", tmp_path / "materialized")


def test_github_gh_api_backend_rejects_oversized_decoded_content_after_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_MATERIALIZATION_BYTES", 4)
    monkeypatch.setattr(github_module, "_max_base64_decoded_size", lambda _encoded: 0)
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout='[{"name":"SKILL.md","path":"skills/alpha/SKILL.md","type":"file"}]',
            ),
            completed(["gh", "api"], stdout="bGFyZ2UK"),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="exceeds GitHub API materialization byte limit"):
        backend.materialize_folder("skills/alpha", tmp_path / "materialized")


def test_github_gh_api_backend_limits_metadata_file_size(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(base_module, "_MAX_SOURCE_SKILL_FILE_BYTES", 4, raising=False)
    runner = FakeRunner([completed(["gh", "api"], stdout="bGFyZ2UK")])
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="source metadata size limit"):
        backend.read_file("skills/alpha/SKILL.md")


def test_github_gh_api_backend_limits_index_file_size(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(base_module, "_MAX_SOURCE_INDEX_BYTES", 4, raising=False)
    runner = FakeRunner(
        [completed(["gh", "api"], stdout="c2NoZW1hX3ZlcnNpb24gPSAxCg==")]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="source index size limit"):
        backend.read_index()


def test_github_gh_api_backend_limits_raw_api_output(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_API_RESPONSE_BYTES", 2)
    runner = FakeRunner([completed(["gh", "api"], stdout="[]\n")])
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="exceeded size limit"):
        backend.list_candidate_skill_files()


def test_github_gh_api_backend_default_runner_captures_output_without_memory_buffer(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_run(args, **kwargs):
        assert "capture_output" not in kwargs
        kwargs["stdout"].write(b"b2s=\n")
        return completed(args)

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"))

    assert backend.read_file("skills/alpha/SKILL.md") == b"ok"


def test_github_gh_api_backend_default_runner_rejects_oversized_output_file(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_API_RESPONSE_BYTES", 2)

    def fake_run(args, **kwargs):
        kwargs["stdout"].write(b"[]\n")
        return completed(args)

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"))

    with pytest.raises(SourceBackendError, match="exceeded size limit"):
        backend.list_candidate_skill_files()


def test_github_gh_api_backend_default_runner_limits_error_output(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_API_ERROR_BYTES", 2)

    def fake_run(args, **kwargs):
        kwargs["stderr"].write(b"err")
        return completed(args, returncode=1)

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"))

    with pytest.raises(SourceBackendError, match="error output exceeded size limit"):
        backend.read_file("skills/alpha/SKILL.md")


def test_github_gh_api_backend_default_runner_reports_timeouts(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_run(_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["gh", "api"], timeout=1)

    monkeypatch.setattr(github_module.subprocess, "run", fake_run)
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"))

    with pytest.raises(SourceBackendError, match="command timed out"):
        backend.read_file("skills/alpha/SKILL.md")


def test_read_limited_process_output_file_reports_read_errors(tmp_path: Path):
    with pytest.raises(SourceBackendError, match="Failed to read test output"):
        github_module._read_limited_process_output_file(
            tmp_path / "missing", 4, "test output", "reading test output"
        )


def test_github_api_helpers_limit_listing_size_item_size_and_response_body(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(github_module, "_MAX_GITHUB_API_RESPONSE_BYTES", 2)
    backend = GitHubGhApiBackend(
        GitHubRepoRef(owner="Org", repo="Skills"),
        runner=FakeRunner([completed(["gh", "api"], stdout="[{}]")]),
    )
    with pytest.raises(SourceBackendError, match="exceeded size limit"):
        backend.list_candidate_skill_files()

    with pytest.raises(SourceBackendError, match="invalid size"):
        github_module._github_item_size({"size": -1}, "checking item")
    assert github_module._github_item_size(object(), "checking item") is None
    assert github_module._max_base64_decoded_size("") == 0

    class LargeBody:
        def read(self, size: int) -> bytes:
            return b"x" * size

    with pytest.raises(SourceBackendError, match="exceeded size limit"):
        github_module._read_limited_response_body(LargeBody())

    class SmallBody:
        def read(self, _size: int) -> bytes:
            return b"ok"

    assert github_module._read_limited_response_body(SmallBody()) == b"ok"


def test_github_gh_api_backend_rejects_unknown_materialization_content_type(
    tmp_path: Path,
):
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"generated","path":"skills/alpha/generated",'
                    '"type":"workflow_run"}]'
                ),
            ),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="unsupported GitHub content type"):
        backend.materialize_folder("skills/alpha", tmp_path / "materialized")


def test_github_gh_api_backend_rejects_out_of_folder_materialization_paths_before_reading(
    tmp_path: Path,
):
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout='[{"name":"secret.txt","path":"other/secret.txt","type":"file"}]',
            ),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    with pytest.raises(SourceBackendError, match="is not inside skills/alpha"):
        backend.materialize_folder("skills/alpha", tmp_path / "materialized")

    assert runner.calls == [
        (["gh", "api", "/repos/Org/Skills/contents/skills/alpha"], None)
    ]


def test_github_gh_api_backend_ignores_skill_directories_without_skill_file():
    runner = FakeRunner(
        [
            completed(["gh", "api"], stdout="[]"),
            completed(
                ["gh", "api"],
                stdout=(
                    '[{"name":"alpha","path":"skills/alpha","type":"dir"},'
                    '{"name":"empty","path":"skills/empty","type":"dir"}]'
                ),
            ),
            completed(
                ["gh", "api"],
                stdout='[{"name":"SKILL.md","path":"skills/alpha/SKILL.md","type":"file"}]',
            ),
            completed(["gh", "api"], stdout="[]"),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    assert backend.list_candidate_skill_files() == ["skills/alpha/SKILL.md"]


def test_github_gh_api_backend_does_not_swallow_private_repo_404_as_missing_index():
    runner = FakeRunner(
        [
            completed(["gh", "api"], returncode=1, stderr="HTTP 404: Not Found\n"),
            completed(["gh", "api"], returncode=1, stderr="HTTP 404: Not Found\n"),
        ]
    )
    backend = GitHubGhApiBackend(
        GitHubRepoRef(owner="Org", repo="Private"), runner=runner
    )

    with pytest.raises(SourceBackendError) as error:
        backend.read_index()

    assert error.value.operation == "reading .sv/index.toml"
    assert "HTTP 404" in error.value.detail
    assert_hint_contains(error.value, "gh auth login", "GH_TOKEN")


def test_github_gh_api_backend_reports_missing_gh_as_structured_fallback_reason():
    def missing_gh(args, cwd=None):
        raise FileNotFoundError("gh")

    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=missing_gh)

    with pytest.raises(SourceBackendError) as error:
        backend.read_file("skills/alpha/SKILL.md")

    assert error.value.operation == "reading remote file"
    assert "gh was not found" in error.value.detail
    assert_hint_contains(error.value, "gh auth login", "GH_TOKEN")


def test_github_gh_api_backend_reports_auth_and_rate_limit_fallback_hints():
    auth_runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                returncode=1,
                stderr="HTTP 401: authentication required\n",
            )
        ]
    )
    auth_backend = GitHubGhApiBackend(
        GitHubRepoRef(owner="Org", repo="Private"), runner=auth_runner
    )

    with pytest.raises(SourceBackendError) as auth_error:
        auth_backend.read_file("skills/private/SKILL.md")

    assert auth_error.value.operation == "reading remote file"
    assert "authentication required" in auth_error.value.detail
    assert_hint_contains(auth_error.value, "gh auth login", "GH_TOKEN")

    rate_runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                returncode=1,
                stderr="API rate limit exceeded for 1.2.3.4\n",
            )
        ]
    )
    rate_backend = GitHubGhApiBackend(
        GitHubRepoRef(owner="Org", repo="Skills"), runner=rate_runner
    )

    with pytest.raises(SourceBackendError) as rate_error:
        rate_backend.list_candidate_skill_files()

    assert rate_error.value.operation == "listing candidate SKILL.md files"
    assert "rate limit" in rate_error.value.detail
    assert_hint_contains(rate_error.value, "gh auth login", "GH_TOKEN")


def test_github_https_api_backend_reads_index_candidates_and_materializes_folder(
    tmp_path: Path,
):
    http_get = FakeHttpGet(
        [
            GitHubHttpResponse(200, b'{"content":"c2NoZW1hX3ZlcnNpb24gPSAxCg==\\n"}'),
            GitHubHttpResponse(
                200,
                b'[{"name":"team","path":"team","type":"dir"},'
                b'{"name":".pi","path":".pi","type":"dir"},'
                b'{"name":"README.md","path":"README.md","type":"file"}]',
            ),
            GitHubHttpResponse(
                200,
                b'[{"name":"alpha","path":"skills/alpha","type":"dir"},'
                b'{"name":".hidden","path":"skills/.hidden","type":"dir"},'
                b'{"name":"README.md","path":"skills/README.md","type":"file"}]',
            ),
            GitHubHttpResponse(
                200,
                b'[{"name":"SKILL.md","path":"skills/alpha/SKILL.md","type":"file"}]',
            ),
            GitHubHttpResponse(
                200,
                b'[{"name":"beta","path":"team/skills/beta","type":"dir"}]',
            ),
            GitHubHttpResponse(
                200,
                b'[{"name":"SKILL.md","path":"team/skills/beta/SKILL.md","type":"file"}]',
            ),
            GitHubHttpResponse(
                200,
                b'[{"name":"deep","path":"packages/agents/pi/skills/deep",'
                b'"type":"dir"}]',
            ),
            GitHubHttpResponse(
                200,
                b'[{"name":"SKILL.md",'
                b'"path":"packages/agents/pi/skills/deep/SKILL.md",'
                b'"type":"file"}]',
            ),
            GitHubHttpResponse(
                200,
                b'[{"name":"SKILL.md","path":"skills/alpha/SKILL.md",'
                b'"type":"file"},'
                b'{"name":"nested","path":"skills/alpha/nested","type":"dir"}]',
            ),
            GitHubHttpResponse(200, b'{"content":"YWxwaGEgc2tpbGwK\\n"}'),
            GitHubHttpResponse(
                200,
                b'[{"name":"README.md","path":"skills/alpha/nested/README.md",'
                b'"type":"file"}]',
            ),
            GitHubHttpResponse(200, b'{"content":"bmVzdGVkCg==\\n"}'),
        ]
    )
    backend = GitHubHttpsApiBackend(
        GitHubRepoRef(owner="Org", repo="Skills"),
        http_get=http_get,
        env={"GH_TOKEN": "token-123"},
    )
    destination = tmp_path / "materialized"

    assert backend.read_index() == b"schema_version = 1\n"
    assert backend.list_candidate_skill_files(("packages/agents/pi/skills",)) == [
        "packages/agents/pi/skills/deep/SKILL.md",
        "skills/alpha/SKILL.md",
        "team/skills/beta/SKILL.md",
    ]
    discovery_call_count = len(http_get.calls)
    backend.materialize_folder("skills/alpha", destination)

    assert (destination / "SKILL.md").read_bytes() == b"alpha skill\n"
    assert (destination / "nested" / "README.md").read_bytes() == b"nested\n"
    assert [url for url, _headers in http_get.calls[:discovery_call_count]] == [
        "https://api.github.com/repos/Org/Skills/contents/.sv/index.toml",
        "https://api.github.com/repos/Org/Skills/contents",
        "https://api.github.com/repos/Org/Skills/contents/skills",
        "https://api.github.com/repos/Org/Skills/contents/skills/alpha",
        "https://api.github.com/repos/Org/Skills/contents/team/skills",
        "https://api.github.com/repos/Org/Skills/contents/team/skills/beta",
        "https://api.github.com/repos/Org/Skills/contents/packages/agents/pi/skills",
        "https://api.github.com/repos/Org/Skills/contents/packages/agents/pi/skills/deep",
    ]
    assert all(
        headers["Authorization"] == "Bearer token-123"
        for _url, headers in http_get.calls
    )
    assert "skills/alpha/nested/README.md" not in "\n".join(
        url for url, _headers in http_get.calls[:discovery_call_count]
    )


def test_github_default_http_get_returns_success_response(monkeypatch: pytest.MonkeyPatch):
    class Response:
        status = 200
        headers = {"x-ratelimit-remaining": "42"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size: int = -1) -> bytes:
            return b'{"ok":true}'

    def urlopen(request, *, timeout: int):
        assert request.full_url == "https://api.github.com/repos/Org/Skills"
        assert timeout == 15
        return Response()

    monkeypatch.setattr(github_module.urllib_request, "urlopen", urlopen)

    response = github_module._default_github_http_get(
        "https://api.github.com/repos/Org/Skills", {"Accept": "application/json"}
    )

    assert response.status == 200
    assert response.body == b'{"ok":true}'
    assert response.headers == {"x-ratelimit-remaining": "42"}


def test_github_default_http_get_returns_http_error_response(
    monkeypatch: pytest.MonkeyPatch,
):
    def urlopen(_request, *, timeout: int):
        assert timeout == 15
        headers = Message()
        headers["x-github-request-id"] = "abc"
        raise github_module.urllib_error.HTTPError(
            "https://api.github.com/repos/Org/Skills",
            404,
            "Not Found",
            headers,
            BytesIO(b'{"message":"Not Found"}'),
        )

    monkeypatch.setattr(github_module.urllib_request, "urlopen", urlopen)

    response = github_module._default_github_http_get(
        "https://api.github.com/repos/Org/Skills", {}
    )

    assert response.status == 404
    assert response.body == b'{"message":"Not Found"}'
    assert response.headers == {"x-github-request-id": "abc"}


def test_github_https_api_backend_reports_rate_limit_as_structured_fallback_reason():
    http_get = FakeHttpGet(
        [
            GitHubHttpResponse(
                403,
                b'{"message":"API rate limit exceeded for 1.2.3.4"}',
                headers={"x-ratelimit-remaining": "0"},
            )
        ]
    )
    backend = GitHubHttpsApiBackend(
        GitHubRepoRef(owner="Org", repo="Skills"), http_get=http_get, env={}
    )

    with pytest.raises(SourceBackendError) as error:
        backend.list_candidate_skill_files()

    assert error.value.operation == "listing candidate SKILL.md files"
    assert "HTTP 403" in error.value.detail
    assert "rate limit" in error.value.detail
    assert_hint_contains(error.value, "GH_TOKEN", "GITHUB_TOKEN")


def test_github_https_api_backend_reports_auth_failure_as_structured_fallback_reason():
    http_get = FakeHttpGet(
        [
            GitHubHttpResponse(
                401,
                b'{"message":"Bad credentials"}',
            )
        ]
    )
    backend = GitHubHttpsApiBackend(
        GitHubRepoRef(owner="Org", repo="Private"), http_get=http_get, env={}
    )

    with pytest.raises(SourceBackendError) as error:
        backend.read_file("skills/private/SKILL.md")

    assert error.value.operation == "reading remote file"
    assert "HTTP 401" in error.value.detail
    assert "Bad credentials" in error.value.detail
    assert_hint_contains(error.value, "GH_TOKEN", "GITHUB_TOKEN")


def test_github_https_api_backend_uses_github_token_and_omits_empty_authorization():
    github_token_http_get = FakeHttpGet(
        [GitHubHttpResponse(200, b'{"content":"YWxwaGEK\\n"}')]
    )
    github_token_backend = GitHubHttpsApiBackend(
        GitHubRepoRef(owner="Org", repo="Skills"),
        http_get=github_token_http_get,
        env={"GITHUB_TOKEN": "github-token"},
    )

    assert github_token_backend.read_file("skills/alpha/SKILL.md") == b"alpha\n"
    assert github_token_http_get.calls[0][1]["Authorization"] == "Bearer github-token"

    unauthenticated_http_get = FakeHttpGet(
        [GitHubHttpResponse(200, b'{"content":"YmV0YQo=\\n"}')]
    )
    unauthenticated_backend = GitHubHttpsApiBackend(
        GitHubRepoRef(owner="Org", repo="Skills"),
        http_get=unauthenticated_http_get,
        env={},
    )

    assert unauthenticated_backend.read_file("skills/beta/SKILL.md") == b"beta\n"
    assert "Authorization" not in unauthenticated_http_get.calls[0][1]


def test_github_https_api_backend_rejects_unsupported_file_content_encoding():
    http_get = FakeHttpGet(
        [GitHubHttpResponse(200, b'{"content":"","encoding":"none"}')]
    )
    backend = GitHubHttpsApiBackend(
        GitHubRepoRef(owner="Org", repo="Skills"), http_get=http_get, env={}
    )

    with pytest.raises(SourceBackendError) as error:
        backend.read_file("skills/large/SKILL.md")

    assert error.value.operation == "reading remote file"
    assert "encoding" in error.value.detail


def test_fake_source_backend_lists_bounded_candidate_skill_files_without_git():
    backend = FakeSourceBackend(
        {
            "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha.\n---\n",
            "team/skills/beta/SKILL.md": "---\nname: beta\ndescription: Beta.\n---\n",
            "too/deep/skills/ignored/SKILL.md": "---\nname: ignored\ndescription: Ignored.\n---\n",
            "skills/skills/nested/SKILL.md": "---\nname: nested\ndescription: Nested.\n---\n",
            "packages/agents/pi/skills/deep/SKILL.md": "---\nname: deep\ndescription: Deep.\n---\n",
            ".pi/skills/project-local/SKILL.md": "---\nname: project-local\ndescription: Ignored.\n---\n",
        }
    )

    assert backend.list_candidate_skill_files(("packages/agents/pi/skills",)) == [
        "packages/agents/pi/skills/deep/SKILL.md",
        "skills/alpha/SKILL.md",
        "team/skills/beta/SKILL.md",
    ]
    assert "skills/skills/nested/SKILL.md" in backend.list_candidate_skill_files(
        ("skills/skills",)
    )


def test_git_local_backend_skips_unsafe_top_level_candidate_root_names(
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    (repo_path / "skills" / "valid").mkdir(parents=True)
    (repo_path / "skills" / "valid" / "SKILL.md").write_text(
        "---\nname: valid\ndescription: Valid.\n---\n"
    )
    (repo_path / "bad:name" / "skills" / "ignored").mkdir(parents=True)
    (repo_path / "bad:name" / "skills" / "ignored" / "SKILL.md").write_text(
        "---\nname: ignored\ndescription: Ignored.\n---\n"
    )
    (repo_path / "skills" / "skills" / "nested").mkdir(parents=True)
    (repo_path / "skills" / "skills" / "nested" / "SKILL.md").write_text(
        "---\nname: nested\ndescription: Nested.\n---\n"
    )
    backend = GitLocalSourceBackend(repo_path)

    assert backend.list_candidate_skill_files() == ["skills/valid/SKILL.md"]
    assert "skills/skills/nested/SKILL.md" in backend.list_candidate_skill_files(
        ("skills/skills",)
    )


def test_github_backend_skips_unsafe_top_level_candidate_root_names():
    runner = FakeRunner(
        [
            completed(
                ["gh", "api"],
                stdout='[{"name":"bad:name","path":"bad:name","type":"dir"}]',
            ),
            completed(["gh", "api"], stdout="[]"),
        ]
    )
    backend = GitHubGhApiBackend(GitHubRepoRef(owner="Org", repo="Skills"), runner=runner)

    assert backend.list_candidate_skill_files() == []
    assert runner.calls == [
        (["gh", "api", "/repos/Org/Skills/contents"], None),
        (["gh", "api", "/repos/Org/Skills/contents/skills"], None),
    ]


def test_git_local_backend_rejects_symlinked_candidate_root_ancestor(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    repo_path = tmp_path / "repo"
    outside = tmp_path / "outside"
    (outside / "skills" / "alpha").mkdir(parents=True)
    (outside / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha.\n---\n"
    )
    repo_path.mkdir()
    (repo_path / "linked").symlink_to(outside, target_is_directory=True)
    backend = GitLocalSourceBackend(repo_path)

    with pytest.raises(SvError, match="Source skills path must not be a symlink"):
        backend.list_candidate_skill_files(("linked/skills",))


def test_git_local_backend_rejects_symlinked_materialization_ancestor(
    tmp_path: Path,
):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    repo_path = tmp_path / "repo"
    outside = tmp_path / "outside"
    (outside / "skills" / "alpha").mkdir(parents=True)
    (outside / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha.\n---\n"
    )
    repo_path.mkdir()
    (repo_path / "linked").symlink_to(outside, target_is_directory=True)
    backend = GitLocalSourceBackend(repo_path)

    with pytest.raises(SvError, match="Source skills path must not be a symlink"):
        backend.materialize_folder("linked/skills/alpha", tmp_path / "materialized")


def test_git_local_backend_rejects_materialization_over_file_limit_before_copying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = tmp_path / "repo"
    source = repo_path / "skills" / "alpha"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("remote\n")
    destination = tmp_path / "materialized"
    backend = GitLocalSourceBackend(repo_path)

    monkeypatch.setattr(materialization_module, "_MAX_MATERIALIZATION_FILES", 0)

    with pytest.raises(SvError, match="file limit"):
        backend.materialize_folder("skills/alpha", destination)

    assert not destination.exists()


def test_git_local_backend_rejects_oversized_materialization_before_copying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = tmp_path / "repo"
    source = repo_path / "skills" / "alpha"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("large\n")
    destination = tmp_path / "materialized"
    backend = GitLocalSourceBackend(repo_path)

    monkeypatch.setattr(materialization_module, "_MAX_MATERIALIZATION_BYTES", 4)

    with pytest.raises(SvError, match="byte limit"):
        backend.materialize_folder("skills/alpha", destination)

    assert not destination.exists()


def test_git_local_backend_rejects_too_deep_materialization_before_copying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = tmp_path / "repo"
    nested = repo_path / "skills" / "alpha" / "docs" / "deep"
    nested.mkdir(parents=True)
    (nested / "usage.md").write_text("remote\n")
    destination = tmp_path / "materialized"
    backend = GitLocalSourceBackend(repo_path)

    monkeypatch.setattr(materialization_module, "_MAX_MATERIALIZATION_DEPTH", 1)

    with pytest.raises(SvError, match="depth limit"):
        backend.materialize_folder("skills/alpha", destination)

    assert not destination.exists()


def test_git_local_backend_rejects_symlinked_index_ancestor(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    repo_path = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside_index = outside / "index.toml"
    outside.mkdir(parents=True)
    outside_index.write_text("schema_version = 1\n")
    repo_path.mkdir()
    (repo_path / ".sv").symlink_to(outside, target_is_directory=True)
    backend = GitLocalSourceBackend(repo_path)

    with pytest.raises(SvError, match="Source file path must not be a symlink"):
        backend.read_index()


def test_git_local_backend_limits_metadata_file_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = tmp_path / "repo"
    skill_file = repo_path / "skills" / "alpha" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("large\n")
    backend = GitLocalSourceBackend(repo_path)
    monkeypatch.setattr(base_module, "_MAX_SOURCE_SKILL_FILE_BYTES", 4, raising=False)

    with pytest.raises(SourceBackendError, match="source metadata size limit"):
        backend.read_file("skills/alpha/SKILL.md")


def test_git_local_backend_limits_index_file_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = tmp_path / "repo"
    index_file = repo_path / ".sv" / "index.toml"
    index_file.parent.mkdir(parents=True)
    index_file.write_text("schema_version = 1\n")
    backend = GitLocalSourceBackend(repo_path)
    monkeypatch.setattr(base_module, "_MAX_SOURCE_INDEX_BYTES", 4, raising=False)

    with pytest.raises(SourceBackendError, match="source index size limit"):
        backend.read_index()


def test_git_local_backend_reports_metadata_read_os_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = tmp_path / "repo"
    skill_file = repo_path / "skills" / "alpha" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("alpha\n")
    backend = GitLocalSourceBackend(repo_path)
    original_open = Path.open

    def fail_open(path, *args, **kwargs):
        if path == skill_file:
            raise OSError("boom")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)

    with pytest.raises(SourceBackendError, match="could not be read: boom"):
        backend.read_file("skills/alpha/SKILL.md")


def test_fake_source_backend_limits_metadata_reads(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(base_module, "_MAX_SOURCE_SKILL_FILE_BYTES", 4, raising=False)
    monkeypatch.setattr(base_module, "_MAX_SOURCE_INDEX_BYTES", 4, raising=False)
    skill_backend = FakeSourceBackend({"skills/alpha/SKILL.md": "large\n"})
    index_backend = FakeSourceBackend({".sv/index.toml": "schema_version = 1\n"})

    with pytest.raises(SourceBackendError, match="source metadata size limit"):
        skill_backend.read_file("skills/alpha/SKILL.md")
    with pytest.raises(SourceBackendError, match="source index size limit"):
        index_backend.read_index()


def test_fake_source_backend_reads_index_and_materializes_selected_folder(
    tmp_path: Path,
):
    backend = FakeSourceBackend(
        {
            ".sv/index.toml": "schema_version = 1\n",
            "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha.\n---\n",
            "skills/alpha/nested/README.md": "nested\n",
            "skills/beta/SKILL.md": "---\nname: beta\ndescription: Beta.\n---\n",
        }
    )
    destination = tmp_path / "materialized"

    assert backend.read_index() == b"schema_version = 1\n"
    backend.materialize_folder("skills/alpha", destination)

    assert (destination / "SKILL.md").read_text() == (
        "---\nname: alpha\ndescription: Alpha.\n---\n"
    )
    assert (destination / "nested" / "README.md").read_text() == "nested\n"
    assert not (destination / ".." / "beta").exists()


def test_git_backend_fallback_can_continue_after_treeless_leaves_directory(
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    calls: list[tuple[list[str], Path | None]] = []

    def runner(args, cwd=None):
        command = list(args)
        calls.append((command, cwd))
        if command == ["git", "--version"]:
            return completed(command, stdout="git version 2.0\n")
        if command[:2] == ["git", "clone"] and "--filter=tree:0" in command:
            (repo_path / ".git").mkdir(parents=True)
            return completed(command, returncode=128, stderr="tree filter failed\n")
        if command[:2] == ["git", "clone"] and "--filter=blob:none" in command:
            assert not repo_path.exists()
            (repo_path / ".git").mkdir(parents=True)
            return completed(command)
        if command[:4] == ["git", "sparse-checkout", "set", "--no-cone"]:
            (repo_path / ".sv").mkdir(parents=True, exist_ok=True)
            (repo_path / ".sv" / "index.toml").write_text("schema_version = 1\n")
            return completed(command)
        return completed(command)

    treeless = GitTreelessPartialBackend(
        "https://example.com/skills.git", repo_path, runner=runner
    )
    blobless = GitBloblessSparseBackend(
        "https://example.com/skills.git", repo_path, runner=runner
    )

    with pytest.raises(SourceBackendError):
        treeless.read_index()
    assert blobless.read_index() == b"schema_version = 1\n"

    clone_filters = [
        next(arg for arg in args if arg.startswith("--filter="))
        for args, _cwd in calls
        if args[:2] == ["git", "clone"]
    ]
    assert clone_filters == ["--filter=tree:0", "--filter=blob:none"]


def test_git_treeless_partial_backend_removes_selected_folder_if_metadata_restore_fails(
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    calls: list[tuple[list[str], Path | None]] = []

    def runner(args, cwd=None):
        command = list(args)
        calls.append((command, cwd))
        if command == ["git", "--version"]:
            return completed(command, stdout="git version 2.0\n")
        if command[:2] == ["git", "clone"]:
            (repo_path / ".git").mkdir(parents=True, exist_ok=True)
            return completed(command)
        if command[:4] == ["git", "sparse-checkout", "set", "--no-cone"]:
            patterns = set(command[4:])
            if "/skills/alpha/**" in patterns:
                (repo_path / "skills" / "alpha").mkdir(parents=True, exist_ok=True)
                (repo_path / "skills" / "alpha" / "SKILL.md").write_text(
                    "---\nname: alpha\ndescription: Alpha.\n---\n"
                )
                (repo_path / "skills" / "alpha" / "notes.md").write_text("full\n")
                return completed(command)
            if "/skills/*/SKILL.md" in patterns:
                return completed(command, returncode=1, stderr="restore failed\n")
        return completed(command)

    backend = GitTreelessPartialBackend(
        "https://example.com/skills.git", repo_path, runner=runner
    )

    with pytest.raises(SourceBackendError, match="restore metadata-only source cache"):
        backend.materialize_folder("skills/alpha", tmp_path / "materialized")

    assert not (repo_path / "skills" / "alpha" / "notes.md").exists()


def test_git_treeless_partial_backend_fetches_metadata_and_selected_folder_without_full_clone(
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    calls: list[tuple[list[str], Path | None]] = []

    def runner(args, cwd=None):
        command = list(args)
        calls.append((command, cwd))
        if command == ["git", "--version"]:
            return completed(command, stdout="git version 2.0\n")
        if command[:2] == ["git", "clone"]:
            (repo_path / ".git").mkdir(parents=True, exist_ok=True)
            return completed(command)
        if command == ["git", "remote", "get-url", "origin"]:
            return completed(command, stdout="https://example.com/skills.git\n")
        if command[:4] == ["git", "sparse-checkout", "set", "--no-cone"]:
            patterns = set(command[4:])
            if "/.sv/index.toml" in patterns:
                (repo_path / ".sv").mkdir(parents=True, exist_ok=True)
                (repo_path / ".sv" / "index.toml").write_text("schema_version = 1\n")
            if "/skills/*/SKILL.md" in patterns:
                (repo_path / "skills" / "alpha").mkdir(parents=True, exist_ok=True)
                (repo_path / "skills" / "alpha" / "SKILL.md").write_text(
                    "---\nname: alpha\ndescription: Alpha.\n---\n"
                )
            if "/*/skills/*/SKILL.md" in patterns:
                (repo_path / "team" / "skills" / "beta").mkdir(
                    parents=True, exist_ok=True
                )
                (repo_path / "team" / "skills" / "beta" / "SKILL.md").write_text(
                    "---\nname: beta\ndescription: Beta.\n---\n"
                )
            if "/packages/agents/pi/skills/*/SKILL.md" in patterns:
                (repo_path / "packages" / "agents" / "pi" / "skills" / "deep").mkdir(
                    parents=True, exist_ok=True
                )
                (
                    repo_path
                    / "packages"
                    / "agents"
                    / "pi"
                    / "skills"
                    / "deep"
                    / "SKILL.md"
                ).write_text("---\nname: deep\ndescription: Deep.\n---\n")
            if "/skills/alpha/**" in patterns:
                (repo_path / "skills" / "alpha" / "nested").mkdir(
                    parents=True, exist_ok=True
                )
                (repo_path / "skills" / "alpha" / "nested" / "README.md").write_text(
                    "nested\n"
                )
            return completed(command)
        return completed(command)

    backend = GitTreelessPartialBackend(
        "https://example.com/skills.git", repo_path, runner=runner
    )
    destination = tmp_path / "materialized"

    assert backend.read_index() == b"schema_version = 1\n"
    assert backend.list_candidate_skill_files(("packages/agents/pi/skills",)) == [
        "packages/agents/pi/skills/deep/SKILL.md",
        "skills/alpha/SKILL.md",
        "team/skills/beta/SKILL.md",
    ]
    backend.materialize_folder("skills/alpha", destination)

    assert (destination / "SKILL.md").read_text() == (
        "---\nname: alpha\ndescription: Alpha.\n---\n"
    )
    assert (destination / "nested" / "README.md").read_text() == "nested\n"
    clone_calls = [args for args, _cwd in calls if args[:2] == ["git", "clone"]]
    assert clone_calls == [
        [
            "git",
            "clone",
            "--filter=tree:0",
            "--sparse",
            "--no-checkout",
            "--depth=1",
            "--",
            "https://example.com/skills.git",
            str(repo_path),
        ]
    ]
    assert ["git", "clone", "--", "https://example.com/skills.git", str(repo_path)] not in clone_calls


def test_git_treeless_partial_backend_keeps_generic_metadata_cache_after_skill_reads(
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    source_files = {
        "skills/alpha/SKILL.md": "---\nname: alpha\ndescription: Alpha.\n---\n",
        "skills/beta/SKILL.md": "---\nname: beta\ndescription: Beta.\n---\n",
    }

    def restore_sparse_patterns(patterns: set[str]) -> None:
        repo_path.mkdir(parents=True, exist_ok=True)
        for child in repo_path.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        for relative_path, content in source_files.items():
            file_pattern = f"/{relative_path}"
            if file_pattern not in patterns and "/skills/*/SKILL.md" not in patterns:
                continue
            target = repo_path / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    def runner(args, cwd=None):
        command = list(args)
        if command == ["git", "--version"]:
            return completed(command, stdout="git version 2.0\n")
        if command[:2] == ["git", "clone"]:
            (repo_path / ".git").mkdir(parents=True, exist_ok=True)
            return completed(command)
        if command == ["git", "remote", "get-url", "origin"]:
            return completed(command, stdout="https://example.com/skills.git\n")
        if command[:4] == ["git", "sparse-checkout", "set", "--no-cone"]:
            restore_sparse_patterns(set(command[4:]))
            return completed(command)
        return completed(command)

    refreshed_backend = GitTreelessPartialBackend(
        "https://example.com/skills.git", repo_path, runner=runner, update=True
    )

    assert refreshed_backend.read_index() is None
    assert refreshed_backend.list_candidate_skill_files() == [
        "skills/alpha/SKILL.md",
        "skills/beta/SKILL.md",
    ]
    assert refreshed_backend.read_file("skills/alpha/SKILL.md") == source_files[
        "skills/alpha/SKILL.md"
    ].encode()
    assert refreshed_backend.read_file("skills/beta/SKILL.md") == source_files[
        "skills/beta/SKILL.md"
    ].encode()

    cached_backend = GitTreelessPartialBackend(
        "https://example.com/skills.git", repo_path, runner=runner, update=False
    )

    assert cached_backend.read_index() is None
    assert cached_backend.list_candidate_skill_files() == [
        "skills/alpha/SKILL.md",
        "skills/beta/SKILL.md",
    ]


def test_source_backend_failure_formats_actionable_message():
    error = SourceBackendError(
        "listing candidate SKILL.md files",
        "rate limit exceeded",
        hint="authenticate with gh auth login or GH_TOKEN",
    )
    failure = SourceBackendFailure.from_error(
        repo_id="Org/Skills",
        repo_url="https://github.com/Org/Skills.git",
        backend="github-api",
        error=error,
    )

    assert failure.backend == "github-api"
    assert failure.detail == "rate limit exceeded"
    assert failure.user_message == (
        "github-api backend failed for Org/Skills while listing candidate SKILL.md files: "
        "rate limit exceeded. authenticate with gh auth login or GH_TOKEN"
    )



def test_source_repo_lock_key_falls_back_when_resolve_detects_symlink_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = tmp_path / "cache" / "repo"

    def fail_resolve(self: Path) -> Path:
        if self == repo_path:
            raise RuntimeError("Symlink loop from '/tmp/cache/repo'")
        return self.absolute()

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    assert git_module._source_repo_lock_key(repo_path) == repo_path.absolute()


def test_sparse_backend_read_file_serializes_prepare_and_local_read_for_same_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = tmp_path / "cache" / "repo"
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    first_inside = threading.Event()
    second_thread_started = threading.Event()
    second_inside = threading.Event()
    release = threading.Event()

    def fake_prepare(*args, **kwargs):
        return None

    def fake_read(self, path):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
            if path == "skills/alpha/SKILL.md":
                first_inside.set()
            else:
                second_inside.set()
        assert release.wait(2), "test did not release metadata read"
        with active_lock:
            active -= 1
        return b"---\nname: alpha\ndescription: Alpha.\n---\n"

    monkeypatch.setattr(git_module, "_ensure_sparse_git_repo", fake_prepare)
    monkeypatch.setattr(git_module.GitLocalSourceBackend, "read_file", fake_read)

    first = GitTreelessPartialBackend("https://example.com/repo.git", repo_path, runner=FakeRunner([]))
    second = GitTreelessPartialBackend("https://example.com/repo.git", repo_path, runner=FakeRunner([]))
    errors: list[BaseException] = []

    def run_backend(backend, path):
        try:
            if path == "skills/beta/SKILL.md":
                second_thread_started.set()
            backend.read_file(path)
        except BaseException as exc:  # noqa: BLE001 - test captures worker failures
            errors.append(exc)

    thread_a = threading.Thread(target=run_backend, args=(first, "skills/alpha/SKILL.md"))
    thread_b = threading.Thread(target=run_backend, args=(second, "skills/beta/SKILL.md"))
    thread_a.start()
    assert first_inside.wait(2), "first metadata read did not start"
    thread_b.start()
    assert second_thread_started.wait(2), "second metadata read thread did not start"
    assert not second_inside.wait(0.25), "second metadata read entered while first held same-repo lock"
    release.set()
    thread_a.join(2)
    thread_b.join(2)

    assert errors == []
    assert second_inside.is_set(), "second metadata read never ran after first released"
    assert max_active == 1



def test_sparse_backend_materialization_serializes_same_repo_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = tmp_path / "cache" / "repo"
    active = 0
    max_active = 0
    active_lock = threading.Lock()
    first_inside = threading.Event()
    second_thread_started = threading.Event()
    second_inside = threading.Event()
    release = threading.Event()

    def fake_prepare(*args, **kwargs):
        return None

    def fake_copy(self, source_path, destination):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
            if destination.name == "a":
                first_inside.set()
            else:
                second_inside.set()
        assert release.wait(2), "test did not release materialization"
        with active_lock:
            active -= 1
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "SKILL.md").write_text("---\nname: alpha\ndescription: Alpha.\n---\n")

    monkeypatch.setattr(git_module, "_ensure_sparse_git_repo", fake_prepare)
    monkeypatch.setattr(git_module, "_remove_backend_cache_folder", lambda *args, **kwargs: None)
    monkeypatch.setattr(git_module.GitLocalSourceBackend, "materialize_folder", fake_copy)

    first = GitTreelessPartialBackend("https://example.com/repo.git", repo_path, runner=FakeRunner([]))
    second = GitTreelessPartialBackend("https://example.com/repo.git", repo_path, runner=FakeRunner([]))
    errors: list[BaseException] = []

    def run_backend(backend, destination):
        try:
            if destination.name == "b":
                second_thread_started.set()
            backend.materialize_folder("skills/alpha", destination)
        except BaseException as exc:  # noqa: BLE001 - test captures worker failures
            errors.append(exc)

    thread_a = threading.Thread(target=run_backend, args=(first, tmp_path / "a"))
    thread_b = threading.Thread(target=run_backend, args=(second, tmp_path / "b"))
    thread_a.start()
    assert first_inside.wait(2), "first materialization did not start"
    thread_b.start()
    assert second_thread_started.wait(2), "second materialization thread did not start"
    assert not second_inside.wait(0.25), "second materialization entered while first held same-repo lock"
    release.set()
    thread_a.join(2)
    thread_b.join(2)

    assert errors == []
    assert second_inside.is_set(), "second materialization never ran after first released"
    assert max_active == 1

def test_ensure_source_repo_excludes_nested_skills_root_from_default_metadata(
    tmp_path: Path,
):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"]),
            completed(["git", "sparse-checkout", "init", "--no-cone"]),
            completed(["git", "sparse-checkout", "set", "--no-cone"]),
            completed(["git", "checkout", "--force", "HEAD"]),
        ]
    )

    ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    sparse_set = next(
        args for args, _cwd in runner.calls if args[:4] == ["git", "sparse-checkout", "set", "--no-cone"]
    )
    assert "/*/skills/*/SKILL.md" in sparse_set
    assert "!/skills/skills/*/SKILL.md" in sparse_set
    assert sparse_set.index("!/skills/skills/*/SKILL.md") > sparse_set.index(
        "/*/skills/*/SKILL.md"
    )


def test_ensure_source_repos_includes_configured_skills_paths_in_metadata_checkout(
    tmp_path: Path,
):
    paths = SvPaths.from_home(tmp_path)
    repos = [
        RepoConfig(
            id="Org/Skills",
            url="https://github.com/Org/Skills.git",
            skills_paths=("packages/agents/pi/skills",),
        )
    ]
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"]),
            completed(["git", "sparse-checkout", "init", "--no-cone"]),
            completed(["git", "sparse-checkout", "set", "--no-cone"]),
            completed(["git", "checkout", "--force", "HEAD"]),
        ]
    )

    ensure_source_repos(repos, paths, runner=runner)

    sparse_set = next(
        args for args, _cwd in runner.calls if args[:4] == ["git", "sparse-checkout", "set", "--no-cone"]
    )
    assert "/packages/agents/pi/skills/*/SKILL.md" in sparse_set


def test_ensure_source_repo_fails_when_git_ignores_lightweight_clone_options(
    tmp_path: Path,
):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"

    class IgnoredLightweightOptionsRunner:
        def __init__(self):
            self.calls = []

        def __call__(self, args, cwd=None):
            command = list(args)
            self.calls.append((command, cwd))
            if command == ["git", "--version"]:
                return completed(command, stdout="git version 2.0\n")
            if command[:2] == ["git", "clone"]:
                return completed(
                    command,
                    stderr=(
                        "Cloning into cache...\n"
                        "warning: --depth is ignored in local clones; use file:// instead.\n"
                        "warning: --filter is ignored in local clones; use file:// instead.\n"
                        "done.\n"
                    ),
                )
            return completed(command)

    runner = IgnoredLightweightOptionsRunner()

    with pytest.raises(SvError, match="ignored requested lightweight clone options"):
        ensure_source_repo(str(tmp_path / "source"), repo_path, runner=runner)

    clone_calls = [args for args, _cwd in runner.calls if args[:2] == ["git", "clone"]]
    assert len(clone_calls) == 2
    assert not repo_path.exists()


def test_ensure_source_repo_uses_treeless_sparse_checkout_when_missing(tmp_path: Path):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"]),
            completed(["git", "sparse-checkout", "init", "--no-cone"]),
            completed(["git", "sparse-checkout", "set", "--no-cone"]),
            completed(["git", "checkout", "--force", "HEAD"]),
        ]
    )

    ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == [
        (["git", "--version"], None),
        (
            [
                "git",
                "clone",
                "--filter=tree:0",
                "--sparse",
                "--no-checkout",
                "--depth=1",
                "--",
                "https://example.com/skills.git",
                str(repo_path),
            ],
            None,
        ),
        (["git", "sparse-checkout", "init", "--no-cone"], repo_path),
        (
            [
                "git",
                "sparse-checkout",
                "set",
                "--no-cone",
                "/.sv/index.toml",
                "/skills/*/SKILL.md",
                "/*/skills/*/SKILL.md",
                "!/skills/skills/*/SKILL.md",
            ],
            repo_path,
        ),
        (["git", "checkout", "--force", "HEAD"], repo_path),
    ]
    assert repo_path.parent.exists()


def test_ensure_source_repo_falls_back_to_blobless_sparse_without_full_clone(
    tmp_path: Path,
):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"], returncode=128, stderr="filter unsupported\n"),
            completed(["git", "clone"]),
            completed(["git", "sparse-checkout", "init", "--no-cone"]),
            completed(["git", "sparse-checkout", "set", "--no-cone"]),
            completed(["git", "checkout", "--force", "HEAD"]),
        ]
    )

    ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    clone_calls = [args for args, _cwd in runner.calls if args[:2] == ["git", "clone"]]
    assert clone_calls == [
        [
            "git",
            "clone",
            "--filter=tree:0",
            "--sparse",
            "--no-checkout",
            "--depth=1",
            "--",
            "https://example.com/skills.git",
            str(repo_path),
        ],
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--sparse",
            "--no-checkout",
            "--depth=1",
            "--",
            "https://example.com/skills.git",
            str(repo_path),
        ],
    ]
    assert ["git", "clone", "--", "https://example.com/skills.git", str(repo_path)] not in clone_calls


def test_ensure_source_repo_falls_back_after_failed_treeless_clone_leaves_directory(
    tmp_path: Path,
):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    calls: list[tuple[list[str], Path | None]] = []

    def runner(args, cwd=None):
        command = list(args)
        calls.append((command, cwd))
        if command == ["git", "--version"]:
            return completed(command, stdout="git version 2.0\n")
        if command[:2] == ["git", "clone"] and "--filter=tree:0" in command:
            (repo_path / ".git").mkdir(parents=True)
            return completed(command, returncode=128, stderr="filter unsupported\n")
        if command[:2] == ["git", "clone"] and "--filter=blob:none" in command:
            assert not repo_path.exists()
            (repo_path / ".git").mkdir(parents=True)
            return completed(command)
        return completed(command)

    ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    clone_calls = [args for args, _cwd in calls if args[:2] == ["git", "clone"]]
    assert ["--filter=tree:0", "--filter=blob:none"] == [
        next(arg for arg in call if arg.startswith("--filter="))
        for call in clone_calls
    ]


def test_ensure_source_repo_cleans_up_when_all_lightweight_clones_fail(
    tmp_path: Path,
):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"

    def runner(args, cwd=None):
        command = list(args)
        if command == ["git", "--version"]:
            return completed(command, stdout="git version 2.0\n")
        if command[:2] == ["git", "clone"]:
            (repo_path / ".git").mkdir(parents=True, exist_ok=True)
            return completed(command, returncode=128, stderr="filter unsupported\n")
        return completed(command)

    with pytest.raises(SvError, match="without running a full clone"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert not repo_path.exists()


def test_default_runner_reports_missing_git_as_sv_error(monkeypatch):
    def missing_binary(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(process_module.subprocess, "run", missing_binary)

    with pytest.raises(SvError, match="Git is required"):
        default_runner(["git", "status"])


def test_default_runner_uses_timeout_and_noninteractive_environment(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return completed(args[0], stdout="ok\n")

    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    monkeypatch.setattr(process_module.subprocess, "run", fake_run)

    result = default_runner(["git", "status"], cwd=Path("/tmp/project"))

    assert result.stdout == "ok\n"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (["git", "status"],)
    assert kwargs["cwd"] == Path("/tmp/project")
    assert kwargs["text"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == process_module.DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["env"]["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
    assert kwargs["env"]["GIT_ALLOW_PROTOCOL"] == "file:https:ssh"


def test_default_runner_reports_timeouts_as_command_failures(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(process_module.subprocess, "run", fake_run)

    result = default_runner(["git", "fetch"])

    assert result.returncode == 124
    assert "timed out after" in result.stderr


def test_default_runner_timeout_result_preserves_captured_streams(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            args[0], kwargs["timeout"], output=b"partial \xff output", stderr="ssh prompt"
        )

    monkeypatch.setattr(process_module.subprocess, "run", fake_run)

    result = default_runner(["gh", "api", "/repos/Org/Skills"])

    assert result.returncode == 124
    assert result.stdout == "partial � output"
    assert result.stderr.endswith(": ssh prompt")


def test_default_runner_preserves_existing_batchmode_ssh_command(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return completed(args[0])

    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /tmp/key -o BatchMode=yes")
    monkeypatch.setattr(process_module.subprocess, "run", fake_run)

    default_runner(["git", "fetch"])

    assert calls[0][1]["env"]["GIT_SSH_COMMAND"] == "ssh -i /tmp/key -o BatchMode=yes"
    assert calls[0][1]["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_default_runner_adds_batchmode_to_existing_ssh_command(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return completed(args[0])

    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /tmp/key")
    monkeypatch.setattr(process_module.subprocess, "run", fake_run)

    default_runner(["git", "fetch"])

    assert calls[0][1]["env"]["GIT_SSH_COMMAND"] == "ssh -i /tmp/key -o BatchMode=yes"


def test_default_runner_reraises_missing_non_git_binary(monkeypatch):
    def missing_binary(*args, **kwargs):
        raise FileNotFoundError("custom")

    monkeypatch.setattr(process_module.subprocess, "run", missing_binary)

    with pytest.raises(FileNotFoundError):
        default_runner(["custom-tool"])


def test_ensure_source_repo_rejects_control_characters_in_repo_url(tmp_path: Path):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    runner = FakeRunner([])

    with pytest.raises(SvError, match="repo cannot contain control characters"):
        ensure_source_repo("https://example.com/skills.git\x1b[2J", repo_path, runner=runner)

    assert runner.calls == []


def test_ensure_source_repo_reports_missing_git_from_runner(tmp_path: Path):
    repo_path = tmp_path / "repo"

    def runner(args, cwd=None):
        raise FileNotFoundError("git")

    with pytest.raises(SvError, match="Git is required"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_reports_exit_code_without_git_output(tmp_path: Path):
    repo_path = tmp_path / "repo"
    runner = FakeRunner(
        [
            completed(["git", "--version"], returncode=2),
        ]
    )

    with pytest.raises(SvError, match="exit code 2"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_reject_symlinked_source_cache_path_handles_paths_outside_cache(tmp_path: Path):
    sources_dir = tmp_path / "sources"
    repo_path = tmp_path / "elsewhere" / "repo"

    reject_symlinked_source_cache_path(repo_path, sources_dir)


def test_reject_symlinked_source_cache_path_reports_inspection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    sources_dir = tmp_path / "sources"
    repo_path = sources_dir / "Org" / "Skills"
    blocked_path = sources_dir / "Org"
    original_is_symlink = Path.is_symlink

    def fail_for_blocked_path(path: Path) -> bool:
        if path == blocked_path:
            raise OSError("permission denied")
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fail_for_blocked_path)

    with pytest.raises(SvError) as exc_info:
        reject_symlinked_source_cache_path(repo_path, sources_dir)

    assert str(exc_info.value) == (
        f"Failed to inspect source path {blocked_path}: permission denied"
    )


def test_ensure_source_repo_reports_cache_path_inspection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_path = tmp_path / "repo"
    runner = FakeRunner([])
    original_is_symlink = Path.is_symlink

    def fail_for_repo_path(path: Path) -> bool:
        if path == repo_path:
            raise OSError("permission denied")
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fail_for_repo_path)

    with pytest.raises(SvError) as exc_info:
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert str(exc_info.value) == (
        f"Failed to inspect source path {repo_path}: permission denied"
    )
    assert runner.calls == []


def test_ensure_source_repo_reports_cache_ancestor_inspection_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    blocked_path = tmp_path / "blocked"
    repo_path = blocked_path / "repo"
    runner = FakeRunner([])
    original_is_symlink = Path.is_symlink

    def fail_for_blocked_path(path: Path) -> bool:
        if path == blocked_path:
            raise OSError("permission denied")
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fail_for_blocked_path)

    with pytest.raises(SvError) as exc_info:
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert str(exc_info.value) == (
        f"Failed to inspect source path {blocked_path}: permission denied"
    )
    assert runner.calls == []


def test_ensure_source_repo_rejects_option_like_repo_url(tmp_path: Path):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    runner = FakeRunner([])

    with pytest.raises(SvError, match="repo cannot start with '-'"):
        ensure_source_repo("--upload-pack=/tmp/fake", repo_path, runner=runner)

    assert runner.calls == []


@pytest.mark.parametrize(
    ("repo_url", "match"),
    [
        ("http://example.com/skills.git", "repo URL must use HTTPS"),
        ("HTTP://example.com/skills.git", "repo URL must use HTTPS"),
        ("https://token@example.com/skills.git", "repo URL cannot contain credentials"),
        ("HTTPS://token@example.com/skills.git", "repo URL cannot contain credentials"),
        ("ssh://git:secret@example.com/skills.git", "repo URL cannot contain credentials"),
        ("SSH://git:secret@example.com/skills.git", "repo URL cannot contain credentials"),
        ("git://example.com/skills.git", "repo URL scheme is not supported"),
        ("ext::sh -c echo-pwn", "repo URL scheme is not supported"),
        ("foo::bar", "repo URL scheme is not supported"),
    ],
)
def test_ensure_source_repo_rejects_cleartext_and_credentialed_repo_urls(
    tmp_path: Path, repo_url: str, match: str
):
    repo_path = tmp_path / ".sv" / "sources" / "default" / "repo"
    runner = FakeRunner([])

    with pytest.raises(SvError, match=match):
        ensure_source_repo(repo_url, repo_path, runner=runner)

    assert runner.calls == []


def test_ensure_source_repo_rejects_symlinked_cache_path(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    real_repo = tmp_path / "real-repo"
    (real_repo / ".git").mkdir(parents=True)
    repo_path = tmp_path / "repo-link"
    repo_path.symlink_to(real_repo, target_is_directory=True)
    runner = FakeRunner([])

    with pytest.raises(SvError, match="Source repo cache path must not be a symlink"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == []


def test_ensure_source_repo_rejects_symlinked_git_dir(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    outside_git = tmp_path / "outside-git"
    outside_git.mkdir()
    (repo_path / ".git").symlink_to(outside_git, target_is_directory=True)
    runner = FakeRunner([])

    with pytest.raises(SvError, match="Source Git metadata path must not be a symlink"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == []


def test_ensure_source_repo_rejects_symlinked_cache_ancestor(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    outside_sources = tmp_path / "outside-sources"
    outside_sources.mkdir()
    sources_link = tmp_path / "sources-link"
    sources_link.symlink_to(outside_sources, target_is_directory=True)
    repo_path = sources_link / "Org" / "Skills" / "repo"
    runner = FakeRunner([])

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == []


def test_ensure_source_repo_updates_existing_clone_with_partial_fetch(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(
                ["git", "remote", "get-url", "origin"],
                stdout="https://example.com/skills.git\n",
            ),
            completed(["git", "fetch", "--depth=1", "--filter=tree:0", "origin"]),
            completed(["git", "sparse-checkout", "init", "--no-cone"]),
            completed(["git", "sparse-checkout", "set", "--no-cone"]),
            completed(["git", "checkout", "--force", "HEAD"]),
        ]
    )

    ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    assert runner.calls == [
        (["git", "--version"], None),
        (["git", "remote", "get-url", "origin"], repo_path),
        (["git", "fetch", "--depth=1", "--filter=tree:0", "origin"], repo_path),
        (["git", "sparse-checkout", "init", "--no-cone"], repo_path),
        (
            [
                "git",
                "sparse-checkout",
                "set",
                "--no-cone",
                "/.sv/index.toml",
                "/skills/*/SKILL.md",
                "/*/skills/*/SKILL.md",
                "!/skills/skills/*/SKILL.md",
            ],
            repo_path,
        ),
        (["git", "checkout", "--force", "FETCH_HEAD"], repo_path),
    ]


def test_ensure_source_repo_can_skip_fetch_for_existing_clone(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(
                ["git", "remote", "get-url", "origin"],
                stdout="https://example.com/skills.git\n",
            ),
            completed(["git", "sparse-checkout", "init", "--no-cone"]),
            completed(["git", "sparse-checkout", "set", "--no-cone"]),
            completed(["git", "checkout", "--force", "HEAD"]),
        ]
    )

    ensure_source_repo(
        "https://example.com/skills.git", repo_path, runner=runner, update=False
    )

    assert runner.calls == [
        (["git", "--version"], None),
        (["git", "remote", "get-url", "origin"], repo_path),
        (["git", "sparse-checkout", "init", "--no-cone"], repo_path),
        (
            [
                "git",
                "sparse-checkout",
                "set",
                "--no-cone",
                "/.sv/index.toml",
                "/skills/*/SKILL.md",
                "/*/skills/*/SKILL.md",
                "!/skills/skills/*/SKILL.md",
            ],
            repo_path,
        ),
        (["git", "checkout", "--force", "HEAD"], repo_path),
    ]


def test_ensure_source_repo_accepts_equivalent_github_remote_url(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(
                ["git", "remote", "get-url", "origin"],
                stdout="https://github.com/Org/Skills.git\n",
            ),
            completed(["git", "fetch", "--depth=1", "--filter=tree:0", "origin"]),
            completed(["git", "sparse-checkout", "init", "--no-cone"]),
            completed(["git", "sparse-checkout", "set", "--no-cone"]),
            completed(["git", "checkout", "--force", "HEAD"]),
        ]
    )

    ensure_source_repo("https://github.com/Org/Skills", repo_path, runner=runner)

    assert runner.calls == [
        (["git", "--version"], None),
        (["git", "remote", "get-url", "origin"], repo_path),
        (["git", "fetch", "--depth=1", "--filter=tree:0", "origin"], repo_path),
        (["git", "sparse-checkout", "init", "--no-cone"], repo_path),
        (
            [
                "git",
                "sparse-checkout",
                "set",
                "--no-cone",
                "/.sv/index.toml",
                "/skills/*/SKILL.md",
                "/*/skills/*/SKILL.md",
                "!/skills/skills/*/SKILL.md",
            ],
            repo_path,
        ),
        (["git", "checkout", "--force", "FETCH_HEAD"], repo_path),
    ]


def test_ensure_source_repo_reports_remote_mismatch(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(
                ["git", "remote", "get-url", "origin"],
                stdout="https://example.com/other.git\n",
            ),
        ]
    )

    with pytest.raises(SvError, match="existing source clone uses"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_escapes_control_characters_in_remote_mismatch(
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    (repo_path / ".git").mkdir(parents=True)
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(
                ["git", "remote", "get-url", "origin"],
                stdout="https://example.com/other.git\x1b[2J\n",
            ),
        ]
    )

    with pytest.raises(SvError) as exc_info:
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    message = str(exc_info.value)
    assert "https://example.com/other.git\\x1b[2J" in message
    assert "\x1b" not in message


def test_ensure_source_repo_reports_git_failure(tmp_path: Path):
    repo_path = tmp_path / "repo"
    runner = FakeRunner(
        [
            completed(["git", "--version"], returncode=1, stderr="git missing\n"),
        ]
    )

    with pytest.raises(SvError, match="Checking Git availability failed"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_escapes_control_characters_in_git_failure(
    tmp_path: Path,
):
    repo_path = tmp_path / "repo"
    runner = FakeRunner(
        [
            completed(
                ["git", "--version"],
                returncode=1,
                stderr="bad\x1b[2J output\n",
            ),
        ]
    )

    with pytest.raises(SvError) as exc_info:
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)

    message = str(exc_info.value)
    assert "bad\\x1b[2J output" in message
    assert "\x1b" not in message


def test_ensure_source_repo_reports_runner_os_errors(tmp_path: Path):
    repo_path = tmp_path / "repo"

    def runner(args, cwd=None):
        raise PermissionError("denied")

    with pytest.raises(SvError, match="Checking Git availability failed: denied"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_reports_non_git_existing_path(tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
        ]
    )

    with pytest.raises(SvError, match="exists but is not a Git clone"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_ensure_source_repo_reports_cache_directory_creation_failures(tmp_path: Path):
    repo_path = tmp_path / ".sv" / "sources" / "Org" / "Skills" / "repo"
    blocker = tmp_path / ".sv" / "sources"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("not a directory\n")
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
        ]
    )

    with pytest.raises(SvError, match="Failed to prepare source cache directory"):
        ensure_source_repo("https://example.com/skills.git", repo_path, runner=runner)


def test_list_source_skills_lists_immediate_skill_folders_only(tmp_path: Path):
    repo_path = tmp_path / "repo"
    (repo_path / "skills" / "alpha").mkdir(parents=True)
    (repo_path / "skills" / "beta").mkdir(parents=True)
    (repo_path / "skills" / "alpha" / "nested").mkdir()
    (repo_path / "skills" / "not-a-dir.md").write_text("not a skill folder\n")

    assert list_source_skills(repo_path) == ["alpha", "beta"]


def test_list_source_skills_returns_empty_when_skills_dir_missing(tmp_path: Path):
    assert list_source_skills(tmp_path / "repo") == []


def test_ensure_source_repos_rejects_symlinked_cache_ancestor(tmp_path: Path):
    if not hasattr(Path, "symlink_to"):
        pytest.skip("symlink support is required")
    paths = SvPaths.from_home(tmp_path)
    outside_org = tmp_path / "outside-org"
    outside_org.mkdir()
    paths.sources_dir.mkdir(parents=True)
    (paths.sources_dir / "Org").symlink_to(outside_org, target_is_directory=True)
    repos = [RepoConfig(id="Org/Skills", url="https://github.com/Org/Skills.git")]
    runner = FakeRunner([])

    with pytest.raises(SvError, match="Source cache path must not contain symlinks"):
        ensure_source_repos(repos, paths, runner=runner)

    assert runner.calls == []


def test_ensure_source_repos_uses_partial_sparse_for_each_configured_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SV_JOBS", "1")
    paths = SvPaths.from_home(tmp_path)
    repos = [
        RepoConfig(id="Org/A", url="https://github.com/Org/A.git"),
        RepoConfig(id="Org/B", url="https://github.com/Org/B.git"),
    ]
    runner = FakeRunner(
        [
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"]),
            completed(["git", "sparse-checkout", "init", "--no-cone"]),
            completed(["git", "sparse-checkout", "set", "--no-cone"]),
            completed(["git", "checkout", "--force", "HEAD"]),
            completed(["git", "--version"], stdout="git version 2.0\n"),
            completed(["git", "clone"]),
            completed(["git", "sparse-checkout", "init", "--no-cone"]),
            completed(["git", "sparse-checkout", "set", "--no-cone"]),
            completed(["git", "checkout", "--force", "HEAD"]),
        ]
    )

    ensured = ensure_source_repos(repos, paths, runner=runner)

    assert ensured == [
        paths.source_repo_for("Org/A"),
        paths.source_repo_for("Org/B"),
    ]
    clone_calls = [args for args, _cwd in runner.calls if args[:2] == ["git", "clone"]]
    assert clone_calls == [
        [
            "git",
            "clone",
            "--filter=tree:0",
            "--sparse",
            "--no-checkout",
            "--depth=1",
            "--",
            "https://github.com/Org/A.git",
            str(paths.source_repo_for("Org/A")),
        ],
        [
            "git",
            "clone",
            "--filter=tree:0",
            "--sparse",
            "--no-checkout",
            "--depth=1",
            "--",
            "https://github.com/Org/B.git",
            str(paths.source_repo_for("Org/B")),
        ],
    ]
    assert all("--filter=tree:0" in call for call in clone_calls)


def test_ensure_source_repos_prepares_independent_repos_in_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = SvPaths.from_home(tmp_path)
    repos = [
        RepoConfig(id="Org/A", url="https://github.com/Org/A.git"),
        RepoConfig(id="Org/B", url="https://github.com/Org/B.git"),
    ]
    started: dict[str, threading.Event] = {
        "Org/A": threading.Event(),
        "Org/B": threading.Event(),
    }

    def fake_ensure_source_repo(repo_url, repo_path, runner, *, update, configured_skills_paths):
        repo_id = "Org/A" if repo_url.endswith("/A.git") else "Org/B"
        peer_id = "Org/B" if repo_id == "Org/A" else "Org/A"
        started[repo_id].set()
        assert started[peer_id].wait(2), "independent source repo preparation did not overlap"
        repo_path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(git_module, "ensure_source_repo", fake_ensure_source_repo)

    ensured = ensure_source_repos(repos, paths, runner=FakeRunner([]), update=True, jobs=2)

    assert ensured == [
        paths.source_repo_for("Org/A"),
        paths.source_repo_for("Org/B"),
    ]


def test_source_utility_helpers_cover_error_details_and_path_matching(tmp_path: Path):
    assert github_module._github_contents_endpoint(GitHubRepoRef("Org", "Repo"), "") == "/repos/Org/Repo/contents"
    assert github_module._github_contents_endpoint(GitHubRepoRef("Org Space", "Repo"), "skills/a b/SKILL.md") == "/repos/Org%20Space/Repo/contents/skills/a%20b/SKILL.md"

    response = GitHubHttpResponse(403, b'{"message": "API rate limit exceeded"}', {"X-RateLimit-Remaining": "0"})
    assert "Forbidden" in github_module._github_https_error_detail(response)
    rate_hint = github_module._github_https_failure_hint(response.status, response.headers, "rate limit")
    auth_hint = github_module._github_https_failure_hint(401, {}, "authentication required")
    not_found_hint = github_module._github_https_failure_hint(404, {}, "not found")
    assert rate_hint is not None and "API limits" in rate_hint
    assert auth_hint is not None and "private repos" in auth_hint
    assert not_found_hint is not None and "private repo" in not_found_hint
    assert github_module._github_https_failure_hint(500, {}, "server error") is None
    assert github_module._github_https_headers({"GH_TOKEN": "  token  "})["Authorization"] == "Bearer token"
    assert github_module._github_https_headers({"GITHUB_TOKEN": "fallback"})["Authorization"] == "Bearer fallback"
    assert github_module._github_https_error_message(b"not json") == "not json"
    assert github_module._github_https_error_message(b"\xff") == ""
    assert github_module._http_status_phrase(599) == ""

    assert github_module._github_item_name({"name": "alpha\n"}, "op") == "alpha\\x0a"
    assert github_module._github_item_path({"path": "skills/alpha"}, "op") == "skills/alpha"
    assert github_module._github_item_type({"type": "dir"}) == "dir"
    assert github_module._github_item_type({"type": 1}) is None
    with pytest.raises(SourceBackendError, match="missing a name"):
        github_module._github_item_name([], "op")
    with pytest.raises(SourceBackendError, match="missing a path"):
        github_module._github_item_path({"path": 1}, "op")
    with pytest.raises(SourceBackendError, match="unsafe path components"):
        github_module._github_item_path({"path": "../secret"}, "op")

    assert base_module._is_direct_child_path("team/skills/alpha", "team/skills")
    assert not base_module._is_direct_child_path("team/skills/nested/alpha", "team/skills")
    assert base_module._is_path_inside("team/skills/alpha/SKILL.md", "team/skills/alpha")
    assert not base_module._is_path_inside("team/skills", "team/skills")
    assert base_module._looks_like_not_found("HTTP 404")
    gh_rate_hint = github_module._gh_failure_hint("rate limit")
    gh_not_found_hint = github_module._gh_failure_hint("not found")
    gh_auth_hint = github_module._gh_failure_hint("HTTP 401")
    assert gh_rate_hint is not None and "api limits" in gh_rate_hint.lower()
    assert gh_not_found_hint is not None and "private" in gh_not_found_hint.lower()
    assert gh_auth_hint is not None and "auth" in gh_auth_hint.lower()
    assert github_module._gh_failure_hint("server exploded") is None

    assert base_module._is_default_candidate_skill_file("skills/alpha/SKILL.md")
    assert base_module._is_default_candidate_skill_file("team/skills/alpha/SKILL.md")
    assert not base_module._is_default_candidate_skill_file("skills/.hidden/SKILL.md")
    assert base_module._is_direct_child_skill_file("custom/alpha/SKILL.md", "custom")
    assert not base_module._is_direct_child_skill_file("custom/.hidden/SKILL.md", "custom")
    assert git_module._same_source_repo("https://github.com/Org/Repo.git", "Org/Repo")
    assert not git_module._same_source_repo("::bad::", "Org/Repo")


@pytest.mark.parametrize(
    "pattern, message",
    [
        ("skills/*/SKILL.md", "repo-root relative"),
        ("/skills\\*/SKILL.md", "repo-root relative"),
        ("/skills:bad/*/SKILL.md", "unsupported"),
        ("/../skills/*/SKILL.md", "unsafe"),
        ("/skills/alpha\x01/SKILL.md", "control"),
    ],
)
def test_normalize_sparse_checkout_patterns_rejects_unsafe_patterns(pattern: str, message: str):
    with pytest.raises(SvError, match=message):
        git_module._normalize_sparse_checkout_patterns([pattern])


def test_sparse_pattern_helpers_dedupe_and_normalize_configured_roots():
    assert git_module._sparse_file_pattern(".sv/index.toml") == "/.sv/index.toml"
    assert git_module._sparse_folder_pattern("skills/alpha") == "/skills/alpha/**"
    patterns = git_module._metadata_sparse_patterns(["custom", "custom"])
    assert patterns.count("/custom/*/SKILL.md") == 1
    assert git_module._normalize_sparse_checkout_patterns(["/skills/*/SKILL.md", "/skills/*/SKILL.md"]) == ["/skills/*/SKILL.md"]


def test_remove_backend_cache_folder_removes_files_directories_and_rejects_symlink(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    file_path = repo / "skills" / "alpha.txt"
    file_path.parent.mkdir()
    file_path.write_text("alpha\n")
    git_module._remove_backend_cache_folder(repo, "skills/alpha.txt")
    assert not file_path.exists()

    dir_path = repo / "skills" / "beta"
    dir_path.mkdir()
    (dir_path / "SKILL.md").write_text("beta\n")
    git_module._remove_backend_cache_folder(repo, "skills/beta")
    assert not dir_path.exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    link = repo / "skills" / "linked"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SvError, match="must not be a symlink"):
        git_module._remove_backend_cache_folder(repo, "skills/linked")


def test_run_git_reports_missing_git_os_errors_empty_failures_and_success(tmp_path: Path):
    def missing(_args, _cwd):
        raise FileNotFoundError("git")

    with pytest.raises(SvError, match="Git is required"):
        git_module._run_git(["status"], tmp_path, missing, "Checking git")

    def os_error(_args, _cwd):
        raise OSError("bad\nerror")

    with pytest.raises(SvError, match=r"bad\\x0aerror"):
        git_module._run_git(["status"], tmp_path, os_error, "Checking git")

    with pytest.raises(SvError, match="exit code 2"):
        git_module._run_git(["status"], tmp_path, lambda args, cwd: completed(args, 2), "Checking git")

    assert git_module._run_git(["status"], tmp_path, lambda args, cwd: completed(args, 0, " ok\n"), "Checking git") == "ok"
