# 专利检索工作流

按照本地保存的《Agent 中国专利检索筛选工作流-软件设计（D4 工程方向）》开发，采用“智慧芽网页检索、用户导入表格与 PDF、Codex 取证判断、本地程序校验与导出”的方式。原始 Word 设计稿只读保留，不随代码上传。

## 启动

Windows 双击 **`启动工作流.cmd`**，浏览器打开 [本地工作台](http://127.0.0.1:8765)。当前电脑可直接启动，无需另行安装；启动脚本优先使用 Codex 随附的 Python。使用期间保持启动窗口运行，关闭窗口即停止服务。

系统不需要专利数据库 API 或额外的模型 API 密钥。模型判断由当前 Codex 聊天读取任务包后完成；本地程序负责保存、校验和调度，不在后台自动启动模型。

## 日常操作：四步

1. **建立任务。** 在工作台填写检索需求、意图和日期，记录任务编号。新颖性用于技术特征对照；全景用于收集领域文献；侵权风险入口提供待核实候选；按号查询只取文献。
2. **让 Codex 处理当前任务包。** 可以直接说：“继续处理任务 R-… 的当前阶段。”Codex 按任务包加载对应 Skill，整理规格、生成具体检索式或执行取证；规格完成后由用户确认。五个 Skill 随项目存放，无需全局安装。
3. **在智慧芽检索并提供材料。** 在智慧芽左侧选择“专家搜索”，复制本任务的具体检索式并执行，导出 CSV、XLSX 或 JSON。在工作台按查询编号登记实际检索式、检索日期、页面结果总数及完成、零命中、截断或失败；导出数量由程序读取文件统计。把所需专利 PDF 放入工作台显示的 `data/runs/<任务编号>/pdf_inbox/`，让 Codex 核对公开号、版本、页数后继续取证；您也可在界面登记核对。需要补检或补证时重复此步。
4. **独立复核并导出。** 当前 Codex 聊天安排独立子智能体复核；复核上下文必须与取证上下文不同。用户抽检、签认后导出报告。新颖性结果分为匹配、排除、待核实；证据不足保留待核实。

PDF 优先以完整公开号命名，例如 `CN123456789A.pdf`，A、B 文本分别提供。取证需要可检索文字及原文段落号或权利要求号；扫描件需先通过 OCR（把扫描图转成可检索文字）处理并核对，首版不自动调用 OCR 服务。错版本、缺页、只有摘要时不能确认匹配。

每个任务的结果保存在 `data/runs/<任务编号>/`：`report.html` 是报告，`evidence.json` 是结构化结果，`evidence_matrix.csv` 是特征证据表，`trace.json` 是操作记录。中断后重新启动并选择原任务继续。

普通检索计划保存在任务目录的 `search_queries.md`，每个代码块仅包含可复制检索式。默认使用 `TACD_ALL`（标题、摘要、权利要求、说明书及机器翻译数据）；新颖性采集先保留日期和状态范围，导入后按基准日判断。平台实际检索速度和命中数须以真实查询为准。

已有任务改用普通检索时，可在“条件与记录”选择“重编计划”，或运行 `python run.py replan <任务编号> --reason "改用普通专家检索" --by "用户本次指示"`。程序保留已执行查询和原始材料，把未完成的旧查询标为已替代，并让 Codex 生成新编号；不复用旧编号改变检索内容。`prompts` 命令保留为 `queries` 的兼容入口。

## 模块分工

| 组件 | 职责 |
| --- | --- |
| `cnps-spec` / `cnps-plan` | 理解需求与条件；扩展关键词并生成智慧芽字段检索式 |
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
python run.py queries <任务编号>
python run.py test
```

迁移到其他电脑时需要 Python 3.11 或以上，以及 `requirements.txt` 中的 `pypdf`、`openpyxl`；使用自己的 Python 环境可运行 `python -m pip install -r requirements.txt`。若当前终端没有配置 `python` 命令，日常使用双击启动脚本即可。

固定样例和自动测试用于验证程序规则与交接；真实检索效果仍需用户提供智慧芽导出文件和专利 PDF 后验收。当前不声称达到匹配精确率或查全率目标。本仓库发布程序、Skill、配置和使用说明；任务数据和原始材料保留在各自电脑。

供其他工作流调用时，使用 `python run.py search --request <请求JSON>` 或 `lookup --request <请求JSON>`，均返回统一的 `evidence.json` 数据结构；用 `python run.py evidence <任务编号>` 读取同一任务最新进度。收到 `PENDING_IMPORT` 表示等待人工导入，应继续原任务。

正式人工抽检后，可用 `python run.py evaluate <任务编号> --labels <人工标注JSON>` 计算设计稿的五项指标，保存到任务目录的 `acceptance.json`；缺少人工标注时显示“未评测”。人工标注格式见[接口说明](patent-agent/docs/contracts.md)。

## GitHub 同步与多人协作

仓库地址：[patent-search-workflow-hyzl](https://github.com/1940553872/patent-search-workflow-hyzl)。首次使用可通过 GitHub Desktop 克隆仓库到自己的电脑，再按上文安装依赖并启动。修改代码需要仓库写入权限，或先 Fork（在自己的 GitHub 账号下建立仓库副本）再提交合并申请。

本地保存不会自动上传。每次开始修改前，先在 GitHub Desktop 点击 `Fetch origin`，有更新时点击 `Pull origin`。完成一次修改后，在 Changes 中核对并勾选文件，填写修改说明，点击 `Commit to <分支名>` 记录这一版，再点击 `Push origin` 上传。若提示冲突，先核对并处理冲突，不强制推送。

多人编辑时，各自在工作分支修改，通过 Pull Request（合并申请）提交到 `main`，经审阅后合并。其他成员拉取更新后继续使用。GitHub Actions 会对代码提交和合并申请运行项目验收测试。

仅同步程序、Skill、配置和使用说明。`data/`、Python 缓存、本地运行环境及项目根目录的 Word 设计稿已由 `.gitignore` 排除；专利 PDF、未申请方案和任务结果放在 `data/` 内。GitHub 同步代码，不同步各人的任务数据库。
