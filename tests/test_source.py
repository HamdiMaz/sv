from pathlib import Path
import shutil
import subprocess

import pytest

from sv.config import RepoConfig, SvPaths
from sv.errors import SvError
from sv import materialization as materialization_module
import sv.source as source_module
from sv.source import (
    GitBloblessSparseBackend,
    GitHubGhApiBackend,
    GitLocalSourceBackend,
    GitHubHttpsApiBackend,
    GitHubHttpResponse,
    GitHubRepoRef,
    GitTreelessPartialBackend,
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
    hint = source_module._gh_failure_hint(detail)

    assert hint is not None
    assert expected in hint


def test_github_item_helpers_reject_malformed_directory_items():
    assert source_module._github_item_type(object()) is None
    assert source_module._github_item_type({"type": 123}) is None

    with pytest.raises(SourceBackendError, match="missing a name"):
        source_module._github_item_name(object(), "listing")
    with pytest.raises(SourceBackendError, match="missing a name"):
        source_module._github_item_name({"name": 123}, "listing")
    with pytest.raises(SourceBackendError, match="missing a path"):
        source_module._github_item_path(object(), "listing")
    with pytest.raises(SourceBackendError, match="missing a path"):
        source_module._github_item_path({"path": 123}, "listing")
    with pytest.raises(SourceBackendError, match="control characters"):
        source_module._github_item_path({"path": "bad\x1fname"}, "listing")


def test_normalize_backend_relative_path_rejects_unsafe_edge_cases():
    for value in ("bad\x1fname", "/absolute", "windows\\path", "bad:name", "", ".", "../up"):
        with pytest.raises(SvError):
            source_module._normalize_backend_relative_path(value)


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


def test_github_gh_api_backend_rejects_oversized_materialization_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(source_module, "_MAX_GITHUB_MATERIALIZATION_BYTES", 4)
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
    monkeypatch.setattr(source_module, "_MAX_GITHUB_MATERIALIZATION_BYTES", 4)
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
    monkeypatch.setattr(source_module, "_MAX_GITHUB_MATERIALIZATION_BYTES", 4)

    def fail_decode(*_args, **_kwargs):
        raise AssertionError("oversized content should be rejected before base64 decode")

    monkeypatch.setattr(source_module.base64, "b64decode", fail_decode)
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
    monkeypatch.setattr(source_module, "_MAX_GITHUB_MATERIALIZATION_ENTRIES", 0)
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
    monkeypatch.setattr(source_module, "_MAX_GITHUB_MATERIALIZATION_DEPTH", 0)
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
    monkeypatch.setattr(source_module, "_MAX_GITHUB_MATERIALIZATION_FILES", 0)
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
    monkeypatch.setattr(source_module, "_MAX_GITHUB_MATERIALIZATION_BYTES", 4)
    monkeypatch.setattr(source_module, "_max_base64_decoded_size", lambda _encoded: 0)
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


def test_github_api_helpers_limit_listing_size_item_size_and_response_body(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(source_module, "_MAX_GITHUB_API_RESPONSE_BYTES", 2)
    backend = GitHubGhApiBackend(
        GitHubRepoRef(owner="Org", repo="Skills"),
        runner=FakeRunner([completed(["gh", "api"], stdout="[{}]")]),
    )
    with pytest.raises(SourceBackendError, match="exceeded size limit"):
        backend.list_candidate_skill_files()

    with pytest.raises(SourceBackendError, match="invalid size"):
        source_module._github_item_size({"size": -1}, "checking item")
    assert source_module._github_item_size(object(), "checking item") is None
    assert source_module._max_base64_decoded_size("") == 0

    class LargeBody:
        def read(self, size: int) -> bytes:
            return b"x" * size

    with pytest.raises(SourceBackendError, match="exceeded size limit"):
        source_module._read_limited_response_body(LargeBody())

    class SmallBody:
        def read(self, _size: int) -> bytes:
            return b"ok"

    assert source_module._read_limited_response_body(SmallBody()) == b"ok"


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

    monkeypatch.setattr(source_module.subprocess, "run", missing_binary)

    with pytest.raises(SvError, match="Git is required"):
        default_runner(["git", "status"])


def test_default_runner_uses_timeout_and_noninteractive_environment(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return completed(args[0], stdout="ok\n")

    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    monkeypatch.setattr(source_module.subprocess, "run", fake_run)

    result = default_runner(["git", "status"], cwd=Path("/tmp/project"))

    assert result.stdout == "ok\n"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (["git", "status"],)
    assert kwargs["cwd"] == Path("/tmp/project")
    assert kwargs["text"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == source_module.DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["env"]["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
    assert kwargs["env"]["GIT_ALLOW_PROTOCOL"] == "file:https:ssh"


def test_default_runner_reports_timeouts_as_command_failures(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(source_module.subprocess, "run", fake_run)

    result = default_runner(["git", "fetch"])

    assert result.returncode == 124
    assert "timed out after" in result.stderr


def test_default_runner_timeout_result_preserves_captured_streams(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            args[0], kwargs["timeout"], output=b"partial \xff output", stderr="ssh prompt"
        )

    monkeypatch.setattr(source_module.subprocess, "run", fake_run)

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
    monkeypatch.setattr(source_module.subprocess, "run", fake_run)

    default_runner(["git", "fetch"])

    assert calls[0][1]["env"]["GIT_SSH_COMMAND"] == "ssh -i /tmp/key -o BatchMode=yes"
    assert calls[0][1]["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_default_runner_adds_batchmode_to_existing_ssh_command(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return completed(args[0])

    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /tmp/key")
    monkeypatch.setattr(source_module.subprocess, "run", fake_run)

    default_runner(["git", "fetch"])

    assert calls[0][1]["env"]["GIT_SSH_COMMAND"] == "ssh -i /tmp/key -o BatchMode=yes"


def test_default_runner_reraises_missing_non_git_binary(monkeypatch):
    def missing_binary(*args, **kwargs):
        raise FileNotFoundError("custom")

    monkeypatch.setattr(source_module.subprocess, "run", missing_binary)

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


def test_ensure_source_repos_uses_partial_sparse_for_each_configured_repo(tmp_path: Path):
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


def test_source_utility_helpers_cover_error_details_and_path_matching(tmp_path: Path):
    assert source_module._github_contents_endpoint(GitHubRepoRef("Org", "Repo"), "") == "/repos/Org/Repo/contents"
    assert source_module._github_contents_endpoint(GitHubRepoRef("Org Space", "Repo"), "skills/a b/SKILL.md") == "/repos/Org%20Space/Repo/contents/skills/a%20b/SKILL.md"

    response = GitHubHttpResponse(403, b'{"message": "API rate limit exceeded"}', {"X-RateLimit-Remaining": "0"})
    assert "Forbidden" in source_module._github_https_error_detail(response)
    rate_hint = source_module._github_https_failure_hint(response.status, response.headers, "rate limit")
    auth_hint = source_module._github_https_failure_hint(401, {}, "authentication required")
    not_found_hint = source_module._github_https_failure_hint(404, {}, "not found")
    assert rate_hint is not None and "API limits" in rate_hint
    assert auth_hint is not None and "private repos" in auth_hint
    assert not_found_hint is not None and "private repo" in not_found_hint
    assert source_module._github_https_failure_hint(500, {}, "server error") is None
    assert source_module._github_https_headers({"GH_TOKEN": "  token  "})["Authorization"] == "Bearer token"
    assert source_module._github_https_headers({"GITHUB_TOKEN": "fallback"})["Authorization"] == "Bearer fallback"
    assert source_module._github_https_error_message(b"not json") == "not json"
    assert source_module._github_https_error_message(b"\xff") == ""
    assert source_module._http_status_phrase(599) == ""

    assert source_module._github_item_name({"name": "alpha\n"}, "op") == "alpha\\x0a"
    assert source_module._github_item_path({"path": "skills/alpha"}, "op") == "skills/alpha"
    assert source_module._github_item_type({"type": "dir"}) == "dir"
    assert source_module._github_item_type({"type": 1}) is None
    with pytest.raises(SourceBackendError, match="missing a name"):
        source_module._github_item_name([], "op")
    with pytest.raises(SourceBackendError, match="missing a path"):
        source_module._github_item_path({"path": 1}, "op")
    with pytest.raises(SourceBackendError, match="unsafe path components"):
        source_module._github_item_path({"path": "../secret"}, "op")

    assert source_module._is_direct_child_path("team/skills/alpha", "team/skills")
    assert not source_module._is_direct_child_path("team/skills/nested/alpha", "team/skills")
    assert source_module._is_path_inside("team/skills/alpha/SKILL.md", "team/skills/alpha")
    assert not source_module._is_path_inside("team/skills", "team/skills")
    assert source_module._looks_like_not_found("HTTP 404")
    gh_rate_hint = source_module._gh_failure_hint("rate limit")
    gh_not_found_hint = source_module._gh_failure_hint("not found")
    gh_auth_hint = source_module._gh_failure_hint("HTTP 401")
    assert gh_rate_hint is not None and "api limits" in gh_rate_hint.lower()
    assert gh_not_found_hint is not None and "private" in gh_not_found_hint.lower()
    assert gh_auth_hint is not None and "auth" in gh_auth_hint.lower()
    assert source_module._gh_failure_hint("server exploded") is None

    assert source_module._is_default_candidate_skill_file("skills/alpha/SKILL.md")
    assert source_module._is_default_candidate_skill_file("team/skills/alpha/SKILL.md")
    assert not source_module._is_default_candidate_skill_file("skills/.hidden/SKILL.md")
    assert source_module._is_direct_child_skill_file("custom/alpha/SKILL.md", "custom")
    assert not source_module._is_direct_child_skill_file("custom/.hidden/SKILL.md", "custom")
    assert source_module._same_source_repo("https://github.com/Org/Repo.git", "Org/Repo")
    assert not source_module._same_source_repo("::bad::", "Org/Repo")


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
        source_module._normalize_sparse_checkout_patterns([pattern])


def test_sparse_pattern_helpers_dedupe_and_normalize_configured_roots():
    assert source_module._sparse_file_pattern(".sv/index.toml") == "/.sv/index.toml"
    assert source_module._sparse_folder_pattern("skills/alpha") == "/skills/alpha/**"
    patterns = source_module._metadata_sparse_patterns(["custom", "custom"])
    assert patterns.count("/custom/*/SKILL.md") == 1
    assert source_module._normalize_sparse_checkout_patterns(["/skills/*/SKILL.md", "/skills/*/SKILL.md"]) == ["/skills/*/SKILL.md"]


def test_remove_backend_cache_folder_removes_files_directories_and_rejects_symlink(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    file_path = repo / "skills" / "alpha.txt"
    file_path.parent.mkdir()
    file_path.write_text("alpha\n")
    source_module._remove_backend_cache_folder(repo, "skills/alpha.txt")
    assert not file_path.exists()

    dir_path = repo / "skills" / "beta"
    dir_path.mkdir()
    (dir_path / "SKILL.md").write_text("beta\n")
    source_module._remove_backend_cache_folder(repo, "skills/beta")
    assert not dir_path.exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    link = repo / "skills" / "linked"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SvError, match="must not be a symlink"):
        source_module._remove_backend_cache_folder(repo, "skills/linked")


def test_run_git_reports_missing_git_os_errors_empty_failures_and_success(tmp_path: Path):
    def missing(_args, _cwd):
        raise FileNotFoundError("git")

    with pytest.raises(SvError, match="Git is required"):
        source_module._run_git(["status"], tmp_path, missing, "Checking git")

    def os_error(_args, _cwd):
        raise OSError("bad\nerror")

    with pytest.raises(SvError, match=r"bad\\x0aerror"):
        source_module._run_git(["status"], tmp_path, os_error, "Checking git")

    with pytest.raises(SvError, match="exit code 2"):
        source_module._run_git(["status"], tmp_path, lambda args, cwd: completed(args, 2), "Checking git")

    assert source_module._run_git(["status"], tmp_path, lambda args, cwd: completed(args, 0, " ok\n"), "Checking git") == "ok"
