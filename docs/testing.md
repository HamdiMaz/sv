# Testing sv

Use these commands from the repository root. They match the local release gate and the GitHub Actions workflow so failures are reproducible before opening a PR or publishing.

## Full release verification

Run the same checks used by CI:

```bash
uv run ruff check .
uv run ty check src tests
uv run pytest --cov=sv --cov-report=term-missing
rm -rf dist
uv build
tmp_venv="$(mktemp -d)"
trap 'rm -rf "$tmp_venv"' EXIT
python -m venv "$tmp_venv"
wheel_path="$(python -c 'from pathlib import Path; wheels = sorted(Path("dist").glob("maz_sv-*.whl")); assert len(wheels) == 1, wheels; print(wheels[0])')"
expected_version="$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
uv pip install --python "$tmp_venv/bin/python" --link-mode=copy --no-index "$wheel_path"
"$tmp_venv/bin/sv" --help
EXPECTED_SV_VERSION="$expected_version" "$tmp_venv/bin/python" -c 'import os, sv; assert sv.__version__ == os.environ["EXPECTED_SV_VERSION"], sv.__version__'
```

`pytest` is configured in `pyproject.toml` to collect coverage for `sv`, show missing lines in the terminal report, and enforce the current coverage threshold.

## Fast local test loops

For a quick unit-test pass while editing, skip the slower marker groups and disable coverage for the focused run:

```bash
uv run pytest -m "not integration and not security" -q --no-cov
```

For one file or one test, keep the same focused style:

```bash
uv run pytest tests/test_docs.py -q --no-cov
uv run pytest tests/test_docs.py::test_top_level_docs_files_have_headings -q --no-cov
```

When investigating ordering-sensitive failures, rerun the focused test with `SV_JOBS=1` to disable sv's internal worker pools:

```bash
SV_JOBS=1 uv run pytest tests/test_project.py -q --no-cov
```

Run the full coverage command before treating the change as ready.

## Integration tests

Integration tests are marked with `pytest.mark.integration` and use temporary local Git repositories. They do not require network access or real remote repositories.

```bash
uv run pytest -m integration -q --no-cov
```

## Security tests

Security tests are marked with `pytest.mark.security` and cover hostile paths, symlinks, terminal-control-character escaping, and related safety behavior.

```bash
uv run pytest -m security -q --no-cov
```

## Coverage reports

Use the terminal report for release checks:

```bash
uv run pytest --cov=sv --cov-report=term-missing
```

When you need an HTML report for local inspection, add an HTML output alongside the terminal report:

```bash
uv run pytest --cov=sv --cov-report=term-missing --cov-report=html
```

Open `htmlcov/index.html` after the command finishes. Do not commit generated coverage output.
