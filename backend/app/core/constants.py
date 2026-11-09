from __future__ import annotations

PRESET_SYMPTOMS = [
    "发热",
    "发烧",
    "咳嗽",
    "咳痰",
    "鼻塞",
    "流涕",
    "咽痛",
    "咽喉痛",
    "胸痛",
    "胸闷",
    "喘息",
    "气促",
    "头晕",
]

NEGATION_TOKENS = ["无", "没有", "未", "不伴有", "没有过", "从未"]

TIME_WORDS = ["昨天", "前天", "今天", "最近", "目前", "现在", "之前", "以前", "既往"]

VALIDATION_SCOPES = ["respiratory_adult_zh", "generic_text"]

FACT_DIMENSIONS = [
    "人口学信息",
    "诱因与情境",
    "临床时间信息",
    "症状特征",
    "信息连续性",
]

FACT_STATUSES = {
    "confirmed",
    "denied",
    "uncertain",
    "unknown",
    "asked_unanswered",
}

CLAIM_TYPES = {
    "patient_fact_statement",
    "possible_explanation",
    "recommendation",
    "clarifying_question",
    "general_knowledge",
}

RELATION_TYPES = {"repeat", "supplement", "divergence", "conditional_diff", "unresolved"}

SUGGESTED_ACTIONS = {
    "clarify_or_revise",
    "keep_and_confirm",
    "revise_fact_statement",
    "clarify_condition",
    "view_both_sources",
    "defer",
}

REVIEW_STATUSES = {"pending", "confirmed", "rejected", "revised", "supplemented"}

SUPPLEMENT_TYPES = {
    "patient_new_message",
    "doctor_recorded_note",
    "manual_correction",
    "demo_simulated",
}

TASK_KINDS = {"analysis", "evaluation"}
TASK_STATUSES = {"queued", "running", "succeeded", "failed", "interrupted", "cancelled"}

