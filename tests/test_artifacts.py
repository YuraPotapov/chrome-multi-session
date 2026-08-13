"""Unit tests for configurable reporting (no browser needed).

Covers :class:`ReportConfig` parsing/validation and its per-outcome artifact
decisions, plus :class:`Reporter` writing the right files against a fake adapter
and a temp directory. The scenarios mirror the examples in the feature spec.
"""

import os

import pytest

from domain.result import FlowResult
from engine.artifacts import ARTIFACTS, ReportConfig, Reporter


class FakeAdapter:
    """Records screenshots and serves canned page state for the diagnostics."""

    def __init__(self):
        self.shots = []

    def screenshot(self, path):
        self.shots.append(os.path.basename(path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("PNG")

    def content(self):
        return "<html></html>"

    def console_logs(self):
        return ["one", "two"]

    def url(self):
        return "http://localhost:8069/web"


def _files(tmp_path):
    return set(os.listdir(tmp_path))


def _flow():
    return FlowResult(scenario="s", session="sess")


# --- ReportConfig: parsing & validation -------------------------------------
def test_default_config_is_legacy():
    cfg = ReportConfig()
    assert cfg.configured is False
    assert cfg.effective_level == frozenset(ARTIFACTS)
    assert cfg.screen_modes == frozenset()          # legacy never uses the modes


def test_from_cli_marks_configured_for_each_flag():
    assert ReportConfig.from_cli(level="result").configured is True
    assert ReportConfig.from_cli(always=True).configured is True
    assert ReportConfig.from_cli(screen="start").configured is True


def test_unknown_artifact_raises():
    with pytest.raises(ValueError) as exc:
        ReportConfig.from_cli(level="result,bogus")
    assert "bogus" in str(exc.value)
    assert "report-level artifact" in str(exc.value)


def test_unknown_screen_mode_message_matches_spec():
    with pytest.raises(ValueError) as exc:
        ReportConfig.from_cli(level="screen", screen="start,middle")
    msg = str(exc.value)
    assert "Unknown report-screen mode 'middle'." in msg
    assert "Supported values: start, each, finish." in msg


def test_duplicate_modes_are_deduped():
    cfg = ReportConfig.from_cli(level="screen", screen="start,start,finish")
    assert cfg.screen == frozenset({"start", "finish"})


def test_screen_modes_default_to_finish_when_enabled():
    cfg = ReportConfig.from_cli(level="console,dom,result,screen,url")
    assert cfg.screen_modes == frozenset({"finish"})


def test_screen_ignored_when_not_in_level():
    cfg = ReportConfig.from_cli(level="result", screen="start,each,finish")
    assert cfg.screen_enabled is False
    assert cfg.screen_modes == frozenset()


# --- ReportConfig: which non-screenshot artifacts per outcome ---------------
def test_outcome_artifacts_default():
    cfg = ReportConfig()
    assert cfg.outcome_artifacts(failed=False) == {"result"}
    assert cfg.outcome_artifacts(failed=True) == {"console", "dom", "result", "url"}


def test_outcome_artifacts_level_filters_both_outcomes():
    cfg = ReportConfig.from_cli(level="result")
    assert cfg.outcome_artifacts(failed=False) == {"result"}
    assert cfg.outcome_artifacts(failed=True) == {"result"}


def test_outcome_artifacts_always_promotes_success():
    cfg = ReportConfig.from_cli(level="console,result", always=True)
    assert cfg.outcome_artifacts(failed=False) == {"console", "result"}


# --- Reporter: legacy behaviour (backward compatibility) --------------------
def test_legacy_success_writes_only_result(tmp_path):
    r = Reporter(ReportConfig(), str(tmp_path), FakeAdapter())
    r.capture_start()
    r.capture_step()
    r.finalize(_flow(), failed=False)
    assert _files(tmp_path) == {"result.json"}


def test_legacy_failure_writes_full_bundle(tmp_path):
    r = Reporter(ReportConfig(), str(tmp_path), FakeAdapter())
    r.finalize(_flow(), failed=True)
    assert _files(tmp_path) == {"console.log", "dom.html", "result.json",
                                "screenshot.png", "url.txt"}


def test_legacy_compile_error_writes_result_only(tmp_path):
    r = Reporter(ReportConfig(), str(tmp_path), FakeAdapter())
    r.finalize_compile_error(_flow())
    assert _files(tmp_path) == {"result.json"}


# --- Reporter: configured behaviour (spec examples) -------------------------
def test_level_result_only_even_on_failure(tmp_path):
    r = Reporter(ReportConfig.from_cli(level="result"), str(tmp_path), FakeAdapter())
    r.capture_start()
    r.capture_step()
    r.finalize(_flow(), failed=True)
    assert _files(tmp_path) == {"result.json"}


def test_screen_each_numbers_screenshots(tmp_path):
    # Spec example 1: --report-level=screen --report-screen=each
    r = Reporter(ReportConfig.from_cli(level="screen", screen="each"),
                 str(tmp_path), FakeAdapter())
    r.capture_start()                       # start not requested -> nothing
    r.capture_step()
    r.capture_step()
    r.capture_step()
    r.finalize(_flow(), failed=False)       # finish not requested, result not in level
    assert _files(tmp_path) == {"screenshot_001.png", "screenshot_002.png",
                                "screenshot_003.png"}


def test_complete_report_on_failure_uses_finish_name(tmp_path):
    # Spec "Complete report": all artifacts, default screen point = finish
    r = Reporter(ReportConfig.from_cli(level="console,dom,result,screen,url"),
                 str(tmp_path), FakeAdapter())
    r.capture_start()
    r.finalize(_flow(), failed=True)
    assert _files(tmp_path) == {"console.log", "dom.html", "result.json",
                                "screenshot_finish.png", "url.txt"}


def test_always_writes_console_and_result_on_success(tmp_path):
    # Spec --report-always example: --report-always --report-level=console,result
    r = Reporter(ReportConfig.from_cli(level="console,result", always=True),
                 str(tmp_path), FakeAdapter())
    r.finalize(_flow(), failed=False)
    assert _files(tmp_path) == {"console.log", "result.json"}


def test_start_and_finish_with_result(tmp_path):
    # Spec example 2 (success side): result,screen + always + start,finish
    cfg = ReportConfig.from_cli(level="result,screen", always=True, screen="start,finish")
    r = Reporter(cfg, str(tmp_path), FakeAdapter())
    r.capture_start()
    r.capture_step()                        # each not requested -> nothing
    r.finalize(_flow(), failed=False)
    assert _files(tmp_path) == {"result.json", "screenshot_start.png",
                                "screenshot_finish.png"}


def test_screen_requested_but_not_in_level_captures_nothing(tmp_path):
    # Spec example 3: --report-level=result --report-screen=start,each,finish
    cfg = ReportConfig.from_cli(level="result", screen="start,each,finish")
    r = Reporter(cfg, str(tmp_path), FakeAdapter())
    r.capture_start()
    r.capture_step()
    r.finalize(_flow(), failed=False)
    assert _files(tmp_path) == {"result.json"}


def test_configured_compile_error_respects_level(tmp_path):
    r = Reporter(ReportConfig.from_cli(level="console"), str(tmp_path), FakeAdapter())
    r.finalize_compile_error(_flow())       # result not in level -> nothing written
    assert _files(tmp_path) == set()
