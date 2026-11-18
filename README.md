# 出院沟通中的用药变化与随访事项

本地文本工作区用于保存用药陈述时态与状态相关的合成记录，并提供基础规范化和词项查看能力。

## 环境

需要 Python 3.11 或更新版本。运行程序仅使用 Python 标准库。测试依赖固定为 pytest 8.3.4，可执行 `python -m pip install -r requirements-dev.txt` 安装。

## 运行

运行 `PYTHONPATH=src python -m nlp_clinical_event_reconciliation.desk_cli` 核对签署资料可用于哪些沟通，并查看应补发通知的预约。该工具比对资料里的已登记文字和签署身份。所有样例为合成资料。

从项目根目录执行 `PYTHONPATH=src python -m nlp_clinical_event_reconciliation.cli ingest fixtures/records.json` 导入样例，随后执行 `PYTHONPATH=src python -m nlp_clinical_event_reconciliation.cli list` 查看记录。工作数据写入 `var/records.json`。业务查询使用 fixtures/business.json 中的初始资料，约定见 fixtures/business-contract.md。执行 PYTHONPATH=src python -m nlp_clinical_event_reconciliation.cli business 可查看当前业务目录结果。

样例为本地合成文本。记录包含来源、修订号和原文，当前工作区对空白做统一处理，并提供字符及英文词项切分。

## 测试

执行 `python -m pytest -q` 检查规范化、记录持久化和来源筛选。
