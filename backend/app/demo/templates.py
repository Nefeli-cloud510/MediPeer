from __future__ import annotations

import copy
from typing import Any


# 虚构的成人呼吸道咨询演示病例。时间均为 UTC 字符串，用于验证严格前序，
# 不来自真实患者或医生数据。
DEMO_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "demo_fever_sputum_three_rounds",
        "title": "验收病例：有痰与发热的医生回复审阅",
        "department": "呼吸内科",
        "platform": "weiyi",
        "validation_scope": "respiratory_adult_zh",
        "seed_draft_text": "患者昨天开始有痰，目前无发热。",
        "messages": [
            {
                "id": "wy-fq-q-1001",
                "role": "patient",
                "speaker_id": "patient-1001",
                "speaker_name": "患者",
                "text": "前天开始鼻塞，昨天开始有痰。",
                "created_at": "2026-11-02T08:00:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "wy-fq-a-2001",
                "role": "doctor",
                "speaker_id": "wy-doctor-001",
                "speaker_name": "周医生",
                "speaker_title": "副主任医师",
                "hospital": "市第一医院",
                "text": "这两天量过体温吗？",
                "created_at": "2026-11-02T08:10:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "wy-fq-a-2002",
                "role": "doctor",
                "speaker_id": "wy-doctor-002",
                "speaker_name": "李医生",
                "speaker_title": "主任医师",
                "hospital": "区中心医院",
                "text": "患者昨天开始有痰，可先多喝水观察。",
                "created_at": "2026-11-02T08:20:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "wy-fq-a-2003-focus",
                "role": "doctor",
                "speaker_id": "wy-doctor-003",
                "speaker_name": "张医生",
                "speaker_title": "主治医师",
                "hospital": "市第二医院",
                "text": "结合描述先观察；如果测到发热，记录体温和时间。",
                "created_at": "2026-11-02T09:00:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "wy-fq-a-2004-same-time",
                "role": "doctor",
                "speaker_id": "wy-doctor-004",
                "speaker_name": "赵医生",
                "speaker_title": "主治医师",
                "hospital": "社区医院",
                "text": "建议多喝水，如感不适及时就诊。",
                "created_at": "2026-11-02T09:00:00+00:00",
                "order_basis": "source_timestamp",
            },
        ],
    },
    {
        "key": "demo_respiratory_multi_doctor",
        "title": "演示病例：成人咳嗽伴发热的多医生答复",
        "department": "呼吸内科",
        "platform": "weiyi",
        "validation_scope": "respiratory_adult_zh",
        "seed_draft_text": (
            "患者目前咳嗽伴发热，建议继续观察体温。"
            "患者昨日发热 38.5℃，现已退热；"
            "既往无哮喘病史。建议多喝水，如持续发热可考虑复查血常规。"
        ),
        "messages": [
            {
                "id": "wy-q-1001",
                "role": "patient",
                "speaker_id": "patient-1001",
                "speaker_name": "患者",
                "text": (
                    "医生您好，我最近三天一直咳嗽，晚上比较明显，有少量白痰。"
                    "今天早上开始觉得有点发热，没量过体温。平时没有其他基础病。"
                ),
                "created_at": "2026-10-01T08:00:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "wy-a-2001",
                "role": "doctor",
                "speaker_id": "wy-doctor-001",
                "speaker_name": "王医生",
                "speaker_title": "副主任医师",
                "hospital": "市第一医院",
                "text": "多喝水，注意休息，观察咳嗽和体温变化，症状加重及时就诊。",
                "created_at": "2026-10-01T08:30:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "wy-a-2002",
                "role": "doctor",
                "speaker_id": "wy-doctor-002",
                "speaker_name": "李医生",
                "speaker_title": "主任医师",
                "hospital": "区中心医院",
                "text": (
                    "请问这几天有没有测过体温？是否发热、有无胸闷气短？"
                    "如果体温超过 38.5℃ 且持续不退，需要就诊。"
                ),
                "created_at": "2026-10-01T08:45:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "wy-a-2003-focus",
                "role": "doctor",
                "speaker_id": "wy-doctor-003",
                "speaker_name": "张医生",
                "speaker_title": "主治医师",
                "hospital": "市第二医院",
                "text": (
                    "如果没有气促、胸痛，可以先在家观察；"
                    "测得明确发热时记录体温和时间，必要时采血查感染指标。"
                ),
                "created_at": "2026-10-01T09:00:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "wy-a-2004-same-time",
                "role": "doctor",
                "speaker_id": "wy-doctor-004",
                "speaker_name": "赵医生",
                "speaker_title": "主治医师",
                "hospital": "社区医院",
                "text": "咳嗽三天伴白痰，注意休息即可，有发热再对症处理。",
                "created_at": "2026-10-01T09:00:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "wy-p-1002",
                "role": "patient",
                "speaker_id": "patient-1001",
                "speaker_name": "患者",
                "text": (
                    "补充：昨晚八点量过体温 38.5℃，今天早上没有再烧。"
                    "没有胸闷气短，也没有胸痛。"
                ),
                "created_at": "2026-10-01T09:10:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "wy-a-2005-future",
                "role": "doctor",
                "speaker_id": "wy-doctor-003",
                "speaker_name": "张医生",
                "speaker_title": "主治医师",
                "hospital": "市第二医院",
                "text": "患者已补充发热和退热情况，可暂时按上呼吸道感染继续观察。",
                "created_at": "2026-10-01T09:30:00+00:00",
                "order_basis": "source_timestamp",
            },
        ],
    },
    {
        "key": "demo_respiratory_single_doctor",
        "title": "演示病例：单一医生答复与信息补录",
        "department": "呼吸内科",
        "platform": "weiyi",
        "validation_scope": "respiratory_adult_zh",
        "seed_draft_text": "患者目前无发热，建议暂不使用抗生素。",
        "messages": [
            {
                "id": "sq-q-2001",
                "role": "patient",
                "speaker_id": "patient-2001",
                "speaker_name": "患者",
                "text": "嗓子疼两天，偶尔咳嗽，没有发烧的感觉但没量体温。",
                "created_at": "2026-10-02T10:00:00+00:00",
                "order_basis": "source_timestamp",
            },
            {
                "id": "sa-a-2001",
                "role": "doctor",
                "speaker_id": "wy-doctor-010",
                "speaker_name": "周医生",
                "speaker_title": "副主任医师",
                "hospital": "市第一医院",
                "text": "先观察两天，多喝水；如果出现高热或咽痛加重，再到医院检查。",
                "created_at": "2026-10-02T10:30:00+00:00",
                "order_basis": "source_timestamp",
            },
        ],
    },
]


def list_demo_templates() -> list[dict[str, Any]]:
    return copy.deepcopy(DEMO_TEMPLATES)


def get_demo_template(key: str) -> dict[str, Any]:
    for template in list_demo_templates():
        if template["key"] == key:
            return template
    raise KeyError(f"unknown demo template: {key}")
