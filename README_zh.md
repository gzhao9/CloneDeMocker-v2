# CloneDeMocker v2

<div align="right"><a href="README.md">English</a> | <b>简体中文</b></div>

CloneDeMocker 是一个用于检测和重构 Java 单元测试中重复 Mock 逻辑的本地工具。本 README 先说明如何稳定安装和运行；项目设计与论文实验细节见文末链接。

## 第一次使用

### 1. 安装基础工具

| 工具 | 要求 | 用途 |
|---|---|---|
| Git | 当前稳定版 | 获取代码 |
| uv | 当前稳定版 | 安装固定的 Python 和依赖 |
| JDK | 17 | 运行检测器并验证 Java 项目 |
| Maven | 3.9+ | 构建 Maven 项目；有 Maven Wrapper 的目标项目可不全局安装 |
| Gradle | 目标项目要求的版本 | 仅处理 Gradle 项目时需要，优先使用目标项目的 Wrapper |

项目固定使用 Python 3.11。无需自行创建虚拟环境，也不要使用系统 Python 直接执行 `studio`；`uv` 会按照 `.python-version`、`pyproject.toml` 和 `uv.lock` 创建 `.venv` 并安装锁定依赖。

安装 uv：

```powershell
winget install --id=astral-sh.uv -e
```

macOS/Linux 请参考 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)。

### 2. 自动安装项目依赖

Windows 推荐运行 `.cmd`，它不会被常见的 PowerShell `ExecutionPolicy` 设置拦截：

```powershell
.\setup.cmd
```

macOS/Linux：

```bash
bash setup.sh
```

安装脚本会：

1. 使用项目内的 `.uv-cache` 和 `.uv-python`，避免多用户或受限目录的权限问题；
2. 获取并使用 Python 3.11；
3. 执行 `uv sync --locked`，严格按照 `uv.lock` 安装依赖；
4. 验证核心 Python 模块，并检查 Java 和 Maven。

## 启动 Web UI

Windows：

```powershell
.\start-ui.cmd
```

macOS/Linux：

```bash
./start-ui.sh
```

如果压缩包或某些文件系统丢失了脚本执行位，可改用：

```bash
bash start-ui.sh
```

浏览器打开：<http://127.0.0.1:8765>

启动脚本每次都会快速检查锁文件和环境；依赖已经同步时不会重复下载安装。

完整的下载清单和可执行检查方法见 [ENVIRONMENT_CHECKLIST_zh.md](ENVIRONMENT_CHECKLIST_zh.md)。安装后可直接运行：

```powershell
.\check-env.cmd
```

## 基本使用流程

1. 打开 Web UI，选择需要分析的 Java 项目根目录。
2. 运行环境检查，确认 JDK、构建工具和项目路径可用。
3. 扫描 Mock Clone Instances（MCI）。
4. 选择候选项并生成重构方案。
5. 查看统一或双栏 Diff。
6. 通过编译、测试与可选的 PIT 验证后，再决定采纳或丢弃。

每完成一个 MCI，结果都会增量保存到 `data/<project>/refactoring/<setup>/`；“报告存入
data/”按钮是可重复执行的补救入口。PIT 的完整原始证据也可以回放并保存到 `data/`。目录结构、
PIT 命令和验收命令见 [ENVIRONMENT_CHECKLIST_zh.md](ENVIRONMENT_CHECKLIST_zh.md)。

初次体验建议启用 UI 中的 Debug/Mock 模式，它不会调用外部大模型。使用真实模型时，在仓库根目录创建不会被 Git 提交的 `.env`：

```dotenv
OPENAI_API_KEY=your-key
```

启动脚本会读取当前仓库或其父目录中的 `.env`，但不会覆盖已经存在的环境变量。

## 依赖与版本规则

- `pyproject.toml`：直接依赖和 Python 版本范围的唯一来源。
- `uv.lock`：完整、可复现的传递依赖版本；应提交到 Git。
- `.python-version`：团队统一的 Python 3.11 选择。
- `studio/requirements.txt`：只为无法读取 `pyproject.toml` 的工具保留，不是推荐安装入口。

只使用 Web UI 不需要报告绘图依赖。需要运行 `validation/report_builders/` 时执行：

```powershell
.\setup.cmd -IncludeReports
```

macOS/Linux 使用 `bash setup.sh --reports`。

新增或升级依赖时：

```powershell
uv add <package>
uv lock
uv sync --locked
```

提交 `pyproject.toml` 和 `uv.lock`。不要只在个人虚拟环境里执行 `pip install`。

## 常见问题

### `uv.lock`、Python 版本或 `StrEnum` 错误

不要用系统 Python 启动。执行：

```powershell
.\setup.cmd
.\start-ui.cmd
```

如环境曾由其他 Python 创建，可删除本地 `.venv` 后重新运行 `setup.cmd`；`.venv` 不会被提交。

### PowerShell 提示脚本被禁止或未签名

使用：

```powershell
.\start-ui.cmd
```

`.cmd` 会只为当前进程使用 `ExecutionPolicy Bypass`，无需修改整台电脑的执行策略。

### `start-ui.sh: Permission denied`

优先执行：

```bash
bash start-ui.sh
```

从 Git 正常克隆时脚本应保留可执行位；若通过 ZIP/网盘传输，可执行 `chmod +x setup.sh start-ui.sh`。

### uv 缓存目录拒绝访问

使用本项目的启动/安装脚本。它们将 uv 缓存和 Python 安装目录固定在仓库内的 `.uv-cache` 与 `.uv-python`。

### UI 能打开，但扫描或验证 Java 项目失败

确认 `java -version` 为 JDK 17，并执行 `mvn -version` 检查 Maven 实际使用的 Java。Gradle 项目优先保留并使用项目自己的 `gradlew` / `gradlew.bat`。

## 命令行实验

无头批量验证示例：

```powershell
uv run --locked --python 3.11 python -m validation.run_pilot `
  --project-root "D:\Java_projects\Apache\dubbo-3.3.6" `
  --limit 2 `
  --use-mock
```

完整实验参数、PIT、工作区复位和报告说明见 [validation/README.md](validation/README.md)。

## 项目简介

CloneDeMocker 的流程分为三步：Java 静态检测器从测试代码中挖掘重复的 Mockito 打桩模式；Python Agent 生成受约束的最小补丁；验证 Harness 在隔离工作区中执行编译、回归测试和可选的 PIT 变异测试。Web UI 用于逐条审阅和采纳方案，`validation/` 用于批量实验。

主要目录：

| 路径 | 作用 |
|---|---|
| `studio/` | Python 后端、Web UI、重构 Agent 和验证 Harness |
| `DETECTION/` | Java/Maven 静态检测器 |
| `validation/` | 批量实验、分析与报告工具 |
| `tests/` | Python 回归测试 |
| `data/` | 可提交的规范化实验数据 |

更完整的架构和术语映射见 [AI_PROJECT_BRIEF.md](AI_PROJECT_BRIEF.md)。

## 验证与开发

```powershell
uv run --locked --python 3.11 python -m unittest discover -s tests -v
```

项目使用 Apache License 2.0。
