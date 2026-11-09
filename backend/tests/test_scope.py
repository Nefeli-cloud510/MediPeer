from __future__ import annotations

from backend.app.services.scope_service import compute_replay_scope


BASE_MESSAGES = [
    {
        "id": "q",
        "role": "patient",
        "speaker_id": "p",
        "text": "咳嗽三天。",
        "created_at": "2026-10-01T08:00:00+00:00",
    },
    {
        "id": "a-prior",
        "role": "doctor",
        "speaker_id": "d1",
        "text": "多喝水。",
        "created_at": "2026-10-01T08:30:00+00:00",
    },
    {
        "id": "p-earlier",
        "role": "patient",
        "speaker_id": "p",
        "text": "补充：昨天发热。",
        "created_at": "2026-10-01T08:45:00+00:00",
    },
    {
        "id": "focus",
        "role": "doctor",
        "speaker_id": "d2",
        "text": "观察体温。",
        "created_at": "2026-10-01T09:00:00+00:00",
    },
    {
        "id": "same-time",
        "role": "doctor",
        "speaker_id": "d3",
        "text": "同样建议观察。",
        "created_at": "2026-10-01T09:00:00+00:00",
    },
    {
        "id": "p-later",
        "role": "patient",
        "speaker_id": "p",
        "text": "补充：今天退了。",
        "created_at": "2026-10-01T09:10:00+00:00",
    },
    {
        "id": "future",
        "role": "doctor",
        "speaker_id": "d4",
        "text": "按上呼吸道处理。",
        "created_at": "2026-10-01T09:30:00+00:00",
    },
]


def test_strict_prior_scope():
    scope = compute_replay_scope("case-1", BASE_MESSAGES, "focus")
    assert [m.id for m in scope.visible_messages] == ["q", "a-prior", "p-earlier"]
    assert scope.prior_answer_message_ids == ["a-prior"]
    assert scope.patient_message_ids == ["q", "p-earlier"]
    assert set(scope.excluded_message_ids) == {
        "focus",
        "same-time",
        "p-later",
        "future",
    }
    assert scope.order_quality == "ok"


def test_unparseable_time_is_excluded_not_guessed():
    messages = [
        dict(item) for item in BASE_MESSAGES
    ]
    messages.append(
        {
            "id": "bad-time",
            "role": "doctor",
            "speaker_id": "d5",
            "text": "时间不明确。",
            "created_at": "not-a-date",
        }
    )
    scope = compute_replay_scope("case-1", messages, "focus")
    assert "bad-time" in scope.excluded_message_ids
    assert "不可解析" in scope.exclusion_reasons["bad-time"]
    assert scope.order_quality == "partial"


def test_unparseable_focus_time_yields_empty_scope():
    messages = [dict(item) for item in BASE_MESSAGES]
    messages[3]["created_at"] = None
    scope = compute_replay_scope("case-1", messages, "focus")
    assert scope.visible_messages == []
    assert scope.order_quality == "unknown"
    assert len(scope.excluded_message_ids) == len(messages)


def test_current_scope_only_adds_manual_supplement_not_original_later_patient():
    messages = [dict(item) for item in BASE_MESSAGES]
    messages.append(
        {
            "id": "p-manual",
            "role": "patient",
            "speaker_id": "p",
            "text": "补充：我今天早上发过烧，现在没量。",
            "created_at": "2026-10-01T09:20:00+00:00",
            "order_basis": "manual_supplement",
        }
    )
    scope = compute_replay_scope("case-1", messages, "focus", "current")
    patient_ids = scope.patient_message_ids
    assert "p-manual" in patient_ids
    assert "p-later" not in patient_ids
    visible_ids = [message.id for message in scope.visible_messages]
    assert "future" not in visible_ids
    assert "p-manual" in visible_ids
