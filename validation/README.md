# 重构通过率验证 / Refactoring Pass-Rate Validation

这个文件夹和 `app/` 是分开的：`app/` 是产品化的检测 + 重构 Agent 工具，这里是拿真实项目跑一遍
论文 RQ2 的验证方法，衡量“重构到底成不成功”。不复用 `app/` 里任何写死全量测试的逻辑，只在
`app/refactoring_agent.py` 里加了一个可选的 `harness` 注入参数，让这里可以传入范围受限的 Harness。

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

1. **Before**：在隔离副本上，只针对这个 MCI 涉及的测试类（不是全量 `mvn test`）跑一次
   `mvn test -Dtest=<涉及的测试类>` + 一次 PIT `mutationCoverage`（`-DtargetTests=` 同样限定
   到这些测试类）——PIT 的粒度是"这些测试类组成的 suite 跑一次"，不是每个测试方法单独跑一次。
2. **执行重构**：调用 `RefactoringAgent.run(..., harness=ScopedProjectHarness(...))`，真实调用
   模型生成候选 diff。
3. **After**：同样只对这些测试类重新跑一次 test + PIT，对比 before/after 的
   compile/test 结果与 PIT 分数。
4. 按论文三条标准判定这次重构成功与否，失败的按上面三种已知模式（或新模式）分类记录。

## 用量控制

`run_pilot.py` 默认只跑 `--limit` 指定的少数几个 MCI（先验证流程通不通），确认没问题后再加大
`--limit` 跑全量——不是一上来就把整个项目的 MCI 都跑一遍模型。

## 文件

- `scoped_harness.py`：`ProjectHarness` 的子类，把 compile 之外的 test/PIT 命令范围收窄到指定测试类。
- `run_pilot.py`：驱动脚本，跑 scan → detect → 对每个选中的 MCI 做 before/refactor/after 验证，输出汇总报告。
- `results/`：每次跑的 JSON 报告落在这里（gitignore，不提交）。
