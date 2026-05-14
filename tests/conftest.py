import pytest

from sv.cli import SkillSelector, select_skills
from tests.helpers import SvResult, run_sv as execute_sv


@pytest.fixture
def run_sv(capsys):
    def _run_sv(
        args,
        *,
        cwd,
        home,
        git_runner=None,
        process_runner=None,
        skill_selector: SkillSelector = select_skills,
        skill_chooser=None,
    ) -> SvResult:
        return execute_sv(
            args,
            cwd=cwd,
            home=home,
            capsys=capsys,
            git_runner=git_runner,
            process_runner=process_runner,
            skill_selector=skill_selector,
            skill_chooser=skill_chooser,
        )

    return _run_sv
