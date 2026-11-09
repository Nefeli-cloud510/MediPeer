# raw_input 上传区

本目录是 MediPeer 数据链路的原始数据上传区。文件夹刻意保持为空，等待上传真实文件后再运行
`data_tools/pipeline/t0_source_manifest.py` 生成 `source_manifest.json`。

请上传以下文件（文件名可不同，t0 清单会按实际文件名登记）：

| 平台 | 期望文件 | 用途 | 是否必需 |
| --- | --- | --- | --- |
| 微医 | `answer.parquet` | 医生回答：正文、医生ID、职称、科室、医院、回答时间、seq | 必需 |
| 微医 | `question.parquet` | 患者问题：问题ID、性别、年龄、地址、问题正文、标签、提问时间 | 必需 |
| 微医 | `wy_hospital.parquet` | 医院等级/医院信息 | 可选 |
| 好大夫 | 六列中间表：`consult_id, physician_id, doctor_key, table_text, consult_text, needs_review` | 表单与医患对话 | 可选（本项目微医链路先跑通） |

当前已收到并在用：

| 平台 | 文件 | 状态 | 指纹 |
| --- | --- | --- | --- |
| 微医 | `major5_stratified_question_samples_answers.parquet` | 已登记，t1-t5 已完成（schema、关联关系、规范化消息表、脱敏、病例索引） | `sha256=310fae75...d68e1c` |

约束：

- 原始文件保持只读，任何脱敏、规范化、筛选、评测输出都写入 `data_app_chain/runs/<run_id>/`。
- 不要在本目录手工改名或压缩解压后覆盖原始文件；上传后记录文件指纹。
- `data_app_chain/runs/` 已加入 `.gitignore`，历史运行输出不会被新运行覆盖。

