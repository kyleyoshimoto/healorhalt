import pytest


def test_heal_flag_is_registered(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest("--help")
    result.stdout.fnmatch_lines(["*--heal=*off*cache*heuristic*llm*full*"])


def test_unimplemented_heal_mode_is_rejected(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest("--heal=full")
    result.stderr.fnmatch_lines(["*--heal=full isn't implemented yet*"])
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_unknown_heal_mode_is_rejected(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest("--heal=magic")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
