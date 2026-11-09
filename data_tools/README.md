# MediPeer 数据链路脚本

当前任务优先完成“微医 → 真实脱敏病例 → 严格前序 → 待审草稿 → 人工参照 → 系统审阅 → 对照评测”链路。

执行目录约定：

- `raw_input/`：原始上传区，文件只读。
- `data_app_chain/runs/<run_id>/`：每个阶段独立运行输出，旧运行不覆盖。
- `data_app_chain/current_run.json`：最新一次运行指针，不代表历史输出被覆盖。

阶段脚本：

```powershell
& <python> data_tools/pipeline/t0_source_manifest.py
```

t0 生成 `source_manifest.json`。原始文件上传到 `raw_input/` 后重跑即可更新指纹。

t1 生成真实 schema 与字段映射审计：

```powershell
& <python> data_tools/pipeline/t1_schema_audit.py
```

t2 生成记录单位与关联关系审计（问题线程、时间/排序一致性、医生关联、稳定 ID 映射）：

```powershell
& <python> data_tools/pipeline/t2_relationship_audit.py
```

t3 生成独立规范化消息表 `normalized_messages.parquet`（患者/医生统一消息、同题并入
同一 consult、needs_review 标记、原文与时间回源校验）：

```powershell
& <python> data_tools/pipeline/t3_normalize_messages.py
```

t4 对规范化消息做规则化脱敏并保留脱敏后证据定位（受限映射写入同运行目录）：

```powershell
& <python> data_tools/pipeline/t4_deidentify_messages.py
```

t5 生成呼吸道首版病例索引与范围 CSV（科室/标签/关键词初筛，年龄未知不做成人默认）：

```powershell
& <python> data_tools/pipeline/t5_case_index.py
```

