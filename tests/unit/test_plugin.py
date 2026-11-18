import pytest


def test_heal_flag_is_registered(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest("--help")
    result.stdout.fnmatch_lines(["*--heal=*off*cache*heuristic*llm*full*"])


def test_full_heal_mode_is_accepted(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest("--heal=full")
    assert result.ret == pytest.ExitCode.NO_TESTS_COLLECTED


def test_verdict_thresholds_are_configurable(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest("--help")
    result.stdout.fnmatch_lines(
        ["*healorhalt_verdict_heal_threshold*", "*healorhalt_verdict_floor*"]
    )


def test_unknown_heal_mode_is_rejected(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest("--heal=magic")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
