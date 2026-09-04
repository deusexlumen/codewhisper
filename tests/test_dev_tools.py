import asyncio

import pytest

from dev_tools import ALLOWED, TOOL_NAME, build_tool_declaration, format_result, run_tool


# ---------- build_tool_declaration ----------

def test_build_tool_declaration_has_tool_name():
    decl = build_tool_declaration()
    assert decl["name"] == TOOL_NAME


def test_build_tool_declaration_enum_matches_allowlist():
    decl = build_tool_declaration()
    enum = decl["parameters"]["properties"]["command"]["enum"]
    assert set(enum) == set(ALLOWED.keys())


# ---------- format_result ----------

def test_format_result_success_includes_output():
    text = format_result("git_status", 0, "nothing to commit")
    assert "erfolgreich" in text
    assert "nothing to commit" in text
    assert "gekürzt" not in text


def test_format_result_nonzero_exit_mentions_exit_code():
    text = format_result("pytest", 1, "1 failed, 2 passed")
    assert "1" in text
    assert "1 failed, 2 passed" in text


def test_format_result_empty_output_no_trailing_junk():
    text = format_result("git_status", 0, "")
    assert text.strip().endswith("erfolgreich beendet.")


def test_format_result_truncates_long_output():
    long_output = "x" * 5000
    text = format_result("git_diff", 0, long_output, max_chars=100)
    assert "gekürzt" in text
    assert text.count("x") == 100


# ---------- run_tool ----------

class _FakeRunner:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    async def __call__(self, argv, cwd, timeout):
        self.calls.append((argv, cwd, timeout))
        if self.exc:
            raise self.exc
        return self.result


@pytest.mark.asyncio
async def test_run_tool_rejects_unknown_command_without_running_anything():
    runner = _FakeRunner(result=(0, "sollte nie passieren"))
    result = await run_tool("rm_-rf_alles", runner=runner)
    assert "Unbekanntes Kommando" in result
    assert runner.calls == []


@pytest.mark.asyncio
async def test_run_tool_returns_formatted_success():
    runner = _FakeRunner(result=(0, "On branch main"))
    result = await run_tool("git_status", runner=runner)
    assert "On branch main" in result
    assert runner.calls[0][0] == ALLOWED["git_status"]


@pytest.mark.asyncio
async def test_run_tool_surfaces_nonzero_exit_not_none():
    runner = _FakeRunner(result=(1, "1 failed"))
    result = await run_tool("pytest", runner=runner)
    assert result is not None
    assert "1 failed" in result


@pytest.mark.asyncio
async def test_run_tool_handles_timeout():
    runner = _FakeRunner(exc=asyncio.TimeoutError())
    result = await run_tool("pytest", runner=runner, timeout=5)
    assert "Zeitlimit" in result


@pytest.mark.asyncio
async def test_run_tool_handles_execution_error():
    runner = _FakeRunner(exc=FileNotFoundError("pytest nicht gefunden"))
    result = await run_tool("pytest", runner=runner)
    assert "konnte nicht ausgeführt werden" in result
