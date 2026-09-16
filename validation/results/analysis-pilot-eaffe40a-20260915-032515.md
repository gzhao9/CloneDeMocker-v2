# D:\Java_projects\Apache\dubbo-3.3.6 — 新旧数据对比 / new vs. old
generated from `eaffe40ae9cc4f509d0616978c1fdfe0`, model `gpt-5.6-terra`, repair=True, directLlmBaseline=False

## RQ1 检测规模 / detection scale (Table 5 & 6)

| | 旧 Dubbo 3.2 (78/592 修正版) | 新 Dubbo 3.3.6 |
|---|---|---|
| Mock Objects | 592 | 778 |
| Mock Clone Instances | 78 | 109 |
| Class-Level 影响占比 | 34% (52/151) | 34.5% (69/200) |
| Case-Level 涉及测试用例数 | 155 | 204 |
| Clone-Involved MO 削减 | -72% | 69.9% |
| Clone-Involved LOC 削减 | -44% | 45.3% |
| Whole-Project MO 削减 | -35% | 32.5% |
| Whole-Project LOC 削减 | -23% | 20.0% |

## RQ2 重构成功率 / refactoring success rate (Table 7)

| | 旧 Dubbo 3.2 | 新 Dubbo 3.3.6（一次生成 first-pass） | 新 Dubbo 3.3.6（含修复 final） |
|---|---|---|---|
| MCI 级 | 100% | 77.1% (84/109) | 87.2% (95/109) |
| 测试级 | 100% | 71.3% (234/328) | 83.5% (274/328) |

## 失败原因统计（查日志自动得出，不是人工过一遍）/ failure breakdown (from logs, not manual)

| 分类 | 数量 | 示例 MCI |
|---|---|---|
| SUCCESS | 95 |  |
| MODEL_DECLINED | 11 | org.apache.dubbo.registry.nacos.NacosConnectionManager::1, java.net.InetAddress::2, org.apache.dubbo.registry.client.ServiceDiscovery::1, org.apache.dubbo.metadata.MetadataService::1, org.apache.dubbo.common.config.CompositeConfiguration::1 |
| FAILED_REFACTORING_GOAL | 2 | org.apache.dubbo.rpc.Invoker::3, org.apache.dubbo.rpc.cluster.Directory<org.apache.dubbo.rpc.cluster.filter.DemoService>::1 |
| FAILED_SYNTACTIC_VALIDITY | 1 | org.apache.dubbo.rpc.cluster.Cluster::1 |

## RQ3 成本 / cost

model: `gpt-5.6-terra` — Stated by Codex in the 2026-09-14 design-review session (项目接力.md); cached_input_tokens pricing not confirmed, treated as priced same as regular input tokens until confirmed otherwise — flag this assumption wherever cost is reported.

| | 一次生成 first-pass | 含修复 total |
|---|---|---|
| API 调用次数 | — | 122 |
| Input tokens | 619591 | 782848 |
| Output tokens | 253805 | 287001 |
| 估算成本 (USD) | $4.2848 | $5.0097 |

旧论文单独的 Dubbo RQ3 数字没有留存（只有六项目总计 153 分钟 / $14.41 / 730万 token，以及单项目 $0.29-$5.96 的区间），所以这里没法逐项对比，只能列新数据。

PIT 的墙钟耗时没有计入上面的时间/成本（PIT 不消耗 token，且 harness 目前还没有记录逐阶段耗时，细节见 `OPTIMIZATION_LOG.md`）。