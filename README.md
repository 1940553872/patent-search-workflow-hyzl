# 专利检索工作流

按照本项目[检索筛选设计稿](<Agent 中国专利检索筛选工作流-软件设计（D4 工程方向）.docx>)开发，采用“智慧芽网页检索、用户导入表格与 PDF、Codex 取证判断、本地程序校验与导出”的方式。原始 Word 设计稿只读保留。

## 启动

Windows 双击 **`启动工作流.cmd`**，浏览器打开 [本地工作台](http://127.0.0.1:8765)。当前电脑可直接启动，无需另行安装；启动脚本优先使用 Codex 随附的 Python。使用期间保持启动窗口运行，关闭窗口即停止服务。

系统不需要专利数据库 API 或额外的模型 API 密钥。模型判断由当前 Codex 聊天读取任务包后完成；本地程序负责保存、校验和调度，不在后台自动启动模型。

## 日常操作：四步

1. **建立任务。** 在工作台填写检索需求、意图和日期，记录任务编号。新颖性用于技术特征对照；全景用于收集领域文献；侵权风险入口提供待核实候选；按号查询只取文献。
2. **让 Codex 处理当前任务包。** 可以直接说：“继续处理任务 R-… 的当前阶段。”Codex 按任务包加载对应 Skill，整理规格、生成检索提示词或执行取证；规格完成后由用户确认。五个 Skill 随项目存放，无需全局安装。
3. **在智慧芽检索并提供材料。** 复制每条提示词到智慧芽 Agent，导出 CSV、XLSX 或 JSON，在工作台按查询编号导入并登记完成、零命中、截断或失败。把所需专利 PDF 放入工作台显示的 `data/runs/<任务编号>/pdf_inbox/`，让 Codex 核对公开号、版本、页数后继续取证；您也可在界面登记核对。需要补检或补证时重复此步。
4. **独立复核并导出。** 当前 Codex 聊天安排独立子智能体复核；复核上下文必须与取证上下文不同。用户抽检、签认后导出报告。新颖性结果分为匹配、排除、待核实；证据不足保留待核实。

PDF 优先以完整公开号命名，例如 `CN123456789A.pdf`，A、B 文本分别提供。取证需要可检索文字及原文段落号或权利要求号；扫描件需先通过 OCR（把扫描图转成可检索文字）处理并核对，首版不自动调用 OCR 服务。错版本、缺页、只有摘要时不能确认匹配。

每个任务的结果保存在 `data/runs/<任务编号>/`：`report.html` 是报告，`evidence.json` 是结构化结果，`evidence_matrix.csv` 是特征证据表，`trace.json` 是操作记录。中断后重新启动并选择原任务继续。

## 模块分工

| 组件 | 职责 |
| --- | --- |
| `cnps-spec` / `cnps-plan` | 理解需求与条件；扩展术语并生成智慧芽提示词 |
| `retrieve` / `normalize` / `filter` | 导入原始数据；规范号码、去重；执行硬条件的三值判断 |
| `cnps-match` / `cnps-gap` | 从全文逐字取证；决定补检、补证或停止 |
| `cnps-review` | 在独立上下文中复核证据和初次判断 |
| 调度器 / `export` | 管理阶段、预算、恢复与记录；生成报告并核对数量 |

三值判断是“满足、不满足、未知”；缺字段属于未知。Skill 之间通过程序交接，不互相调用。字段、输入输出和限制详见[接口与职责说明](patent-agent/docs/contracts.md)。

## 命令行与验证

以下命令均在项目根目录执行；日常操作可直接使用工作台。

```powershell
python run.py serve --open
python run.py show <任务编号>
python run.py packet <任务编号>
python run.py advance <任务编号>
python run.py test
```

迁移到其他电脑时需要 Python 3.11 或以上，以及 `requirements.txt` 中的 `pypdf`、`openpyxl`；使用自己的 Python 环境可运行 `python -m pip install -r requirements.txt`。若当前终端没有配置 `python` 命令，日常使用双击启动脚本即可。

固定样例和自动测试用于验证程序规则与交接；真实检索效果仍需用户提供智慧芽导出文件和专利 PDF 后验收。当前不声称达到匹配精确率或查全率目标。代码目前在本地交付，未发布至 GitHub。

供其他工作流调用时，使用 `python run.py search --request <请求JSON>` 或 `lookup --request <请求JSON>`，均返回统一的 `evidence.json` 数据结构；用 `python run.py evidence <任务编号>` 读取同一任务最新进度。收到 `PENDING_IMPORT` 表示等待人工导入，应继续原任务。

正式人工抽检后，可用 `python run.py evaluate <任务编号> --labels <人工标注JSON>` 计算设计稿的五项指标，保存到任务目录的 `acceptance.json`；缺少人工标注时显示“未评测”。人工标注格式见[接口说明](patent-agent/docs/contracts.md)。
