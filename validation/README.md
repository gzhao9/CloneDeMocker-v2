# 重构通过率验证 / Refactoring Pass-Rate Validation

这个文件夹和 `studio/` 是分开的：`studio/` 是产品化的检测 + 重构 Agent 工具，这里是拿真实项目跑一遍
论文 RQ2 的验证方法，衡量“重构到底成不成功”。两条路径共享 `ProjectHarness` 的全项目回归门禁：候选补丁
必须在完整 reactor 的 before/after 测试中通过，且 MCI 涉及的测试类必须实际出现在该次报告中。

## 判定标准（原样抄自论文 RQ2.1）

> a refactoring is successful only if it satisfies: (1) Syntactic Validity（编译通过）
> (2) Behavioral Equivalence（重构前后测试通过/失败结果一致）
> (3) Functional Integrity（PIT 变异测试分数不降低）

已知失败模式（论文 RQ2.1，抄下来做失败分类参考）：
- PowerMock 对象被误判成普通 Mockito mock。
- 生成的 helper 方法被塞进 `@Before`，引入其他测试用例的副作用。
- helper 在参数初始化前被调用，导致 broken data flow（论文里最常见的失败原因）。

## 验证对象

`C:\Java_projects\Apache\dubbo`，当前 checkout 是 `dubbo-3.3.6`——确认过这是 GitHub 上
Apache Dubbo 目前真正的最新 tag（没有更新的 3.3.x 或 3.4.x）。

论文 Table 5 标注的版本是 3.2，两者不是同一份代码；但这次是论文被拒后重新投 FSE，不是在回应
某一轮具体审稿意见，不需要跟论文里报过的旧数字保持一致，所以有意选择用当下最新版本而不是
复现论文里那个已经过去一年多的旧版本（`dubbo-3.2.0` 标签也 clone 下来放在
`C:\Java_projects\Apache\dubbo-3.2.0` 了，留作参考，但不作为这次跑数据的对象）。

## 每个 MCI 的验证流程

1. **Before**：在隔离副本上运行完整项目的 compile、test，以及启用时的 PIT；同时检查 MCI 涉及
   的每个测试类都在本次完整测试报告中留下非跳过结果。
2. **执行重构**：真实调用模型生成候选 diff。
3. **After**：对完整项目再次运行相同的 compile、test 和 PIT，对比 before/after 的完整测试结果
   与 PIT 分数。PIT 分数最多允许下降五个百分点。
4. 按论文三条标准判定这次重构成功与否，失败的按上面三种已知模式（或新模式）分类记录。

## 用量控制

`run_pilot.py` 默认只跑 `--limit` 指定的少数几个 MCI（先验证流程通不通），确认没问题后再加大
`--limit` 跑全量——不是一上来就把整个项目的 MCI 都跑一遍模型。

## 文件

按处理阶段分（不是按论文 RQ 编号分——RQ 的具体划分以后可能调整，目录结构不跟着绑定）：

**数据采集**（跑真实/mock 模型，产出原始结果，全部是纯命令行脚本，不依赖任何 AI 编程助手会话）：
- `run_pilot.py`：驱动脚本，跑 scan → detect → 对每个选中的 MCI 做 before/refactor/after 验证，输出汇总报告到 `results/`。
- `scoped_harness.py`：保留用于局部诊断和开发测试；它不再作为论文结果或产品验收门禁。
- `reset_workspace.py`：把复用的隔离副本还原成干净基线。
- `export_canonical.py`：把一次 `run_pilot.py` 的原始结果蒸馏成 `data/<project>/` 下可提交进 git 的精简数据集。

**分析**（吃 `run_pilot.py` 的原始 JSON，算论文要的指标）：
- `analyze_results.py`：算检测规模 / 重构成功率 / 成本等表格数据，可用 `--rq 1|2|3|all` 只导出某一部分。
- `cctr_analysis.py`：测试可读性指标（CCTR）。
- `rq1_1_validator.py`：检测准确率（对照人工标注的 ground truth）。
- `diff_utils.py`：上面几个脚本共用的 diff 解析/应用工具函数。

**报告生成**（把分析结果排版成给人看的 PDF/HTML，输出到仓库根目录的 `reports/`）：
- `report_builders/build_final_report.py`：当前在用的主报告（双语 HTML，含图表、案例、消融计划）。
- `report_builders/build_disputed_cases_report.py`：有争议/失败案例的详细案例分析 PDF。
- `report_builders/legacy/build_report.py`：已被 `build_final_report.py` 取代的旧版本，留作参考。

**其他**：
- `notes/`：会话交接记录、失败案例调查笔记、优化日志——人读的背景资料，不是代码。
- `paper_reference_data.json` / `case_full_cache.json`：分析/报告脚本用的参考数据与缓存，随仓库提交。
- `results/`：每次跑的原始 JSON/分析报告落在这里（gitignore，不提交）。

## rerun_mcis.py (temporary)

Re-runs selected MCIs through the studio backend on another machine and merges the results back
(`run` / `merge`). Written for the 5 Spring Security MCIs listed in `spring_security_dns_mcis.txt`,
whose baseline fails behind a fake-ip DNS proxy; see the script's docstring for both sides' steps.
