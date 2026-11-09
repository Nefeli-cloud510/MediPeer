# MediPeer 医知协同

面向“接续答复在线咨询问题的医生”的回复审阅工作台。系统对同一条草稿主张同时判断：

- 相对严格前序同行回答，草稿新增了什么；
- 草稿关于患者的事实是否有患者原文依据；
- 潜在分歧是否来自时间或适用条件差异；
- 补入患者回复或修改草稿后，哪些提示变化、哪些保持。

首版验证范围：中文成人呼吸道咨询；其他科室文本目前只做通用整理，不作为验证范围。

## 当前能力

- 案例库：复制演示模板成为当前会话独立病例，会话间互不可见。
- 三栏审阅工作台：左栏患者信息与原文，中栏已有观点与草稿，右栏“观点贡献 + 患者依据”联合审阅提示。
- 点击提示定位草稿句；点击证据引用定位原文。
- 确认、驳回、采用补问、补录患者回复、修改草稿、重新分析。
- 历史分析与新旧结果对比；刷新后恢复状态。
- JSON / Markdown 审阅记录导出，导出遵守当前会话访问范围。
- 统一 LLM 适配器：`stub` 测试桩与阿里云百炼兼容模式（`openai_compatible`），密钥只从后端 `.env` 读取。

## 本地运行

1. 复制 `.env.example` 为 `.env`，填入真实模型配置（示例见 `backend/app/core/config.py`）。
2. 启动后端（`D:\Medipeer` 根目录）：

```powershell
python -X utf8 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

3. 启动前端：

```powershell
cd frontend
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`，登录账号 `demo`（评审账号见 `.env.example` 中 `MEDIPEEPER_DEMO_PASSWORD` / `MEDIPEEPER_JUDGE_PASSWORD`）。

## 验证

```powershell
python -X utf8 -m pytest backend/tests -q
cd frontend
node node_modules/typescript/bin/tsc --noEmit
```

当前后端测试为 63 项，前端类型检查无错误。

## 真实模型运行记录

- t17 配对评测：`data_app_chain/runs/t17_eval_a5978b7e/`，joint 与 baseline 均为 `qwen3.8-flash`，绑定校验 `binding.match=true`。
- t20 同病例三轮验收：`data_app_chain/runs/t20_real_model_20261107T144859Z_c24aec/`，三轮证据校验与语义检查全部通过，含可读 JSON/Markdown 导出。
- 主后端 8000 冒烟：`data_app_chain/runs/t20_main_backend_smoke_20261107T151400Z/smoke_summary.json`。

没有人工冻结标签时，不生成准确率、召回率、F1 或效率提升比例；模型调用失败保留现场，不静默切换到 `stub`。

## 任务链与评审操作

- 微医数据接入任务链：`docs/chain_status.md`
- 评委三条可复现操作：`docs/judge_operations.md`

