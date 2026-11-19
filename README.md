# 出院沟通中的用药变化与随访事项

本地文本工作区用于保存用药陈述时态与状态相关的合成记录，并提供基础规范化和词项查看能力。工作区只核对已经记录的决定，不替任何人作决定。

## 环境

需要 Python 3.11 或更新版本。运行程序仅使用 Python 标准库。测试依赖固定为 pytest 8.3.4，可执行 `python -m pip install -r requirements-dev.txt` 安装。

## 运行

运行 `PYTHONPATH=src python -m nlp_clinical_event_reconciliation.desk_cli` 核对签署资料可用于哪些沟通，并查看应补发通知的预约。该工具比对资料里的已登记文字和签署身份。所有样例为合成资料。

从项目根目录执行 `PYTHONPATH=src python -m nlp_clinical_event_reconciliation.cli ingest fixtures/records.json` 导入样例，随后执行 `PYTHONPATH=src python -m nlp_clinical_event_reconciliation.cli list` 查看记录。工作数据写入 `var/records.json`。业务查询使用 fixtures/business.json 中的初始资料，约定见 fixtures/business-contract.md。执行 `PYTHONPATH=src python -m nlp_clinical_event_reconciliation.cli business` 可查看当前业务目录结果。

样例为本地合成文本。记录包含来源、修订号和原文，当前工作区对空白做统一处理，并提供字符及英文词项切分。

## 交接核对

`desk_cli` 读取的每个事项会区分三类文字：

- **转述**：患者以为、入院前清单、院前记录等既述内容，只保留原句，不进入患者指令。
- **拟议**：讨论、考虑、计划、草稿、待确认等尚未确认的文字，同样不会发出。
- **已确认**：登记簿中相应岗位签署的文书，例如医师签署的出院沟通或门诊协调员签署的预约回执。

只有已确认内容才能成为当前沟通依据。签署语句若写的是“继续此前已经确认的安排”，工具会在同一事项内查找它承接的已确认版本和原句；无法确定承接哪一版或哪一个事项时，只把该事项标记为 `需要核实`，交由协调员核对，其余事项照常给出状态。

预约回执只更新对应复诊的提醒，不会改变用药沟通，也不会替另一项尚未确认的复诊作决定。原计划编号和改期编号同时出现时会归到同一事项；重复到达的同样确认会计数但不会再次发出通知。

每项输出包含：

| 字段 | 含义 |
| --- | --- |
| `subject` / `matter` | 事项名称与归并后的编号 |
| `latest` | 当前可发出的已确认内容，等待核实时为 `null` |
| `earlier` / `sent` | 已经发给患者的提醒原文，补发后仍可查 |
| `action` | `等待签署依据`、`制作交接`、`需要补充通知`、`已通知` 或 `需要核实` |
| `change_summary` | 旧提醒到当前依据的实质变化，例如“时间由 6 月 8 日 改为 6 月 10 日” |
| `needs_review` / `review_reason` | 是否只把该事项交给协调员核实及原因 |
| `resolution` | 承接语句找到的已确认记录及匹配方式 |
| `held` / `held_sources` | 未采用的转述、拟议或越权来源及原因 |
| `materials` | 每个事项的原句、签署、来源和当前沟通状态 |

晚录入的入院历史仍会出现在 `held` 中供查起因，但不会因录入更晚覆盖签署安排。

## 测试

执行 `python -m pytest -q` 检查规范化、记录持久化、来源筛选和交接核对边界。
