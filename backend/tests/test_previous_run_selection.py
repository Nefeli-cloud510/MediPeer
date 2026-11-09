from __future__ import annotations

from types import SimpleNamespace

from backend.app.services.joint_review_service import (
    _is_stub_run,
    _select_previous_run,
)


def _run(**overrides):
    values = {
        "source_mode": "stub_rules",
        "model_name": "medipeer-stub",
        "status": "succeeded",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_is_stub_run_flags_rule_based_runs():
    assert _is_stub_run(_run(source_mode="stub_rules", model_name="medipeer-stub"))
    assert _is_stub_run(_run(source_mode="stub_baseline", model_name="medipeer-stub"))
    assert not _is_stub_run(
        _run(source_mode="llm_joint", model_name="qwen3.8-flash")
    )


def test_select_previous_run_prefers_real_model_over_newer_stub():
    current = _run(source_mode="llm_joint", model_name="qwen3.8-flash")
    real_older = _run(
        source_mode="llm_joint",
        model_name="qwen3.8-flash",
        id="real-old",
    )
    stub_newer = _run(source_mode="stub_rules", id="stub-new")

    selected = _select_previous_run(current, [stub_newer, real_older])
    assert selected.id == "real-old"


def test_select_previous_run_keeps_latest_when_current_is_stub():
    current = _run()
    real_older = _run(source_mode="llm_joint", model_name="qwen3.8-flash", id="real")
    stub_newer = _run(source_mode="stub_rules", id="stub")

    assert _select_previous_run(current, [stub_newer, real_older]).id == "stub"


def test_select_previous_run_returns_none_without_candidates():
    current = _run(source_mode="llm_joint", model_name="qwen3.8-flash")
    assert _select_previous_run(current, []) is None


def test_select_previous_run_prefers_earlier_changed_draft():
    current = _run(
        source_mode="llm_joint",
        model_name="qwen3.8-flash",
        id="current",
        created_at="2026-11-08T10:00:00+00:00",
        case_version_id="cv2",
        draft_version_id="dv2",
    )
    same_draft = _run(
        source_mode="llm_joint",
        model_name="qwen3.8-flash",
        id="same-draft",
        created_at="2026-11-08T09:59:00+00:00",
        case_version_id="cv2",
        draft_version_id="dv2",
    )
    changed_draft = _run(
        source_mode="llm_joint",
        model_name="qwen3.8-flash",
        id="changed-draft",
        created_at="2026-11-08T09:30:00+00:00",
        case_version_id="cv2",
        draft_version_id="dv1",
    )

    selected = _select_previous_run(current, [same_draft, changed_draft])
    assert selected.id == "changed-draft"


def test_select_previous_run_falls_back_to_latest_same_input():
    current = _run(
        source_mode="llm_joint",
        model_name="qwen3.8-flash",
        id="current",
        created_at="2026-11-08T10:00:00+00:00",
        case_version_id="cv2",
        draft_version_id="dv2",
    )
    same_input = _run(
        source_mode="llm_joint",
        model_name="qwen3.8-flash",
        id="same-input",
        created_at="2026-11-08T09:59:00+00:00",
        case_version_id="cv2",
        draft_version_id="dv2",
    )

    assert _select_previous_run(current, [same_input]).id == "same-input"


def test_select_previous_run_ignores_future_runs():
    current = _run(
        source_mode="llm_joint",
        model_name="qwen3.8-flash",
        id="current",
        created_at="2026-11-08T10:00:00+00:00",
        case_version_id="cv2",
        draft_version_id="dv2",
    )
    future = _run(
        source_mode="llm_joint",
        model_name="qwen3.8-flash",
        id="future",
        created_at="2026-11-08T11:00:00+00:00",
        case_version_id="cv2",
        draft_version_id="dv2",
    )
    changed_draft = _run(
        source_mode="llm_joint",
        model_name="qwen3.8-flash",
        id="changed-draft",
        created_at="2026-11-08T09:30:00+00:00",
        case_version_id="cv2",
        draft_version_id="dv1",
    )

    selected = _select_previous_run(current, [future, changed_draft])
    assert selected.id == "changed-draft"
