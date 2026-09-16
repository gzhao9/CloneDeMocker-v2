# CloneDeMocker (v2) - 自动化单元测试 Mock 代码克隆检测与重构系统

<div align="right">
  <b>语言 / Language:</b> <a href="README.md"><b>English</b></a> | <b>简体中文</b>
</div>

[![Java 17](https://img.shields.io/badge/Java-17-orange.svg)](https://adoptium.net/)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org/)
[![License](https://img.shields.io/badge/License-Apache_2.0-green.svg)](LICENSE)

**CloneDeMocker** 是一套面向 Java 单元测试的 **Mock 逻辑克隆（Mock-Clone Instances, MCIs）检测与神经符号约束重构系统**。本工具旨在解决大型企业级微服务（如 Apache Dubbo 等）测试代码中泛滥的重复 Mockito 打桩逻辑，通过静态频繁项集挖掘与大模型（LLM）约束重构流水线，实现测试代码的自动化提炼、封装与三层完整性验证。

---

## 目录
- [1. 核心理念与技术架构](#1-核心理念与技术架构)
- [2. 论文术语与代码映射](#2-论文术语与代码映射)
- [3. 环境要求](#3-环境要求)
- [4. 快速启动（Web UI 交互式重构工作台）](#4-快速启动web-ui-交互式重构工作台)
- [5. 命令行与无头实验评估（Headless Pilot）](#5-命令行与无头实验评估headless-pilot)
- [6. 关键技术防御与 Windows 兼容性保障](#6-关键技术防御与-windows-兼容性保障)
- [7. 项目代码目录结构](#7-项目代码目录结构)

---

## 1. 核心理念与技术架构

CloneDeMocker 采用**“符号静态分析发现 + 神经大模型重构 + 分级确定性测试安全门禁”**的协同架构：

```
[Target Java Project]
        │
        ▼ (Stage 1: 符号静态分析 - JavaParser + Apriori)
  Detection Scope ──► Mock Logic Extraction ──► Frequent Stub Mining ──► MCI Formation
                                                                               │
        ┌──────────────────────────────────────────────────────────────────────┘
        ▼ (Stage 2: 神经符号约束重构 - LLM Agent)
  Scoped Prompting ──► Encapsulation & Integration ──► Unified Diff Generation
                                                              │
        ┌─────────────────────────────────────────────────────┘
        ▼ (Stage 3: 分级多维验证流水线 - Verification Pipeline)
  [Gate 1] 语法与补丁契约检查 ──► [Gate 2] 隔离编译 (Javac) ──► [Gate 3] 回归测试 ──► [Gate 4] PIT 变异测试
```

1. **阶段一：MCI 检测挖掘（Java/Maven）**
   - **Mock Logic Extraction**：基于 JavaParser AST 提取全部 `Mockito.when(...).thenReturn(...)` 及相关 mock 行为调用序列；
   - **Frequent Stub Set Mining**：利用 Apriori 频繁项集挖掘算法发现多处测试间高频共现的打桩模式；
   - **MCI Formation**：聚类形成 Mock Clone Instances（MCI），供用户审查与选择重构目标。
2. **阶段二：有界重构生成（Python LLM Agent）**
   - **最小补丁协议**：模型仅返回受影响测试类的最小 Unified Diff，严格禁止重写无关文件；
   - **结构瘦身**：去除了冗余嵌套源码传递，精准控制 Token 消耗；
   - **调试模式（Mock Mode）**：支持本地免 API 调用演练，不消耗任何 Token。
3. **阶段三：自动化隔离安全验证（Harness Validation）**
   - 在独立的隔离工作空间构建，不污染原始工程；
   - 执行三层严格门禁：`编译通过 (Compile)` ➜ `测试行为等价 (Test Equivalence)` ➜ `PIT 变异分数不下降 (Mutation Score Integrity)`；
   - 遇到编译或单测失败时，捕获编译器精准诊断信息反哺模型进行最多 2 轮针对性自我修复。

---

## 2. 论文术语与代码映射

为方便论文复现（Artifact Evaluation）与代码审查，下表列出了论文核心概念与代码实现类的直接对应关系：

| 论文术语 (Paper Term) | 核心算法 / 概念说明 | 代码对应实现 |
|---|---|---|
| **Detection Scope** | 被测工程扫描范围界定 | `DetectionScope`, `DetectionService.scan` |
| **Mock Logic Extraction** | 测试类中 Mock 行为抽象与提取 | `MockInfoExporter`, `MockAnalyzer` |
| **Frequent Stub Set Mining** | 高频打桩集合挖掘（Apriori 算法） | `AprioriMiner`, `MockCloneMiner.FrequentStubSet` |
| **Mock Clone Instance (MCI)** | 聚合形成的 Mock 克隆实例单元 | `MockCloneMiner.formMockCloneInstances` |
| **Encapsulation & Integration** | 提取辅助测试方法与内联替换 | `RefactoringAgent._model_input()`, Prompt 协议 |
| **Harness Validation** | 三层沙箱隔离验证门禁 | `ProjectHarness`, `ScopedProjectHarness`, `HarnessEvidence` |

---

## 3. 环境要求

- **JDK**: Eclipse Adoptium Temurin OpenJDK 17 或更高（必须配置 `JAVA_HOME`）；
- **构建工具**: Apache Maven 3.9+ 或 Gradle（推荐配置 `M2_HOME` / `MAVEN_HOME`）；
- **Python**: Python 3.11+ 并安装现代包管理器 `uv`；
- **操作系统**: Windows 10/11、macOS 或 Linux（已完成原生跨平台兼容处理）。

---

## 4. 快速启动（Web UI 交互式重构工作台）

### 4.1 启动服务

**Windows (PowerShell)**:
```powershell
.\start-ui.ps1
```

**macOS / Linux (Bash)**:
```bash
./start-ui.sh
```

启动后在浏览器访问：👉 **`http://127.0.0.1:8765`**

### 4.2 Web 工作台核心特性
1. **IDE 级别双模 Diff 审视器**：支持 **Unified（单栏）** 与 **Side-by-Side（双栏对照）** 自由切换，多文件标签页导航；
2. **细粒度人工决断管线**：提供明确的 `[✓ 采纳并写回 (Accept & Apply)]` 与 `[✗ 丢弃提案 (Discard)]`；
3. **环境与路径安全防御**：实时检测路径中文/空格风险，内置 `[🛡️ 环境安全诊断]` 模态框；
4. **全要素中英文双语**：右上角一键无缝切换中英文，配置自动持久化。

### 4.3 API Key 配置（可选）
- **调试模式（推荐初次体验）**：在 Web 界面勾选“调试模式（Debug Mode）”，系统使用本地规则引擎演练完整重构与验证流程，**完全不消耗任何 OpenAI Token**。
- **调用真实大模型**：
  ```powershell
  $env:OPENAI_API_KEY = "sk-..."
  ```
  或者设置多密钥配置并在界面切换 Profile：
  ```powershell
  $env:CLONEDEMOCKER_OPENAI_KEYS = '{"default":"sk-...","research":"sk-..."}'
  ```

---

## 5. 命令行与无头实验评估（Headless Pilot）

用于大规模学术实验与 RQ 复现的自动化评估流水线位于 `validation/` 目录下（与交互 UI 完全解耦）：

```powershell
# 运行自动化试点评测
uv run python -m validation.run_pilot \
  --project-dir "D:\Java_projects\Apache\dubbo-3.3.6" \
  --limit-mcis 5 \
  --use-mock
```

- **增量重构与作用域编译**：通过 `scoped_harness.py` 自动计算 MCI 关联的最小子模块（`-pl <module> -am`），杜绝百模块工程全量构建导致的开销激增；
- **变异测试门禁**：自动执行 PIT 变异测试（`mutationCoverage`），验证重构后的测试用例故障检出能力未遭降级；
- **失败隔离机制**：若某候选 MCI 未通过门禁，工作区将自动原子撤销改动，防止错误污染后续评估。

---

## 6. 关键技术防御与 Windows 兼容性保障

1. **严格换行符策略（Strict LF）**：
   所有代码写入操作强制遵循 `\n`（LF），杜绝在 Windows 平台因默认 CRLF 触发目标开源项目（如 Dubbo）的 Spotless 格式校验失败；
2. **纯 ASCII 路径隔离**：
   针对 Windows `sun.jnu.encoding=GBK` 与 `javac` 在中文路径下容易产生静默编译失败的问题，隔离工作区统一创建在目标项目同级目录的纯英文路径下；
3. **长路径保护（Windows MAX_PATH）**：
   底层文件 I/O 采用深层路径适配，彻底解决 Windows 260 字符限制。

---

## 7. 项目代码目录结构

```text
CloneDeMocker-v2/
├── app/                        # Python 后端服务与重构 Agent
│   ├── detection_service.py    # 调用 Java 检测器的调度服务
│   ├── harness.py              # 项目编译、单测与 PIT 验证沙箱
│   ├── model_provider.py       # 大模型适配层（真实 OpenAI API / 本地 Mock）
│   ├── refactoring_agent.py    # 重构 Agent 决策核心与 Diff 生成器
│   ├── server.py               # 轻量 REST API 服务器 (Starlette/Uvicorn)
│   └── web/                    # 现代化 IDE 级重构审视前端 (HTML/CSS/JS)
├── DETECTION/                  # Java/Maven 静态分析与克隆挖掘引擎
│   ├── pom.xml                 # 检测器 Maven 构建文件
│   └── src/                    # JavaParser AST 提取与 Apriori 频繁项集实现
├── validation/                 # 学术论文实验与大规模自动化验证模块
│   ├── run_pilot.py            # 批量实验执行与指标度量入口
│   ├── scoped_harness.py       # 模块级精简编译与 PIT 自动化驱动
│   └── reset_workspace.py      # 工作区安全复位工具
├── tests/                      # Python 自动化回归测试集
├── start-ui.ps1                # Windows 一键启动脚本
├── start-ui.sh                 # macOS/Linux 一键启动脚本
└── pyproject.toml              # Python 项目与 uv 依赖规范
```

---

## 许可证
本项目遵循 [Apache License 2.0](LICENSE) 开源协议。
