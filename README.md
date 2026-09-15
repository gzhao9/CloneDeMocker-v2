# CloneDeMocker v2

本分支把论文中的检测和重构流程实现为一个本地 Agent UI。它先建立 **Detection Scope**，再执行 **Mock Logic Extraction → Frequent Stub Set Mining → Mock Clone Instance Formation**。用户可以按目录、Java 文件和 package 建立检测池，并在扫描后逐个取消不希望参与检测的 Mock Object。

This branch exposes the paper workflow as a local agent UI. Users define a **Detection Scope**, inspect and filter extracted Mock Objects, form MCIs only from the selected objects, and generate an isolated refactoring proposal with a unified diff.

## 启动 / Run

环境要求：Java 17、Maven、Python 3.11+ 和 `uv`。Windows 用 PowerShell：

```powershell
.\start-ui.ps1
```

macOS/Linux 用 bash：

```bash
./start-ui.sh
```

两个脚本做的事完全一样（设置 `UV_CACHE_DIR`、`uv run python -m app.server`），检测、重构、验证这几个模块本身（`app/`、`DETECTION/`、`validation/`）已经按操作系统分支处理了 `mvn`/`mvn.cmd`、`java`/`java.exe`、`gradlew`/`gradlew.bat`，不需要额外适配就能跨平台跑。

然后访问 `http://127.0.0.1:8765`。第一次扫描会在需要时构建 `DETECTION` 的可执行 JAR。默认不解析外部依赖，工具仍会读取 Java 源码并用受限语法降级识别 Mockito；勾选“解析项目依赖”后，Maven/Gradle classpath 会通过临时文件读取，不会修改被检测项目的构建文件。

自动重构默认使用 `gpt-5.6-terra`。单 key 可设置 `OPENAI_API_KEY`；多 key 可设置 JSON 映射并在 UI 输入 profile 名：

```powershell
uv pip install -r app/requirements.txt
$env:CLONEDEMOCKER_OPENAI_KEYS='{"default":"sk-...","research":"sk-..."}'
```

Agent 按 **Encapsulation → Integration → Harness Validation** 执行。候选源码、`changes.diff`、模型 token 用量和机器验证证据写入 `.clonedemocker/runs/<run-id>/refactoring/`。它在隔离副本中执行 compile、test 和可选 PIT；编译或测试失败时，会将诊断交回模型，最多自动修复两次。生成阶段不会覆盖原项目源码。

调试链路时可勾选“调试模式”（API 传 `useMock: true`），此时不会调用真实模型、不消耗 token，只原样回填源码走完 diff/harness 全流程，方便反复验证 UI 和链路而不产生费用。

一个 MCI 内部也可以只挑子集 sequence 重构（例如 9 条只选 7 条），在对应 MCI 卡片展开“sequence”列表取消勾选即可；API 对应 `sequenceSelection: { "<mciId>": [mockObjectId, ...] }`，未列出的 MCI 视为全选。注意：被排除的 sequence 所在文件如果和被选中的 sequence 共享同一个源文件，模型仍会看到整份文件内容（用于保持上下文连贯），因此不能保证该文件里被排除部分完全不受影响，只是不会作为重构目标出现在 prompt 的 `selectedMockCloneInstances` 里。

## 论文术语与代码 / Paper terminology mapping

| 论文术语 / Paper term | v2 代码 |
|---|---|
| Detection Scope | `DetectionScope`, `DetectionService.scan` |
| Mock Logic Extraction | `MockInfoExporter`, `MockAnalyzer` |
| Frequent Stub Set Mining | `AprioriMiner`, `MockCloneMiner.FrequentStubSet` |
| Mock Clone Instance Formation | `MockCloneMiner.formMockCloneInstances` |
| Encapsulation / Integration | `RefactoringAgent` prompt and state |
| Harness Validation | `ProjectHarness`, `HarnessEvidence` |

重要实现处同时保留中文和英文注释，便于维护和论文复核。

## 旧版本 artifact / Legacy artifact

v2 是从原始 CloneDeMocker 仓库复制并重写的，`REFACTORING/`、`DATA/`（旧的手工 prompt 流水线、论文原始实验数据、消融实验 prompt 模板等）不在这个分支里保留副本——它们原样存在于同级的 `../CloneDeMocker` 目录（v2 复制自那里，内容未改动），需要查旧数据或旧 prompt 模板时去那边找。

v2 was copied and rewritten from the original CloneDeMocker repository; `REFACTORING/` and `DATA/` (the old manual prompt pipeline, the paper's original experiment data, the ablation prompt templates, etc.) are not duplicated in this branch — they live unmodified in the sibling `../CloneDeMocker` directory that v2 was copied from.

## 重构通过率验证 / Refactoring pass-rate validation

`validation/` 是独立于 `app/` 的验证模块，用真实项目按论文 RQ2.1 的三层标准（编译通过 / 测试结果一致 / PIT 分数不降）跑重构通过率，细节见 `validation/README.md`。
