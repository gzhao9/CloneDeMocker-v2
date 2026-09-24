# CloneDeMocker 环境与数据检查清单

## 每台电脑需要下载什么

1. **Git 和 Git LFS**：克隆仓库后执行 `git lfs pull`。
2. **uv**：自动安装项目固定使用的 Python 3.11 和锁定依赖；不需要另行安装项目专用 Python。
3. **JDK 17**：`java` 和 `javac` 都必须可用，并正确设置 `JAVA_HOME`。
4. **Java 构建工具**：Maven 项目使用 Maven 3.9+ 或项目自己的 `mvnw`；Gradle 项目优先使用 `gradlew`。
5. **首次安装所需网络**：需要访问 uv/Python 包源以及 Maven Central 或目标项目配置的 Java 仓库。
6. **仅真实模型运行需要 OpenAI Key**：Debug/Mock 模式不需要 Key。

Windows 运行 `setup.cmd`，macOS/Linux 运行 `bash setup.sh`。安装后执行：

```powershell
.\check-env.cmd
```

针对实际项目检查 PIT 前置条件：

```powershell
.\check-env.cmd --project-root "D:\Java_projects\Apache\cloudstack-4.23.0.0" `
  --data-project cloudstack-4.23.0.0 `
  --require-pit
```

每项失败都会显示具体缺少的条件和修复命令；未要求执行的可选检查显示为 `WARN`。

## data/ 中必须保存什么

UI 现在会在每个 MCI 完成后增量保存。“报告存入 data/”按钮仍可用作幂等重试。

```text
data/<project>/
├── detection.json
├── detection-meta.json
└── refactoring/<setup>/
    ├── setup.json
    ├── refactoring-results.json
    ├── refactoring-results.csv
    ├── diffs/<mci>.diff
    └── pit/
        ├── <mci>.json
        ├── baselines/.../mutations.xml
        └── candidates/.../mutations.xml
```

UI 的 PIT 选项会把 PIT 摘要写入 `refactoring-results.json`。如需保留基线、候选方案的完整证据以及原始 `mutations.xml`，请对已经保存的 setup 执行 PIT 回放：

```powershell
uv run --locked --python 3.11 python validation/pit_replay.py pit cloudstack-4.23.0.0
```

确认 PIT 原始证据确实已经进入 `data/`：

```powershell
.\check-env.cmd --project-root "D:\Java_projects\Apache\cloudstack-4.23.0.0" `
  --data-project cloudstack-4.23.0.0 `
  --require-pit `
  --verify-pit-output
```

`validation/results/` 是临时运行产物，Git 会忽略它；`data/` 才是应审阅、提交并推送的规范数据集。
