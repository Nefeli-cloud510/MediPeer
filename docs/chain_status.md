# 微医数据接入 MediPeer 任务链

目标链路：原始数据 → 规范化咨询 → 严格前序上下文 → 待审草稿 → 人工参照 → 系统审阅 → 对照评测 → 演示案例。

| 步骤 | 内容 | 状态 | 最新产物 |
| --- | --- | --- | --- |
| t0 | 定位输入文件，建立只读数据清单 | 已登记真实文件，待 t1 schema 复核 | `data_app_chain/runs/t0_20261105T020821Z/source_manifest.json` |
| t1 | 真实字段映射与 schema 报告 | 已完成：62,206 行、7,000 个独立问题、7,143 位医生；10 项字段映射 verified，answer_event_proxy_key 缺失；无精确重复行，但存在 2 组 (question_id, seq) 冲突，需 question_id+seq+user_id 复合键；182 行同时间戳回答分布 163 个问题，严格前序必须排除；时间范围 2017-09-02 至 2021-10-07，question_desc/answer 无空值 | `data_app_chain/runs/t1_20261105T143947Z/schema_report.json`、`field_mapping.csv`、`audit_summary.md` |
| t2 | 记录单位与关联关系审计 | 已完成：62,206 行 / 7,000 问题 / 7,143 医生；复合键 question_id+seq+user_id 全表唯一；question_id+seq 有 2 组 4 行冲突；0 条回答早于提问；同时间戳 182 组 367 行 163 问题；4,114 题文件内行数少于 ans_count（抽样子集）；seq 与时间序存在 22,627 个严格冲突对；175 位医生元数据不一致；answer_event_proxy_key 缺失，当前用复合键映射并保留回填限制 | `data_app_chain/runs/t2_20261105T144738Z/relationship_report.json`、`alignment_conflict_sample.csv`、`audit_summary.md` |
| t3 | 规范化消息表 | 已完成：生成 `normalized_messages.parquet`，69,206 条消息（patient 7,000 / doctor 62,206），7,000 个 consult；message_id 与 source_record_id 全唯一；doctor 回源不匹配 0，原文或时间不一致 0；needs_review 371（367 同时间戳 + 4 复合键冲突）；文本原样保留，未做医学语义清洗 | `data_app_chain/runs/t3_20261105T145622Z/normalized_messages.parquet`、`normalization_report.json`、`normalization_exceptions.csv` |
| t4 | 脱敏与脱敏后证据定位 | 已完成：69,206 条消息规则化脱敏（unchanged 69,120 / changed 86 / needs_review 0），规则 t4-deid-v3；替换 id_card=1、id_label=1、mobile=10、email=4、door=6、name=17、phone_label=10、wechat=1、address=5、landline=3、hotline=16、url=19；临床标记词受损 0，脱敏后仍匹配原始 PII 模式 0；受限映射 identity_mapping_restricted.csv | `data_app_chain/runs/t4_20261105T152301Z/deidentified_messages.parquet`、`deidentification_report.json` |
| t5 | 首版场景筛选与病例索引 | 已完成：7,000 咨询全量索引；有患者文本/有医生回答各 7,000，≥2 位医生 6,254；呼吸道候选 1,371，全部因年龄未知需人工复核；呼吸道候选且结构可联合审阅 1,247；排除均有原因（非呼吸道 5,629、医生<2 71、顺序未知 53），无 consult-qid 不匹配 | `data_app_chain/runs/t5_20261105T152844Z/case_index.parquet`、`case_scope.csv`、`case_index_report.json` |
| t6 | 严格前序 replay manifest | 未开始 | - |
| t7 | 待审草稿任务 | 未开始 | - |
| t8 | 开发/测试/演示集合划分 | 未开始 | - |
| t9 | 标注规则与首个完整病例 | 未开始 | - |
| t10 | 双人独立标注与分歧裁定 | 未开始 | - |
| t11 | 病例加载与可见范围控制 | 已完成：后端案例复制/隔离/回放接口、前端三栏工作台；10 项测试通过，typecheck 与生产构建通过，HTTP 联调完成（7 条消息、2 条前序、4 条排除，Vite 代理访问后端成功） | `backend/app/services/scope_service.py`、`backend/tests/`、`frontend/src/components/Workbench.tsx` |
| t12 | 患者事实整理 | 已完成：后端 schema/服务/API、前端事实面板自动分析；16 项后端测试通过，typecheck 与生产构建通过；HTTP 联调校验四类判定（未提及=unknown、医生提问=asked_unanswered、明确否认=denied、昨天/今天双时间状态） | `backend/app/services/patient_facts_service.py`、`backend/tests/test_patient_facts.py`、`frontend/src/components/Workbench.tsx` |
| t13 | 同行观点整理与比较 | 已完成：后端 schema/服务/API（仅取严格前序回答、脱敏字段、句级切分，区分观点/澄清提问/适用条件，输出草稿关系与同行关系）、前端中栏“同行观点整理（焦点前）”卡片与右栏“患者依据 / 观点贡献 · 同行比较”双组标签；26 项后端测试通过，typecheck 与生产构建通过；HTTP 联调返回 3 条同行关系（conditional_diff），确认来源仅 wy-a-2001/wy-a-2002，且焦点/同时刻/后续/患者补充均未泄漏 | `backend/app/services/doctor_opinions_service.py`、`backend/tests/test_doctor_opinions.py`、`frontend/src/components/Workbench.tsx` |
| t14 | 同一主张的联合审阅 | 已完成：后端 `/api/cases/{case_id}/joint-review` 将患者事实、前序医生观点、同行关系与草稿句写入同一 AnalysisRun（stage=joint_review），逐句输出“观点贡献 + 患者依据 + 建议动作 + 证据引用 + 签名”的联合审阅结果；前端右栏顶部新增“同一主张联合审阅”卡片、中栏草稿按句渲染并支持定位，逐条证据引用可定位原文；32 项后端测试通过，typecheck 与生产构建通过；HTTP 联调返回 4 条草稿句/4 条联合审阅结果，证据仅来自 wy-q-1001/wy-a-2001/wy-a-2002，焦点与后续材料未泄漏 | `backend/app/services/joint_review_service.py`、`backend/tests/test_joint_review.py`、`frontend/src/components/Workbench.tsx` |
| t15 | 人工处理与信息更新 | 已完成：后端确认/驳回/采用补问、人工补录（新建 CaseVersion）、改稿（新建 DraftVersion）、新旧 run diff（新增/已解决/保持不变）；前端 finding 卡片确认/驳回/采用补问、补录区、可编辑草稿与重新分析、右栏“与上一审阅的提示变化”；39 项后端测试通过，typecheck 与生产构建通过；HTTP 冒烟（确认 action、补录后 version=2、改稿后 draft version=2，重跑 diff=1 added + 4 unchanged）通过 | `backend/app/services/review_action_service.py`、`backend/tests/test_review_actions.py`、`frontend/src/components/Workbench.tsx` |
| t16 | 同模型直接审阅基线 | 已完成：新增 `/api/cases/{case_id}/baseline-review`，与 joint-review 共用同一病例版本、焦点、草稿版本和严格前序可见消息，直接审阅跳过患者事实/同行观点结构化模块；输出同构 findings；保存实际请求、模型标识、提示词版本、生成配置、输出、耗时与失败状态（ModelCall + 失败 Run 不清除）；程序校验引用 ID、逐字片段、角色和范围；超过 max_input_chars 直接失败不截断；46 项后端测试通过，HTTP 冒烟返回 4 句/4 条基线审阅；`stub` 仅作为无凭据测试桩，真实模型路径已由主后端 8000 与 t17/t20 运行验证 | `backend/app/services/baseline_review_service.py`、`backend/app/services/llm_client.py`、`backend/tests/test_baseline_review.py` |
| t17 | 完整方案配对评测 | 已完成：新增 EvaluationRun 服务与配对评测 CLI，同一病例版本/焦点/草稿版本/严格前序上下文下同时运行 joint-review 与 baseline-review；隔离评测库不写应用库；真实模型 `qwen3.8-flash` 两路均 succeeded，`binding.match=true`，共同 case/draft 版本与 scope 已核对；逐主张结果与证据引用已完成，失败与不适用保留；无人工标签不生成准确率 | `data_app_chain/runs/t17_eval_a5978b7e/evaluation_summary.json`、`evaluation_detail.csv`、`failure_cases.md` |
| t20 | 真实模型同病例三轮审阅验收 | 已完成：主后端 8000 冒烟成功（qwen3.8-flash，`33bcc82a36fe4d6c8d49a23d62508e50`），8001 验收端完成初次审阅/补录复核/改稿复核三轮真实运行；三轮证据校验 all pass，语义检查 all pass；“目前无发热”在补录后改为 `detail_unverified`，改稿后转为有逐字患者引用；JSON/Markdown 导出可读；失败运行保留且不静默切换 stub | `data_app_chain/runs/t20_real_model_20261107T144859Z_c24aec/`（run ids `7a084c0e0a4e48a9bff9d272148e0acc`、`df855cabb51f4c9281a37d45a305fb23`、`b8a7a89fa0cf40beb49b6ad48edc3eef`）、`data_app_chain/runs/t20_main_backend_smoke_20261107T151400Z/smoke_summary.json` |
| t18 | 三个可复现演示案例 | 未开始（用户暂时不优先） | - |
| t19 | 参赛数据证据包 | 未开始（用户暂时不优先） | - |

说明：

- 不把研究分析的派生结果当作原始业务输入。
- 原始文件保持只读；派生输出写入 `data_app_chain/runs/<run_id>/`。
- t17 及后续评测 CLI 默认写入 `data_app_chain/eval_db/eval_*.db`（隔离评测库），不写入正在运行的应用数据库。
- 每一步必须提供真实产物与验收方式；没有数据时写“等待上传”，不虚构统计。

