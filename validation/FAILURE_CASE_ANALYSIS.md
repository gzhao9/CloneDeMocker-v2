# Dubbo 3.3.6 全量跑批：14 个非 SUCCESS 案例逐一分析

数据来源：`validation/results/pilot-eaffe40a-20260915-032515.json`（109 个 MCI 的全量重构跑批）。
这份报告里 `MODEL_DECLINED` / `FAILED_REFACTORING_GOAL` / `FAILED_SYNTACTIC_VALIDITY` 三类判定
**不受 PIT bug 影响**（PIT 的问题只污染了 SUCCESS 里"变异分数是否保持"这一层的验证，跟这三类的
判定逻辑无关），所以这 14 个案例现在就能作为最终数据使用，不用等第三轮 PIT 重放跑批完成。

每个案例的代码片段直接取自检测器保存的原始数据（`rawStatementInfo`/`testMethodRawCode`），
不是重新手写摘抄的；分类标签来自 `validation/analyze_results.py::categorize_declined_reasons`
的关键词规则（可多标签），可复现。

## 总览

| # | MCI | 判定 | 关键词类别 |
|---|---|---|---|
| 1 | `NacosConnectionManager::1` | MODEL_DECLINED | 语义分化 |
| 2 | `InetAddress::2` | MODEL_DECLINED | 语义分化 |
| 3 | `ServiceDiscovery::1` | MODEL_DECLINED | 语义分化 + 需要新建共享文件 |
| 4 | `MetadataService::1` | MODEL_DECLINED | **mock 未接入被测对象** |
| 5 | `CompositeConfiguration::1` | MODEL_DECLINED | **mock 未接入被测对象** |
| 6 | `ConfigManager::1` | MODEL_DECLINED | **mock 未接入被测对象** |
| 7 | `Invoker::2` | MODEL_DECLINED | 需要运行时验证 |
| 8 | `NotifyListener::2` | MODEL_DECLINED | 需要新建共享文件 |
| 9 | `ExtensionLoader<RegistryProtocolListener>::1` | MODEL_DECLINED | 文件过大/协议限制 |
| 10 | `NamingService::4` | MODEL_DECLINED | 语义分化（无共享语句） |
| 11 | `EventListener::1` | MODEL_DECLINED | 文件过大/协议限制 |
| 12 | `Invoker::3` | FAILED_REFACTORING_GOAL | 部分完成 |
| 13 | `Directory<DemoService>::1` | FAILED_REFACTORING_GOAL | 部分完成 |
| 14 | `Cluster::1` | FAILED_SYNTACTIC_VALIDITY | Spotless（已修复，见下） |

---

## 类别 A：mock 未接入被测对象（"死配置"）—— 3 例

模型的判断逻辑是一致的：这类 mock 被创建、被 stub，但从代码上看**从未被传入任何被测对象的构造函数
/setter/方法参数**，stub 的返回值不可能影响任何被测逻辑。模型认为"提取成 helper"只是把一段没有
实际作用的代码搬个位置，不构成论文定义的 Encapsulation-and-Integration 重构，所以拒绝。

### 4. `org.apache.dubbo.metadata.MetadataService::1`

文件：`ServiceInstancesChangedListenerTest.java`（用户要的具体例子）

```java
// 类级别字段与初始化（@BeforeAll setUp()）
static MetadataService metadataService;
...
metadataService = Mockito.mock(MetadataService.class);
// 注意：这里之后没有任何 when(metadataService....) 或者把它传给别的对象
```

它唯一被使用的地方，在完全不同的一个测试方法里：

```java
@Test
@Order(12)
public void testInstanceWithoutRevision() {
    Set<String> serviceNames = new HashSet<>();
    serviceNames.add("app1");
    ServiceDiscovery serviceDiscovery = Mockito.mock(ServiceDiscovery.class);   // 局部变量，同名遮蔽了类字段
    listener = new ServiceInstancesChangedListener(serviceNames, serviceDiscovery);  // 只传了 serviceDiscovery
    ServiceInstancesChangedListener spyListener = Mockito.spy(listener);
    Mockito.doReturn(null).when(metadataService).getMetadataInfo(eq(null));   // <- 被 MCI 检测到的"重复"语句
    ServiceInstancesChangedEvent event = new ServiceInstancesChangedEvent("app1", app1InstancesWithNoRevision);
    spyListener.onEvent(event);
    assertTrue(true);   // 断言本身也只是个占位符
}
```

`ServiceInstancesChangedListener` 的构造函数只接收了 `serviceDiscovery`，从没接收过 `metadataService`。
`spyListener.onEvent(event)` 这条被测路径不可能触达 `metadataService.getMetadataInfo(...)` 这个 stub——
它是一段配置了但永远不会被调用的死代码。模型的原文判断：

> "the duplicated stubbing targets static metadataService mocks that are not injected into, referenced
> by, or otherwise reachable from the locally created ServiceDiscovery and ServiceInstancesChangedListener
> in either test method."

**结论**：模型的判断是对的，这确实是一段死配置。这类案例更适合的动作其实不是"重构"而是"标记为
可疑的死代码"——超出了当前"Encapsulation and Integration"这一种重构手法的范围，模型选择拒绝而不是
乱猜是稳妥的。

### 5. `org.apache.dubbo.common.config.CompositeConfiguration::1`

文件：`RegistryProtocolTest.java`，跨 7 个测试方法重复。共享语句：
`when(compositeConfiguration.convert(Boolean.class, ENABLE_CONFIGURATION_LISTEN, true)).thenReturn(true)`。

模型原文：

> "each CompositeConfiguration mock is created and stubbed but is never passed to RegistryProtocol,
> ModuleModel, ConfigManager, or any other collaborator... Injecting or wiring it would change behavior
> based on unavailable context about how CompositeConfiguration is intended to be used."

跟案例 4 同一种模式，只是分布在 7 个测试方法而不是 2 个。

### 6. `org.apache.dubbo.config.context.ConfigManager::1`

同一个文件 `RegistryProtocolTest.java`，同样的 7 个测试方法，共享语句：
`when(configManager.getApplicationOrElseThrow()).thenReturn(applicationConfig)`。模型原文几乎是模板化的：

> "each ConfigManager mock is created and stubbed but never passed to RegistryProtocol, ModuleModel,
> ApplicationModel, or any other collaborator used by the test... Removing them would be dead-code
> cleanup rather than the requested two-step mock-clone refactoring."

**这三例合起来看**：`RegistryProtocolTest.java` 一个文件里就占了 2 个（CompositeConfiguration、
ConfigManager），说明这个测试类本身可能存在遗留的、构造时没删干净的死 mock——这不是
CloneDeMocker 的检测误报（这些 mock 确实是重复的），而是被测项目自身测试代码质量的问题，模型
正确地识别出"这不是我该处理的重构范围"，没有强行接线。

---

## 类别 B：语义分化（跨实例故意配置成不同行为）—— 4 例

这类的模式是：mock 在结构上相似（同一个类、同样的方法名），但每个测试方法/序列里的具体 stub 值
是有意设计成不同的，为了覆盖不同的分支/边界条件。合并成一个共享配置会让某些测试失去区分度。

### 2. `java.net.InetAddress::2`（用户可以对比参考的第二个具体例子）

文件横跨两个测试类：`NetUtilsInterfaceDisplayNameHasMetaCharactersTest.java` 和 `NetUtilsTest.java`。

`testIsValidAddress`（NetUtilsTest）里，同一个变量名 `address` 被连续 reassign 了 5 次，每次配置
完全不同的 stub 组合，专门用来跑穷举分支：

```java
@Test
void testIsValidAddress() {
    assertFalse(NetUtils.isValidV4Address((InetAddress) null));
    InetAddress address = mock(InetAddress.class);
    when(address.isLoopbackAddress()).thenReturn(true);
    assertFalse(NetUtils.isValidV4Address(address));
    address = mock(InetAddress.class);
    when(address.getHostAddress()).thenReturn("localhost");
    assertFalse(NetUtils.isValidV4Address(address));
    address = mock(InetAddress.class);
    when(address.getHostAddress()).thenReturn("0.0.0.0");
    assertFalse(NetUtils.isValidV4Address(address));
    address = mock(InetAddress.class);
    when(address.getHostAddress()).thenReturn("127.0.0.1");
    assertFalse(NetUtils.isValidV4Address(address));
    address = mock(InetAddress.class);
    when(address.getHostAddress()).thenReturn("1.2.3.4");
    assertTrue(NetUtils.isValidV4Address(address));   // 唯一一个返回 true 的分支
}
```

而另一个测试类里需要的是**同一个 mock 上叠加三个 stub**（`isLoopbackAddress` + `getHostAddress` +
`isReachable`）。这两种用法没有交集，合并到一个共享 setup 里会破坏其中一个测试的分支覆盖。模型原文：

> "The two clone instances require different InetAddress configurations: one requires isLoopbackAddress,
> getHostAddress, and isReachable stubs on the same mock, while the other intentionally creates separate
> mocks with selectively configured stubs for independent assertions."

### 1. `NacosConnectionManager::1`

文件：`NacosNamingServiceWrapperTest.java`，`testSubscribe` vs `testConcurrency`。两处结构相同
（`connectionManager = Mockito.mock(...)` → `when(connectionManager.getNamingService()).thenReturn(namingService)`），
但 `testConcurrency` 是一个真正的并发测试（起两个线程分别 register/deregister），依赖它自己独立的
`connectionManager` 实例和时序，不能与 `testSubscribe` 共享同一个 mock 生命周期。模型原文：

> "each mock is method-local and testConcurrency relies on its own independently configured connection
> manager during concurrent registration and deregistration."

### 10. `NamingService::4`

同一个文件，6 个序列，`sharedStatements` 为空——因为这次检测到的重复只是"裸的
`Mockito.mock(NamingService.class)`"这个创建语句本身，没有共同的 stub 内容。而在
`testSubscribeMultiManager` 里，同一个测试方法内创建了两个 `NamingService` mock
（`namingService1`、`namingService2`），分别代表连接管理器切换前后的两个不同后端：

```java
NamingService namingService1 = Mockito.mock(NamingService.class);
NamingService namingService2 = Mockito.mock(NamingService.class);
...
Mockito.when(connectionManager.getNamingService()).thenReturn(namingService1);
nacosNamingServiceWrapper.subscribe(...);
Mockito.when(connectionManager.getNamingService()).thenReturn(namingService2);
nacosNamingServiceWrapper.subscribe(...);
```

把它们合并成一个可复用 mock 工厂，会让"切换后端"这个测试意图本身消失。模型原文：

> "The mocks are independently scoped to distinct connection-manager lifecycles (including anonymous
> manager overrides that capture different lists). Integrating them into a shared reusable mock would
> risk cross-test interaction and alter lifecycle behavior."

### 3. `ServiceDiscovery::1`（同时也命中"需要新建共享文件"，见下）

文件：`ServiceInstancesChangedListenerTest.java` + `...WithoutEmptyProtectTest.java`，
`testRevisionFailureOnNotification`。共享语句里有一条是靠线程名字动态切换返回值的
`thenAnswer`（检查调用线程是否包含 `"Dubbo-framework-metadata-retry"`），属于跟具体重试机制强绑定
的行为，模型认为把它当成"可复用配置"提取出来会丢失这个上下文相关性。

---

## 类别 C：需要新建跨测试类的共享文件 —— 2 例（含上面的 ServiceDiscovery::1）

这正是你之前问的"是否要允许跨测试类新建 helper 文件"那个问题的具体数字来源。这两例模型的态度是
"重构方案本身是清楚的，但当前只允许改已有文件、不能新建文件"这个限制挡住了它：

### 8. `org.apache.dubbo.registry.NotifyListener::2`

跨 `ServiceInstancesChangedListenerTest.java` 和 `...WithoutEmptyProtectTest.java`，22 个序列、
6 个测试方法。模型原文最直接地点出了这个限制本身：

> "A behavior-preserving extraction is technically possible for each individual mock creation/stubbing
> pair, but integrating the selected clones across both test classes would require either introducing a
> new shared test utility file or coupling one test class to a helper declared in the other. The
> supplied-file constraint prohibits adding the appropriate shared utility file, while cross-class
> coupling would make the independently executable test classes depend on an unrelated sibling test
> source file."

### 3. `ServiceDiscovery::1`（重复列出，因为它同时属于类别 B 和类别 C）

> "Creating a dedicated shared fixture/helper would require adding a file not supplied for modification."

**这两例的共同点**：模型明确说了"如果能新建文件，方案是清楚的"，不是因为它想不出重构方案，而是
工具本身的约束（只能修改检测到的相关文件）挡住了它。这是当前架构里**唯一一类"放开限制就能直接
挽回成功率"的失败**——跟另外两类（死 mock、语义分化）不同，那两类即使允许新建文件，正确答案仍然是
"不应该重构"。

---

## 类别 D：需要运行时验证 —— 1 例

### 7. `org.apache.dubbo.rpc.Invoker::2`

文件：`MergeableClusterInvokerTest.java`，4 个测试方法 × 2 个 invoker = 8 个序列。共享的
stub（`getInterface`/`getUrl`/`invoke`/`isAvailable`）在部分测试方法里，稍后又被**重新 stub**
了一次 `invoke(invocation)` 来模拟异常场景。模型原文：

> "the repeated stubbing is interleaved with later restubbing of invoke(invocation) in exception
> tests. Although a helper could encapsulate the common statements, safely validating Mockito stubbing
> precedence and all affected invocation behavior requires executing or inspecting the target
> implementation and test suite, which is not available in the supplied files."

这一例比较特殊：模型不是说"不该做"，而是说"能不能做安全，我在纯静态阅读下无法百分百确认
stub 覆盖顺序不会被破坏，需要能跑测试才能验证"。这类可以算是模型在保守性和过度谨慎之间的边界
案例，理论上给它反馈迭代（repair loop 用的诊断信息）能不能说服它重新尝试，值得后续单独看一次
repair 有没有真的被触发过（这条只有 1 次 attempt，没进 repair 循环，因为 MODEL_DECLINED 不属于
`_goal_check`/`FAILED_SYNTACTIC_VALIDITY`/`FAILED_BEHAVIORAL_EQUIVALENCE` 这几种会触发修复的分类）。

---

## 类别 E：完整文件返回协议本身的限制 —— 2 例

这两例不是"重构该不该做"的判断，而是当前 I/O 协议（要求模型返回完整文件内容而不是 diff）本身的
工程限制暴露出来的失败模式。

### 9. `ExtensionLoader<RegistryProtocolListener>::1`

文件：`RegistryProtocolTest.java`（就是类别 A 里那个已经有死 mock 的文件，本身体量很大）。

> "the requested result must include the complete modified source file, while the supplied file is too
> large to reproduce reliably without risking truncation or accidental behavioral changes."

### 11. `EventListener::1`

文件：`NacosNamingServiceWrapperTest.java`。

> "the supplied source content is incomplete for reliable reconstruction: the file is provided as a
> large inline excerpt without an independently addressable source artifact or checksum, making it
> unsafe to return a modified complete file while guaranteeing that all unchanged content is preserved
> byte-for-byte."

**这两例揭示了一个真实的架构短板**：当前 refactoring agent 要求模型"整份文件搬运"，文件一大，
模型自己就会因为"怕改坏没动的部分"而主动放弃。换成基于 diff/patch 的返回协议（模型只输出改动的
hunk，而不是整份文件）理论上能规避这一类失败，但这是一个改动面较大的架构调整，按照"先出一个版本
数据、非重大失误不做频繁优化"的原则，这轮不动，留给下一轮迭代。

---

## 类别 F：目标部分达成（FAILED_REFACTORING_GOAL）—— 2 例

这两例编译、测试都通过（`compileStatus=PASSED`, `testStatus=PASSED`），但用重放 diff 逐行核对
`_goal_check` 同款的"改动前后重复次数"比较后，发现模型只处理了 MCI 里的一部分重复，另一部分原样
留下——是模型"做得不够彻底"，不是检查逻辑的假阳性。

### 12. `org.apache.dubbo.rpc.Invoker::3`

文件：`ConnectivityValidationTest.java`。模型把 `isAvailable()` 的重复 stub 提取成了共享方法：

```java
private void setAvailability(boolean available, Invoker... invokers) {
    for (Invoker invoker : invokers) {
        when(invoker.isAvailable()).thenReturn(available);
    }
}
```

这部分改得对，也编译测试都过。但 MCI 里还标记了另一组重复——`invokerList.add(invokerN);`
在两个**独立的测试方法**里各出现一次，模型完全没碰：

| 行 | before | after |
|---|---|---|
| `invokerList.add(invoker1);` | 2 | 2 |
| `invokerList.add(invoker2);` | 2 | 2 |

### 13. `org.apache.dubbo.rpc.cluster.Directory<org.apache.dubbo.rpc.cluster.filter.DemoService>::1`

文件横跨 `BroadCastClusterInvokerTest.java`（`testNormal`/`testEx`/`testFailPercent`，共享的
`dic` 字段在 `setUp()` 里创建一次）和 `FailSafeClusterInvokerTest.java`（`testNoInvoke` 在方法体
内部局部重新 `mock(Directory.class)`）：

```java
// FailSafeClusterInvokerTest.testNoInvoke() 局部代码
dic = mock(Directory.class);
given(dic.getUrl()).willReturn(url);
given(dic.getConsumerUrl()).willReturn(url);
```

| 行 | before | after |
|---|---|---|
| `dic = mock(Directory.class);` | 2 | 2 |

**这两例的共同结构**：都是"两个独立测试方法各自要一份新鲜的 mock 实例"，如果真要去重，正确做法是
在两个测试方法都调用一个共享的工厂方法（类似案例 12 里那个成功的 `setAvailability`），但模型这次
只处理了其中一组重复语句，另一组同类型的重复被漏掉了。这提示 repair 循环目前的诊断信息（只有
harness 的编译/测试日志）不包含"目标未完全达成、还有 N 处遗漏"这类信息，所以即使达标失败，模型
也没有被反馈这个具体信号去补第二刀——`FAILED_REFACTORING_GOAL` 目前不进 repair 循环
（`run_pilot.py` 里只有 `FAILED_SYNTACTIC_VALIDITY`/`FAILED_BEHAVIORAL_EQUIVALENCE` 会触发修复）。
如果要挽回这两例，比起改模型行为，更直接的杠杆是把 `_goal_check` 的失败也纳入 repair 循环，并把
"具体哪几行还重复"作为诊断信息喂回去——这是一个可评估的后续优化点，暂不在这轮动。

---

## 类别 G：Spotless 误判为语法无效 —— 1 例（已在本轮修复）

### 14. `org.apache.dubbo.rpc.cluster.Cluster::1`

文件：`RegistryProtocolTest.java`。完整错误日志显示，编译本身完全成功（`Compiling 48 source files`
无报错），失败发生在后面的 Spotless 格式检查：

```
[ERROR] Failed to execute goal com.diffplug.spotless:spotless-maven-plugin:2.44.5:check (default)
        on project dubbo-registry-api: The following files had format violations:
[ERROR]     src\test\java\...\RegistryProtocolTest.java
[ERROR]         @@ -114,9 +114,13 @@
[ERROR]          ········ModuleModel·moduleModel·=·Mockito.spy(...);
[ERROR]         -········moduleModel.getApplicationModel().getApplicationConfigManager().setApplication(...);
[ERROR]         +········moduleModel
[ERROR]         +················.getApplicationModel()
[ERROR]         +················.getApplicationConfigManager()
[ERROR]         +················.setApplication(...);
```

模型生成的代码把长方法链拆成了多行——语法完全合法、能编译，只是缩进/换行风格跟 Dubbo 自己的
Spotless 规则不一致。这跟本轮修复的 pom.xml 注入触发 Spotless 误判是**同一个根因**：论文对
"Syntactic Validity" 的定义是"能否编译"，不是某个项目自选的格式规范。加上
`-Dspotless.check.skip=true -Dspotless.apply.skip=true` 之后，这个案例预期会在第三轮重放里
转判为通过（编译+测试都过，最终是否 SUCCESS 取决于 PIT 那层是否也通过）——这是对这次修复的一次
真实数据侧验证，不是巧合。

---

## 小结：14 例里真正可以通过工程改动挽回的有哪些

| 案例 | 当前判定 | 是否可挽回 | 挽回方式 |
|---|---|---|---|
| `Cluster::1` | FAILED_SYNTACTIC_VALIDITY | **已修复** | 本轮加的 Spotless 跳过 |
| `NotifyListener::2`、`ServiceDiscovery::1` | MODEL_DECLINED | **可挽回** | 允许新建跨测试类共享文件 |
| `Invoker::3`、`Directory<DemoService>::1` | FAILED_REFACTORING_GOAL | **部分可挽回** | 把 `_goal_check` 失败也纳入 repair 循环，反馈"还有哪几行重复" |
| `ExtensionLoader<...>::1`、`EventListener::1` | MODEL_DECLINED | 架构级，暂缓 | 改用 diff/patch 返回协议而不是整份文件 |
| `Invoker::2` | MODEL_DECLINED | 边界案例，价值不确定 | 值得单独看一次 repair 反馈能否说服模型 |
| 其余 7 例（3 例死 mock + 4 例语义分化） | MODEL_DECLINED | **不应挽回** | 模型的拒绝是正确判断，这是被测项目本身的测试代码特性，不是重构机会 |

也就是说，14 个失败案例里，**7 个是模型正确识别出"不该重构"**（死 mock / 故意的语义分化），
**1 个已经在本轮修复**，剩下 **6 个是有具体、可执行的后续优化空间**的（2 个新建文件限制、
2 个 repair 循环覆盖面、2 个大文件协议限制）。
