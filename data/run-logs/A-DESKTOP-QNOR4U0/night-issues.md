# 夜间运行问题与决策记录 / Overnight issues & decisions (2026-09-22)

| 时间 | 项目 | 对象 | 问题 | 处理 |
|---|---|---|---|---|
| 01:35 | druid-37.0.0 | TaskToolbox::1 | 候选补丁使 AbstractTaskTest 4 个测试 ERROR（修复 1 次仍失败） | 真实重构失败，按 FAILED_BEHAVIORAL_EQUIVALENCE 记录，继续 |
| 02:16 | druid-37.0.0 | HttpServletRequest::1 | 目标测试在未修改的基线上就不通过（ENVIRONMENT_NOT_READY） | 按环境问题记录，未消耗模型调用，继续 |
| 02:33 | druid-37.0.0 | TaskActionClient::1 | 补丁后编译不通过（修复后仍失败） | 按 FAILED_SYNTACTIC_VALIDITY 记录，继续 |
| 02:38 | druid-37.0.0 | Injector::1 | 基线不可用（ENVIRONMENT_NOT_READY） | 按环境问题记录，继续 |
| 02:43 | druid-37.0.0 | Injector::2 | 基线不可用（ENVIRONMENT_NOT_READY） | 按环境问题记录，继续 |

**根因（02:45 排查）**：Druid 的环境失败是 Windows 平台问题，与重构无关，也不是本机配置错误：
- `HttpServletRequest::1`、`Injector::2`：测试把 segment 文件内存映射（mmap）后再删临时目录，Windows 上被映射的文件无法删除（`Cannot delete file ... 00000.smoosh: 另一个程序正在使用此文件`），Druid 官方只支持 Linux/macOS。
- `Injector::1`：`JettyServerModuleTest` 把 Windows 路径转 URI 失败（`Cannot be converted to URI`）。
- 决策：不改 Druid 测试、不跳过测试，照实记为 ENVIRONMENT_NOT_READY；这些 MCI 可以日后在 Linux 上用 `validation/rerun_mcis.py run --ids ...` 补跑再合并。
| 02:55 | druid-37.0.0 | Binding<SslContextFactory.Server>::1 | 基线不可用（ENVIRONMENT_NOT_READY），同属 Windows 平台问题 | 记录，继续 |
| 02:59 | druid-37.0.0 | TLSCertificateChecker::1 | 基线不可用（ENVIRONMENT_NOT_READY），TLS/证书相关测试 | 记录，继续 |
| 03:26 | druid-37.0.0 | GroupByColumnSelectorPlus::1 | 基线不可用（ENVIRONMENT_NOT_READY） | 记录，继续 |
| 03:29 | druid-37.0.0 | ServiceEmitter::2 | 基线不可用（ENVIRONMENT_NOT_READY） | 记录，继续 |
| 03:43 | druid-37.0.0 | HandlingInputRowIterator.InputRowHandler::1 | 基线没有产生实际执行的测试结果 | 记录，继续 |

**03:45 复查 8 个环境失败**：3 个 mmap 删文件 / 3 个 Windows 路径转 URI（`Binding<SslContextFactory.Server>::1`、`TLSCertificateChecker::1`、`Injector::1`）/ `ServiceEmitter::2` 是 `MetricsModuleTest` 的测试 JVM 在本地（native）代码里崩溃（非内存不足，Windows 原生库问题）/ `GroupByColumnSelectorPlus::1` 目标测试基线未通过 / `InputRowHandler::1` 基线没有跑出任何测试。都不是瞬时故障，本机重跑不会变，留给 Linux 补跑。
| 03:47 | druid-37.0.0 | JettyServerInitializer::1 | 基线不可用（ENVIRONMENT_NOT_READY），Jetty 模块，同类 Windows URI 问题的可能性大 | 记录，继续 |
| 03:52 | druid-37.0.0 | SegmentsMetadataManagerConfig::1 | 基线不可用（ENVIRONMENT_NOT_READY） | 记录，继续 |
| 03:57 | druid-37.0.0 | PasswordProvider::1 | 基线不可用（ENVIRONMENT_NOT_READY） | 记录，继续 |
| 04:05 | druid-37.0.0 | CloudObjectInputSource::2 | MODEL_DECLINED：没有任何阶段产出可应用的编辑 | 记录为模型未能生成补丁，继续 |
| 05:05 | druid-37.0.0 | SegmentsMetadataManager::1 | 基线不可用（ENVIRONMENT_NOT_READY） | 记录，继续 |
| 05:14 | druid-37.0.0 | SslContextFactory.Server::1 | 基线不可用（ENVIRONMENT_NOT_READY），TLS/Jetty 同类问题 | 记录，继续 |
| 05:18 | druid-37.0.0 | SegmentReplicaCount::1 | 基线不可用（ENVIRONMENT_NOT_READY） | 记录，继续 |
| 05:43 | druid-37.0.0 | Provider<SslContextFactory.Server>::1 | 基线不可用（ENVIRONMENT_NOT_READY），TLS/Jetty 同类问题 | 记录，继续 |

## CloudStack 4.23.0.0（06:04 开始重构）
**规模远超预期，需要你回来后决定**：检测出 13873 个 mock 对象、**1794 个 MCI**（Druid 87、spring-security 314）。已核对不是误扫：MCI 全部来自 CloudStack 真实模块（server 678、plugins/network-elements 303、plugins/hypervisors 194、engine/orchestration 92 ……）；仓库里多出来的 antlr/cglib/com 等未跟踪目录不含 .java 文件，没有贡献 MCI。
- 按 Druid 的速度（约 3 分钟/个、约 3.4 万 token/个）估算：全量约 **90 小时、约 6000 万 token**，并且会把 dubbo / spring-security 的 PIT 推后约 4 天。
- 决策：按"夜里尽量多跑数据"的原则先继续跑；结果按 MCI 逐条并入 data/，随时可以停，之后可断点续跑（已验证过的补丁命中缓存与验证账本，重跑很快）。**你回来后请决定**：全量跑完 / 跑到某个数量（或按模块抽样）后停，先回去做 PIT。
- **06:10 编译失败的 ENVIRONMENT_NOT_READY（预期内）**：`NetworkModel::1/2` 等 MCI 位于 `noredist` profile 里的模块（需要 VMware 等不可再分发的依赖，默认构建不包含），Maven 报 `Could not find the selected project in the reactor`。共 **252 个 MCI** 受影响：tungsten 169、vmware 27、nsx 22、cisco-vnmc 14、netris 13、juniper-contrail 6、veeam 1；其余 1542 个在默认构建里。
  - 决策：让它们快速失败（约 1 分钟/个，不调用模型、不花 token），记为 ENVIRONMENT_NOT_READY；不在夜里改 harness 去开 `-Dnoredist`（会把 vmware 等模块一起拉进 reactor，风险大）。以后可以只对 tungsten 这类模块开 profile 单独补跑。
| 06:44 | cloudstack-4.23.0.0 | NetworkModel::5 | 补丁后测试结果改变（FAILED_BEHAVIORAL_EQUIVALENCE） | 按真实失败记录，继续 |
| 07:05 | cloudstack-4.23.0.0 | HostDao::4 | MODEL_DECLINED | 记录，继续 |
| 08:31 | cloudstack-4.23.0.0 | PhysicalNetworkDao::1 | 补丁后测试结果改变（FAILED_BEHAVIORAL_EQUIVALENCE） | 按真实失败记录，继续 |
| 08:47 | cloudstack-4.23.0.0 | NetworkACLItemVO::1 | 补丁后测试结果改变（FAILED_BEHAVIORAL_EQUIVALENCE） | 按真实失败记录，继续 |
