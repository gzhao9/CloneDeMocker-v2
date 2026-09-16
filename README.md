# CloneDeMocker (v2): Automated Detection and LLM-Based Refactoring of Mock-Clone Instances in Unit Tests

<div align="right">
  <b>Language:</b> <b>English</b> | <a href="/daynell/CloneDeMocker-v2/src/branch/main/README_zh.md"><b>简体中文</b></a>
</div>

[![Java 17](https://img.shields.io/badge/Java-17-orange.svg)](https://adoptium.net/)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org/)
[![Conference Artifact](https://img.shields.io/badge/FSE_Artifact-Reproducible-brightgreen.svg)](https://conf.researchr.org/)
[![License](https://img.shields.io/badge/License-Apache_2.0-green.svg)](LICENSE)
---


## Table of Contents
- [1. Overview & Problem Definition](#1-overview--problem-definition)
- [2. System Architecture](#2-system-architecture)
- [3. Paper Terminology to Code Mapping](#3-paper-terminology-to-code-mapping)
- [4. Environment Prerequisites](#4-environment-prerequisites)
- [5. Quick Start: Web UI Refactoring Studio](#5-quick-start-web-ui-refactoring-studio)
- [6. Headless Experimentation & Paper Evaluation](#6-headless-experimentation--paper-evaluation)
- [7. Defensive Engineering & Platform Compatibility](#7-defensive-engineering--platform-compatibility)
- [8. Repository Layout](#8-repository-layout)
- [9. Citation & License](#9-citation--license)

---

## 1. Overview & Problem Definition

In unit testing for microservice architectures (e.g., Apache Dubbo), developers frequently set up external RPC dependencies using mock frameworks like Mockito. However, duplicated mock setup logic across unit tests leads to **Mock-Clone Instances (MCIs)**. When underlying interface contracts evolve, these scattered clones cause significant test maintenance overhead and high cognitive burden.

**CloneDeMocker** addresses this challenge via a bounded **Neuro-Symbolic** paradigm:
1. **Symbolic Static Mining**: Uses JavaParser AST analysis and the Apriori algorithm to discover frequent stubbing patterns and aggregate them into MCIs.
2. **Bounded Neural Refactoring**: Prompts an LLM with minimal context to encapsulate duplicated mocks into reusable test helper methods and inline callers, emitting isolated unified patches.
3. **Deterministic Multi-Tier Verification**: Validates candidate patches against compilation, test regression, and mutation testing (PIT) before committing changes back to the target project.

---

## 2. System Architecture

```
[Target Java Codebase]
        │
        ▼ (Stage 1: Symbolic Detection Pipeline - JavaParser + Apriori)
  Detection Scope ──► Mock Logic Extraction ──► Frequent Stub Mining ──► MCI Formation
                                                                               │
        ┌──────────────────────────────────────────────────────────────────────┘
        ▼ (Stage 2: Bounded Neural Refactoring - LLM Agent)
  Scoped Prompting ──► Encapsulation & Integration ──► Minimal Unified Patch
                                                              │
        ┌─────────────────────────────────────────────────────┘
        ▼ (Stage 3: Multi-Tier Verification Pipeline - Deterministic Gates)
  [Gate 1] Patch Policy Check ──► [Gate 2] Isolated Compile ──► [Gate 3] Test Equivalence ──► [Gate 4] PIT Mutation
```

- **Minimal Patch Gateway**: The model generates strictly scoped `Unified Diff` replacements rather than whole-file rewrites, preserving unrelated test cases and minimizing token usage.
- **Closed-Loop Self-Repair**: If compilation or test regressions are detected in Gate 2 or Gate 3, targeted diagnostic summaries feed back into the repair controller for up to 2 automated repair rounds.
- **Local Mock Debug Mode**: Supports offline trial runs without calling external LLM APIs, enabling rapid workflow verification without incurring API fees.

---

## 3. Paper Terminology to Code Mapping

To aid Artifact Evaluation (AEC) and research reproducibility, the following table maps the theoretical concepts defined in the paper directly to their software implementation:

| Paper Term | Conceptual Role | Implementation Class / Method |
|---|---|---|
| **Detection Scope** | Project subtrees, packages, and mock object filtering | `DetectionScope`, `DetectionService.scan` |
| **Mock Logic Extraction** | AST extraction of `Mockito.when(...).thenReturn(...)` | `MockInfoExporter`, `MockAnalyzer` |
| **Frequent Stub Set Mining** | Co-occurrence pattern mining via the Apriori algorithm | `AprioriMiner`, `MockCloneMiner.FrequentStubSet` |
| **Mock Clone Instance (MCI)** | Aggregated clone unit comprising multiple test sequences | `MockCloneMiner.formMockCloneInstances` |
| **Encapsulation & Integration** | Extraction of helper methods and inline replacement | `RefactoringAgent._model_input()`, System Prompt |
| **Harness Validation** | Sandboxed multi-tier validation gates | `ProjectHarness`, `ScopedProjectHarness`, `HarnessEvidence` |

---

## 4. Environment Prerequisites

- **Java Development Kit (JDK)**: OpenJDK 17 or higher (Eclipse Adoptium Temurin 17 recommended). Ensure `JAVA_HOME` is set.
- **Build Tool**: Apache Maven 3.9+ or Gradle.
- **Python**: Python 3.11+ with the modern `uv` package manager.
- **Supported Operating Systems**: Windows 10/11, macOS, and Linux (native multi-platform paths and scripts included).

---

## 5. Quick Start: Web UI Refactoring Studio

CloneDeMocker v2 includes an IDE-grade visual Refactoring Studio for interactive human-in-the-loop review.

### 5.1 Launching the Studio

**On Windows (PowerShell)**:
```powershell
.\start-ui.ps1
```

**On macOS / Linux (Bash)**:
```bash
./start-ui.sh
```

Open your browser and navigate to: 👉 **`http://127.0.0.1:8765`**

### 5.2 Key Studio Features
- **Dual-Mode Diff Inspector**: Seamlessly toggle between **Unified** and **Side-by-Side** views with line-number tracking and multi-file tabs.
- **Explicit Decision Pipeline**: Review patches with one-click **`[✓ Accept & Apply]`** to write back changes, or **`[✗ Discard]`** to purge candidate proposals and retain a pristine workspace.
- **Safety & Encoding Guard**: Real-time path inspection alerts for non-ASCII/space characters, plus an interactive **`[🛡️ Env & Safety Check]`** modal displaying active JDK, Python encodings, and platform status.
- **Bilingual Interface**: Full English / Chinese i18n support toggleable via the header button with local storage persistence.

### 5.3 Model Provider Configuration (Optional)
- **Debug Mode (Zero-Token Local Mode)**: Check the **"Debug Mode"** checkbox in the UI (or pass `useMock: true` in API calls). The agent uses the deterministic mock provider to run through the entire diff and harness pipeline without consuming tokens.
- **Real LLM Inference**: Set your OpenAI API key in the environment:
  ```powershell
  $env:OPENAI_API_KEY = "sk-..."
  ```
  Or configure multiple keys via JSON profile mapping:
  ```powershell
  $env:CLONEDEMOCKER_OPENAI_KEYS = '{"default":"sk-...","research":"sk-..."}'
  ```

---

## 6. Headless Experimentation & Paper Evaluation

For automated batch evaluation and replication of the paper's Research Questions (RQ1–RQ4), run the headless pilot driver in `validation/`:

```powershell
# Run pilot evaluation on benchmark project (e.g., Apache Dubbo)
uv run python -m validation.run_pilot \
  --project-dir "D:\Java_projects\Apache\dubbo-3.3.6" \
  --limit-mcis 5 \
  --use-mock
```

- **Scoped Module Acceleration**: `scoped_harness.py` dynamically resolves the minimal affected Maven module (`-pl <module> -am`), avoiding full 100+ module reactor builds.
- **PIT Mutation Testing**: Executes scoped mutation coverage (`mutationCoverage`) to verify that the refactored test cases preserve original fault-detection sensitivity.
- **Atomic Failure Rollback**: Any patch that fails compilation, test consistency, or mutation integrity is rolled back atomically, ensuring evaluation stability.

---

## 7. Defensive Engineering & Platform Compatibility

1. **Strict LF Line Endings**: All programmatic writes enforce `newline="\n"`. On Windows hosts, this eliminates spurious CRLF conversions that violate Spotless linter rules in open-source targets.
2. **ASCII Path Isolation**: In accordance with Windows `sun.jnu.encoding=GBK` constraints, compilation workspaces are dynamically allocated alongside the target project in pure ASCII directories, preventing silent `javac` compilation failures.
3. **Windows Long Paths**: File operations transparently handle deep directory hierarchies exceeding the 260-character `MAX_PATH` boundary.

---

## 8. Repository Layout

```text
CloneDeMocker-v2/
├── app/                        # Python application backend & Refactoring Agent
│   ├── detection_service.py    # Java detection CLI invocation & caching
│   ├── harness.py              # Compilation, test, and PIT execution sandbox
│   ├── model_provider.py       # LLM client abstractions (OpenAI & Mock)
│   ├── refactoring_agent.py    # Neuro-Symbolic Agent core & prompt synthesis
│   ├── server.py               # REST API web service (Starlette/Uvicorn)
│   └── web/                    # Modern bilingual UI (HTML/CSS/JS)
├── DETECTION/                  # Java/Maven static detection engine
│   ├── pom.xml                 # Maven build specification
│   └── src/                    # JavaParser AST extraction & Apriori miner
├── validation/                 # Headless batch evaluation & paper benchmark harness
│   ├── run_pilot.py            # Orchestrator for empirical evaluation
│   ├── scoped_harness.py       # Module-scoped Maven/PIT execution driver
│   └── reset_workspace.py      # Pristine baseline restoration utility
├── tests/                      # Automated Python regression test suite
├── start-ui.ps1                # Windows one-click startup script
├── start-ui.sh                 # macOS/Linux one-click startup script
└── pyproject.toml              # Python package & uv configuration
```

---

## 9. Citation & License

This project is licensed under the [Apache License 2.0](LICENSE). If you use CloneDeMocker in academic research, please cite our corresponding publication.
