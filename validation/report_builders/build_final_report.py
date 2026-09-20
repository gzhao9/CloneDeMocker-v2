# -*- coding: utf-8 -*-
"""Builds the bilingual (EN/ZH toggle) combined report:
  Part 1 -- comparison vs. the FSE paper baseline (Dubbo 3.2), with charts,
            reusing the already-validated numbers from
            validation/results/dubbo-3.3.6-validation-report-20260916.pdf
            (re-verified against current source data where checked).
  Part 2 -- case studies from the FINAL run (pilot-merged-redesign-round1):
            the 2 genuine remaining failures, plus 6 cases where the model
            flagged a reservation (modelCaveat) but was still run to
            completion and verified -- showing whether the reservation was
            actually justified. The earlier interim methodology (blocking on
            model objection) is intentionally not shown; it was a discarded
            evaluation-design mistake, not a result.
  Part 3 -- the proposed 3-arm ablation study (not yet run).

用法 / Usage:
    uv run --with pygments --with fpdf2 --with Pillow python validation/report_builders/build_final_report.py
"""
import json
import html as htmllib
from pathlib import Path
from pygments import highlight
from pygments.lexers import JavaLexer
from pygments.formatters import HtmlFormatter

HERE = Path(__file__).parent
VALIDATION_DIR = HERE.parent
REPO_ROOT = VALIDATION_DIR.parent
FULL = json.loads((VALIDATION_DIR / "case_full_cache.json").read_text(encoding="utf-8"))
OUT = REPO_ROOT / "reports" / "clonedemocker-report-20260917.html"

fmt = HtmlFormatter(nowrap=True)


def hl(java_src):
    return highlight(java_src, JavaLexer(), fmt)


def esc(s):
    return htmllib.escape(s, quote=True)


def B(en, zh, tag="span", cls=""):
    c = (" " + cls) if cls else ""
    return (f'<{tag} class="lang-en{c}">{en}</{tag}>'
            f'<{tag} class="lang-zh{c}" hidden>{zh}</{tag}>')


# ---------------------------------------------------------------------------
# Chart helper -- simple grouped vertical bar chart as inline SVG
# ---------------------------------------------------------------------------
SERIES_COLORS = ["var(--chart-paper)", "var(--chart-ours)", "var(--chart-ours2)"]


def grouped_bar_svg(categories, series, w=640, h=300, y_max=None, y_suffix="",
                     y_fmt=None, bar_label_fmt=None):
    """categories: list[str]; series: list[(label, [values...])] same length as categories."""
    pad_l, pad_r, pad_t, pad_b = 34, 14, 30, 54
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b
    n_cat = len(categories)
    n_ser = len(series)
    all_vals = [v for _, vals in series for v in vals]
    vmax = y_max if y_max else max(all_vals) * 1.18
    group_w = plot_w / n_cat
    bar_gap = group_w * 0.14
    bar_w = (group_w - bar_gap * (n_ser + 1)) / n_ser

    def y(v):
        return pad_t + plot_h - (v / vmax * plot_h)

    parts = []
    # gridlines
    for i in range(5):
        gy = pad_t + plot_h - (i / 4) * plot_h
        val = vmax * i / 4
        label = y_fmt(val) if y_fmt else f"{val:.0f}"
        parts.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{w-pad_r}" y2="{gy:.1f}" '
                      f'class="chart-grid"/>')
        parts.append(f'<text x="{pad_l-6}" y="{gy+3:.1f}" class="chart-axis" '
                      f'text-anchor="end">{label}</text>')
    # bars
    for ci, cat in enumerate(categories):
        gx0 = pad_l + ci * group_w
        for si, (slabel, vals) in enumerate(series):
            v = vals[ci]
            bx = gx0 + bar_gap + si * (bar_w + bar_gap)
            by = y(v)
            bh = pad_t + plot_h - by
            color = SERIES_COLORS[si % len(SERIES_COLORS)]
            parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" '
                          f'height="{bh:.1f}" fill="{color}" rx="2"/>')
            lbl = bar_label_fmt(v) if bar_label_fmt else f"{v:g}"
            parts.append(f'<text x="{bx+bar_w/2:.1f}" y="{by-5:.1f}" '
                          f'class="chart-val" text-anchor="middle">{lbl}</text>')
        parts.append(f'<text x="{gx0+group_w/2:.1f}" y="{h-pad_b+18:.1f}" '
                      f'class="chart-cat" text-anchor="middle">{esc(cat)}</text>')
    # axis line
    parts.append(f'<line x1="{pad_l}" y1="{pad_t+plot_h:.1f}" x2="{w-pad_r}" '
                  f'y2="{pad_t+plot_h:.1f}" class="chart-axisline"/>')
    # legend
    lx = pad_l
    ly = 12
    for si, (slabel, _) in enumerate(series):
        color = SERIES_COLORS[si % len(SERIES_COLORS)]
        parts.append(f'<rect x="{lx:.1f}" y="{ly-9:.1f}" width="10" height="10" '
                      f'fill="{color}" rx="2"/>')
        parts.append(f'<text x="{lx+15:.1f}" y="{ly:.1f}" class="chart-legend">{esc(slabel)}</text>')
        lx += 15 + len(slabel) * 6.4 + 18
    return (f'<svg viewBox="0 0 {w} {h}" class="chart-svg" role="img">'
            + "".join(parts) + "</svg>")


# ---------------------------------------------------------------------------
# Pipeline diagram -- simple box/arrow flow, inline SVG
# ---------------------------------------------------------------------------
def pipeline_svg():
    stages = [
        ("Detection", "static analysis", False),
        ("LLM Proposal", "+ caveat if unsure", True),
        ("Harness Verify", "compile · test · mutation", True),
        ("Repair Loop", "≤ 2 rounds", False),
        ("Classification", "SUCCESS / FAILED_*", False),
    ]
    bw, bh, gap = 150, 64, 34
    pad = 16
    top = 44
    w = pad * 2 + len(stages) * bw + (len(stages) - 1) * gap
    h = top + bh + 74
    parts = [f'<svg viewBox="0 0 {w} {h}" class="chart-svg pipeline-svg" role="img">']
    parts.append(
        '<marker id="pl-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="var(--ink-faint)"/></marker>'
        '<marker id="pl-arrow-accent" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker>'
    )
    xs = []
    for i, (label, sub, is_new) in enumerate(stages):
        x = pad + i * (bw + gap)
        xs.append(x)
        stroke = "var(--accent)" if is_new else "var(--border)"
        sw = "2" if is_new else "1.2"
        parts.append(f'<rect x="{x}" y="{top}" width="{bw}" height="{bh}" rx="9" '
                      f'fill="var(--surface)" stroke="{stroke}" stroke-width="{sw}"/>')
        parts.append(f'<text x="{x+bw/2}" y="{top+27}" class="pl-label" text-anchor="middle">{esc(label)}</text>')
        parts.append(f'<text x="{x+bw/2}" y="{top+45}" class="pl-sub" text-anchor="middle">{esc(sub)}</text>')
        if is_new:
            bx, by = x + bw - 38, top - 10
            parts.append(f'<rect x="{bx}" y="{by}" width="36" height="16" rx="8" fill="var(--accent)"/>')
            parts.append(f'<text x="{bx+18}" y="{by+11.5}" class="pl-new" text-anchor="middle">NEW</text>')
        if i > 0:
            px = xs[i - 1] + bw
            parts.append(f'<line x1="{px+2}" y1="{top+bh/2}" x2="{x-4}" y2="{top+bh/2}" '
                          f'stroke="var(--ink-faint)" stroke-width="1.4" marker-end="url(#pl-arrow)"/>')
    # feedback loop: Repair Loop (index 3) back to LLM Proposal (index 1), on failure
    fx0 = xs[3] + bw / 2
    fx1 = xs[1] + bw / 2
    fy = top + bh + 34
    parts.append(f'<path d="M{fx0},{top+bh} C{fx0},{fy} {fx1},{fy} {fx1},{top+bh}" '
                  f'fill="none" stroke="var(--fail)" stroke-width="1.4" stroke-dasharray="4 3" '
                  f'marker-end="url(#pl-arrow)"/>')
    parts.append(f'<text x="{(fx0+fx1)/2}" y="{fy+13}" class="pl-loop" text-anchor="middle">retry on compile/test failure</text>')
    parts.append('</svg>')
    return "".join(parts)


pipeline_diagram = pipeline_svg()

# ===========================================================================
# PART 1 -- comparison vs. paper
# ===========================================================================

chart_scale = grouped_bar_svg(
    ["Mock Objects", "Mock Clone Instances", "Test cases involved"],
    [("Paper -- Dubbo 3.2", [592, 78, 155]),
     ("This work -- Dubbo 3.3.6", [778, 109, 204])],
    w=620, h=280, bar_label_fmt=lambda v: f"{v:g}",
)

chart_reduction = grouped_bar_svg(
    ["Clone-involved MO", "Clone-involved LOC", "Whole-project MO", "Whole-project LOC"],
    [("Paper -- Dubbo 3.2", [72, 44, 35, 23]),
     ("This work -- Dubbo 3.3.6", [69.9, 45.3, 32.5, 20.0])],
    w=620, h=280, y_max=85, bar_label_fmt=lambda v: f"{v:g}%",
)

chart_success = grouped_bar_svg(
    ["MCI-level", "Test-level"],
    [("Paper -- 6-project aggregate", [89, 86]),
     ("This work -- Dubbo 3.3.6", [98.2, 98.2])],
    w=480, h=280, y_max=112, bar_label_fmt=lambda v: f"{v:g}%",
)

chart_cctr = grouped_bar_svg(
    ["< 10%", "10 - 40%", "40 - 90%", "90 - 100%"],
    [("Paper (6-project aggregate)", [20, 53.9, 20, 2.1]),
     ("This work -- Dubbo 3.3.6", [19.4, 57.6, 21.3, 1.6])],
    w=620, h=280, y_max=68, bar_label_fmt=lambda v: f"{v:g}%",
)

RQ1_TABLE = [
    ("Mock Objects", "592", "778"),
    ("Mock Clone Instances", "78", "109"),
    (B("Class-level impact", "类级别影响占比"), "34% (52/151)", "34.5% (69/200)"),
    (B("Test cases involved", "涉及的测试用例数"), "155", "204"),
]

RQ11_TABLE = [
    ("Precision", "1.000", "1.000"),
    ("Recall", B("1.000 (dead-code artifact)", "1.000（死代码导致的假象）"), "0.971"),
    ("F1", "1.000", "0.985"),
]

RQ3_TABLE = [
    (B("Per-project cost", "单项目成本"),
     B("$0.29 -- $5.96 (6-project range)", "$0.29 -- $5.96（六项目区间）"),
     B("$5.01 (122 calls, full 109-MCI run)", "$5.01（122 次调用，109 个 MCI 全量跑批）")),
]

part1_html = f'''
<section class="part" id="part-1">
  <div class="part-head">
    <span class="part-no">I</span>
    <h2>{B("Comparison vs. the FSE Paper", "与 FSE 论文的对比")}</h2>
  </div>
  <p class="part-dek">{B(
    "Same detection and refactoring pipeline, run on Apache Dubbo 3.3.6 instead of the "
    "paper's Dubbo 3.2, using the redesigned protocol pictured below.",
    "同一套检测与重构流程，跑在 Apache Dubbo 3.3.6（论文原用的是 3.2）上，使用下面这套"
    "重新设计后的协议。"
  )}</p>

  <div class="pipeline-box">{pipeline_diagram}</div>
  <p class="note pipeline-note">{B(
    "Two pieces are new versus the paper's pipeline: the model can attach a "
    "<code>modelCaveat</code> to a proposal instead of the run stopping there, and every "
    "proposal &mdash; caveat or not &mdash; goes through an isolated harness that compiles "
    "it, runs the real tests, and checks mutation coverage before/after, instead of a "
    "manual pass/fail call. A compile or test failure feeds the harness's own diagnostics "
    "back to the model for up to two repair rounds before the candidate is finally scored.",
    "相比论文的流程，有两处是新的：模型现在可以给一个方案附上 <code>modelCaveat</code>，"
    "而不是就此停在那里不往下做；并且不管有没有顾虑，每个方案都要经过一个隔离的 harness"
    "&mdash;&mdash;编译、跑真实测试、核对改动前后的变异覆盖率&mdash;&mdash;而不是靠人工"
    "判断通过与否。编译或测试失败时，会把 harness 自己的诊断信息反馈回模型，最多修复两轮，"
    "之后才给这个候选项定最终结果。"
  )}</p>

  <h3 class="rq-head">{B("RQ1 &middot; Detection scale", "RQ1 &middot; 检测规模")}</h3>
  <div class="chart-row">
    <div class="chart-box">{chart_scale}</div>
    <div class="chart-box">{chart_reduction}</div>
  </div>
  <div class="table-wrap"><table class="data-table">
    <thead><tr><th>{B("Metric","指标")}</th><th>{B("Paper -- Dubbo 3.2","论文 -- Dubbo 3.2")}</th><th>{B("This work -- Dubbo 3.3.6","本次 -- Dubbo 3.3.6")}</th></tr></thead>
    <tbody>{"".join(f"<tr><td>{a}</td><td>{b}</td><td class='hl'>{c}</td></tr>" for a,b,c in RQ1_TABLE)}</tbody>
  </table></div>

  <h3 class="rq-head">{B("RQ1.1 &middot; Detection accuracy", "RQ1.1 &middot; 检测准确率")}</h3>
  <div class="table-wrap"><table class="data-table">
    <thead><tr><th>{B("Metric","指标")}</th><th>{B("Paper -- Dubbo 3.2","论文 -- Dubbo 3.2")}</th><th>{B("This work -- Dubbo 3.3.6","本次 -- Dubbo 3.3.6")}</th></tr></thead>
    <tbody>{"".join(f"<tr><td>{a}</td><td>{b}</td><td class='hl'>{c}</td></tr>" for a,b,c in RQ11_TABLE)}</tbody>
  </table></div>
  <p class="note">{B(
    "The paper's own validator script has a grouping-key bug that pins Recall at 1.0 for "
    "any dataset, including its own Dubbo data (confirmed by re-running the unmodified "
    "script against it: 0 of 68 group keys ever matched). The figures above use the "
    "corrected key.",
    "论文自带的校验脚本有一个分组键 bug，导致任何数据集的 Recall 都会被钉死在 1.0（包括"
    "论文自己的 Dubbo 数据——用未改动的脚本重新跑一遍，68 个分组键没有一个真正匹配上）。"
    "上表用的是修正后的分组键。"
  )}</p>

  <h3 class="rq-head">{B("RQ2.1 &middot; Refactoring success rate", "RQ2.1 &middot; 重构成功率")}</h3>
  <div class="chart-row"><div class="chart-box">{chart_success}</div></div>
  <p class="note">{B(
    "The paper's Dubbo-only figure is 100%, but that's a single-digit MCI sample (part of "
    "its 73-MCI manually-verified set) where everything happened to pass &mdash; not a "
    "meaningful comparison point. The number above is the paper's own overall figure across "
    "all 6 study subjects (65/73 MCIs, 269/310 tests). This work: 107 of 109 MCIs, 322 of "
    "328 individual tests, on Dubbo 3.3.6 alone.",
    "论文里 Dubbo 单项目的数字是 100%，但那只是论文 73 个 MCI 人工核验样本里分到 Dubbo 的"
    "个位数几个、恰好全部通过而已，不是一个有意义的比较对象。上面用的是论文自己给出的六个"
    "项目整体数字（65/73 个 MCI，269/310 个测试）。本次数字是 109 个 MCI 里 107 个成功、"
    "328 个具体测试里 322 个成功，只在 Dubbo 3.3.6 一个项目上跑出来的。"
  )}</p>

  <h3 class="rq-head">{B("RQ2.2 &middot; Test simplification (CCTR)", "RQ2.2 &middot; 测试简化程度（CCTR）")}</h3>
  <div class="chart-row"><div class="chart-box">{chart_cctr}</div></div>
  <p class="note">{B(
    "Computed over the real before/after diffs of all 314 test methods across the 107 "
    "successfully refactored MCIs &mdash; not a simulation. Mean per-case reduction: "
    "27.8% (the paper does not state a single aggregate mean).",
    "基于 107 个重构成功的 MCI 里共 314 个测试方法"
    "的真实改动前后 diff 计算得出，不是模拟。"
    "平均单例减少幅度：27.8%（论文本身没有给出"
    "单一的汇总均值）。"
  )}</p>

  <h3 class="rq-head">{B("RQ3 &middot; Cost", "RQ3 &middot; 成本")}</h3>
  <div class="table-wrap"><table class="data-table">
    <thead><tr><th></th><th>{B("Paper (6-project aggregate)","论文（六项目汇总）")}</th><th>{B("This work -- Dubbo 3.3.6","本次 -- Dubbo 3.3.6")}</th></tr></thead>
    <tbody>{"".join(f"<tr><td>{a}</td><td>{b}</td><td class='hl'>{c}</td></tr>" for a,b,c in RQ3_TABLE)}</tbody>
  </table></div>
</section>'''

print("stage2 part1 ok", len(part1_html))

# ===========================================================================
# PART 2 -- case studies (from the FINAL run: pilot-merged-redesign-round1)
# ===========================================================================
CASES2 = [
    dict(fkey="4", mci="org.apache.dubbo.metadata.MetadataService::1", group="ok",
         files="ServiceInstancesChangedListenerTest.java",
         before=[("Field declaration and the line inside @BeforeAll setUp() that creates it",
             'static MetadataService metadataService;\n'
             'metadataService = Mockito.mock(MetadataService.class);'),
            ("The only method that touches it",
             '@Test\n@Order(12)\n'
             'public void testInstanceWithoutRevision() {\n'
             '    Set<String> serviceNames = new HashSet<>();\n'
             '    serviceNames.add("app1");\n'
             '    ServiceDiscovery serviceDiscovery = Mockito.mock(ServiceDiscovery.class);\n'
             '    listener = new ServiceInstancesChangedListener(serviceNames, serviceDiscovery);\n'
             '    ServiceInstancesChangedListener spyListener = Mockito.spy(listener);\n'
             '    Mockito.doReturn(null).when(metadataService).getMetadataInfo(eq(null));\n'
             '    ServiceInstancesChangedEvent event =\n'
             '        new ServiceInstancesChangedEvent("app1", app1InstancesWithNoRevision);\n'
             '    spyListener.onEvent(event);\n'
             '    assertTrue(true);\n'
             '}')],
         after=[("SUCCESS -- accepted patch (real diff, applied in both test classes)",
             '// new file: ServiceInstancesChangedListenerTestHelper.java\n'
             'static void stubMetadataInfoForNullRevision(MetadataService metadataService) {\n'
             '    Mockito.doReturn(null).when(metadataService).getMetadataInfo(eq(null));\n'
             '}\n\n'
             '// call site:\n'
             'ServiceInstancesChangedListenerTestHelper.stubMetadataInfoForNullRevision(metadataService);')],
         caveat_en="The stubbed static metadataService does not appear directly connected to the "
                    "locally created ServiceDiscovery in these methods; this refactor intentionally "
                    "preserves the existing stubbing and invocation timing exactly.",
         caveat_zh="被 stub 的静态 metadataService 在这些方法里看不出和局部创建的 ServiceDiscovery "
                    "有直接联系；这次重构刻意原样保留了现有的 stub 调用和时机，不做改变。",
         verdict_en="Same reservation the model always had about this MCI &mdash; the mock still "
                     "looks disconnected. This time it wasn't asked to resolve that question, only to "
                     "move the statement without changing what it does. That's unconditionally safe "
                     "regardless of whether metadataService matters, and it compiled and passed.",
         verdict_zh="模型对这个 MCI 的顾虑和以前一样&mdash;&mdash;这个 mock 看起来还是没接上。但这次它"
                     "不需要回答“该不该修”，只需要把这条语句原样搬走，行为完全不变。不管 "
                     "metadataService 有没有意义，这个动作本身都是安全的，编译测试都过了。",
         extra_seqs=[]),

    dict(fkey="5", mci="CompositeConfiguration::1", group="ok",
         files="RegistryProtocolTest.java &mdash; 7 test methods (full method shown; the other 6 "
               "are the same shape)",
         before=[(None,
             '/**\n * verify the generated consumer url information\n */\n'
             '@Test\n'
             'void testConsumerUrlWithoutProtocol() {\n'
             '    ApplicationConfig applicationConfig = new ApplicationConfig();\n'
             '    applicationConfig.setName("application1");\n'
             '    ConfigManager configManager = mock(ConfigManager.class);\n'
             '    when(configManager.getApplicationOrElseThrow()).thenReturn(applicationConfig);\n'
             '    CompositeConfiguration compositeConfiguration = mock(CompositeConfiguration.class);\n'
             '    when(compositeConfiguration.convert(Boolean.class, ENABLE_CONFIGURATION_LISTEN, true))\n'
             '        .thenReturn(true);\n'
             '    Map<String, String> parameters = new HashMap<>();\n'
             '    parameters.put(INTERFACE_KEY, DemoService.class.getName());\n'
             '    parameters.put("registry", "zookeeper");\n'
             '    parameters.put("register", "false");\n'
             '    parameters.put(REGISTER_IP_KEY, "172.23.236.180");\n'
             '    Map<String, Object> attributes = new HashMap<>();\n'
             '    ServiceConfigURL serviceConfigURL = new ServiceConfigURL(\n'
             '        "registry", "127.0.0.1", 2181,\n'
             '        "org.apache.dubbo.registry.RegistryService", parameters);\n'
             '    Map<String, String> refer = new HashMap<>();\n'
             '    attributes.put(REFER_KEY, refer);\n'
             '    attributes.put("key1", "value1");\n'
             '    URL url = serviceConfigURL.addAttributes(attributes);\n'
             '    RegistryFactory registryFactory = mock(RegistryFactory.class);\n'
             '    Registry registry = mock(Registry.class);\n'
             '    RegistryProtocol registryProtocol = new RegistryProtocol();\n'
             '    MigrationRuleListener migrationRuleListener = mock(MigrationRuleListener.class);\n'
             '    List<RegistryProtocolListener> registryProtocolListeners = new ArrayList<>();\n'
             '    registryProtocolListeners.add(migrationRuleListener);\n'
             '    ModuleModel moduleModel =\n'
             '        Mockito.spy(ApplicationModel.defaultModel().getDefaultModule());\n'
             '    moduleModel.getApplicationModel().getApplicationConfigManager()\n'
             '        .setApplication(new ApplicationConfig("application1"));\n'
             '    ExtensionLoader<RegistryProtocolListener> extensionLoaderMock =\n'
             '        mock(ExtensionLoader.class);\n'
             '    Mockito.when(moduleModel.getExtensionLoader(RegistryProtocolListener.class))\n'
             '        .thenReturn(extensionLoaderMock);\n'
             '    Mockito.when(extensionLoaderMock.getActivateExtension(url, REGISTRY_PROTOCOL_LISTENER_KEY))\n'
             '        .thenReturn(registryProtocolListeners);\n'
             '    url = url.setScopeModel(moduleModel);\n'
             '    when(registryFactory.getRegistry(registryProtocol.getRegistryUrl(url)))\n'
             '        .thenReturn(registry);\n'
             '    Cluster cluster = mock(Cluster.class);\n'
             '    Invoker<?> invoker =\n'
             '        registryProtocol.doRefer(cluster, registry, DemoService.class, url, parameters);\n'
             '    Assertions.assertTrue(invoker instanceof MigrationInvoker);\n'
             '    URL consumerUrl = ((MigrationInvoker<?>) invoker).getConsumerUrl();\n'
             '    Assertions.assertTrue((consumerUrl != null));\n'
             '    // verify that the protocol header of consumerUrl is set to "consumer"\n'
             '    Assertions.assertEquals("consumer", consumerUrl.getProtocol());\n'
             '    Assertions.assertEquals(parameters.get(REGISTER_IP_KEY), consumerUrl.getHost());\n'
             '    Assertions.assertFalse(consumerUrl.getAttributes().containsKey(REFER_KEY));\n'
             '    Assertions.assertEquals("value1", consumerUrl.getAttribute("key1"));\n'
             '}')],
         after=[("SUCCESS -- accepted patch (real diff, same file, all 7 call sites)",
             'private void enableConfigurationListening(CompositeConfiguration compositeConfiguration) {\n'
             '    when(compositeConfiguration.convert(Boolean.class, ENABLE_CONFIGURATION_LISTEN, true))\n'
             '        .thenReturn(true);\n'
             '}\n\n'
             '// each of the 7 call sites becomes:\n'
             'CompositeConfiguration compositeConfiguration = mock(CompositeConfiguration.class);\n'
             'enableConfigurationListening(compositeConfiguration);')],
         caveat_en="The CompositeConfiguration mocks are retained as local variables exactly as "
                    "before, even though their connection to the code under test is not visible in "
                    "this source alone.",
         caveat_zh="CompositeConfiguration 的 Mock 还是照原样保留为局部变量；虽然单看这份源码看不出"
                    "它和被测代码有什么联系，但这次没有因此不做。",
         verdict_en="Same &ldquo;looks disconnected&rdquo; observation, same outcome: extract the "
                     "identical statement into a same-file private method, seven times, don't try to "
                     "decide whether it matters. Compiled, tests passed, across all 7 call sites.",
         verdict_zh="又是一句“看起来没接上”，结果还是一样：把这条完全相同的语句提取成同文件"
                     "内的私有方法，7 处全部照做，不去评判它是否“有意义”。编译测试全过。",
         extra_seqs=[]),

    dict(fkey="6", mci="ConfigManager::1", group="ok",
         files="RegistryProtocolTest.java &mdash; same file, same 7 test methods as CompositeConfiguration::1 "
               "(same method shown in full above; repeated here for the ConfigManager half)",
         before=[(None,
             '/**\n * verify the generated consumer url information\n */\n'
             '@Test\n'
             'void testConsumerUrlWithoutProtocol() {\n'
             '    ApplicationConfig applicationConfig = new ApplicationConfig();\n'
             '    applicationConfig.setName("application1");\n'
             '    ConfigManager configManager = mock(ConfigManager.class);\n'
             '    when(configManager.getApplicationOrElseThrow()).thenReturn(applicationConfig);\n'
             '    CompositeConfiguration compositeConfiguration = mock(CompositeConfiguration.class);\n'
             '    when(compositeConfiguration.convert(Boolean.class, ENABLE_CONFIGURATION_LISTEN, true))\n'
             '        .thenReturn(true);\n'
             '    Map<String, String> parameters = new HashMap<>();\n'
             '    parameters.put(INTERFACE_KEY, DemoService.class.getName());\n'
             '    parameters.put("registry", "zookeeper");\n'
             '    parameters.put("register", "false");\n'
             '    parameters.put(REGISTER_IP_KEY, "172.23.236.180");\n'
             '    Map<String, Object> attributes = new HashMap<>();\n'
             '    ServiceConfigURL serviceConfigURL = new ServiceConfigURL(\n'
             '        "registry", "127.0.0.1", 2181,\n'
             '        "org.apache.dubbo.registry.RegistryService", parameters);\n'
             '    Map<String, String> refer = new HashMap<>();\n'
             '    attributes.put(REFER_KEY, refer);\n'
             '    attributes.put("key1", "value1");\n'
             '    URL url = serviceConfigURL.addAttributes(attributes);\n'
             '    RegistryFactory registryFactory = mock(RegistryFactory.class);\n'
             '    Registry registry = mock(Registry.class);\n'
             '    RegistryProtocol registryProtocol = new RegistryProtocol();\n'
             '    MigrationRuleListener migrationRuleListener = mock(MigrationRuleListener.class);\n'
             '    List<RegistryProtocolListener> registryProtocolListeners = new ArrayList<>();\n'
             '    registryProtocolListeners.add(migrationRuleListener);\n'
             '    ModuleModel moduleModel =\n'
             '        Mockito.spy(ApplicationModel.defaultModel().getDefaultModule());\n'
             '    moduleModel.getApplicationModel().getApplicationConfigManager()\n'
             '        .setApplication(new ApplicationConfig("application1"));\n'
             '    ExtensionLoader<RegistryProtocolListener> extensionLoaderMock =\n'
             '        mock(ExtensionLoader.class);\n'
             '    Mockito.when(moduleModel.getExtensionLoader(RegistryProtocolListener.class))\n'
             '        .thenReturn(extensionLoaderMock);\n'
             '    Mockito.when(extensionLoaderMock.getActivateExtension(url, REGISTRY_PROTOCOL_LISTENER_KEY))\n'
             '        .thenReturn(registryProtocolListeners);\n'
             '    url = url.setScopeModel(moduleModel);\n'
             '    when(registryFactory.getRegistry(registryProtocol.getRegistryUrl(url)))\n'
             '        .thenReturn(registry);\n'
             '    Cluster cluster = mock(Cluster.class);\n'
             '    Invoker<?> invoker =\n'
             '        registryProtocol.doRefer(cluster, registry, DemoService.class, url, parameters);\n'
             '    Assertions.assertTrue(invoker instanceof MigrationInvoker);\n'
             '    URL consumerUrl = ((MigrationInvoker<?>) invoker).getConsumerUrl();\n'
             '    Assertions.assertTrue((consumerUrl != null));\n'
             '    // verify that the protocol header of consumerUrl is set to "consumer"\n'
             '    Assertions.assertEquals("consumer", consumerUrl.getProtocol());\n'
             '    Assertions.assertEquals(parameters.get(REGISTER_IP_KEY), consumerUrl.getHost());\n'
             '    Assertions.assertFalse(consumerUrl.getAttributes().containsKey(REFER_KEY));\n'
             '    Assertions.assertEquals("value1", consumerUrl.getAttribute("key1"));\n'
             '}')],
         after=[("SUCCESS -- accepted patch (real diff, all 7 call sites)",
             'private static ConfigManager createConfigManager(ApplicationConfig applicationConfig) {\n'
             '    ConfigManager configManager = mock(ConfigManager.class);\n'
             '    when(configManager.getApplicationOrElseThrow()).thenReturn(applicationConfig);\n'
             '    return configManager;\n'
             '}\n\n'
             '// each call site becomes:\n'
             'ConfigManager configManager = createConfigManager(applicationConfig);')],
         caveat_en="The created ConfigManager variables appear unused in the supplied test source; "
                    "this refactor deliberately preserves their creation, stubbing, scope, and "
                    "per-test instance behavior rather than removing them.",
         caveat_zh="创建出来的 ConfigManager 变量在提供的测试源码里看着没被用到；这次重构刻意保留了"
                    "它们的创建、stub、作用域和逐测试独立实例的行为，而不是直接删掉。",
         verdict_en="The model explicitly considered removing the unused mock and chose not to "
                     "&mdash; deletion would be a behavior change it wasn't asked to make. It picked "
                     "the smaller, unconditionally safe move: a factory method. Passed.",
         verdict_zh="模型明确考虑过直接删掉这个没用到的 mock，但没有这么做&mdash;&mdash;删除会改变行为，"
                     "而这不是它被要求做的事。它选了更小、绝对安全的动作：抽成工厂方法。通过了验证。",
         extra_seqs=[]),

    dict(fkey="7", mci="org.apache.dubbo.rpc.Invoker::2", group="ok",
         files="MergeableClusterInvokerTest.java &mdash; 4 test methods &times; 2 invokers "
               "(testInvokerToException shown in full)",
         before=[(None,
             '/**\n * test when network exception\n */\n'
             '@Test\n'
             'void testInvokerToException() {\n'
             '    String menu = "first";\n'
             '    List<String> menuItems = new ArrayList<String>() {\n'
             '        {\n'
             '            add("1");\n'
             '            add("2");\n'
             '        }\n'
             '    };\n'
             '    given(invocation.getMethodName()).willReturn("addMenu");\n'
             '    given(invocation.getParameterTypes())\n'
             '        .willReturn(new Class<?>[] { String.class, List.class });\n'
             '    given(invocation.getArguments()).willReturn(new Object[] { menu, menuItems });\n'
             '    given(invocation.getObjectAttachments()).willReturn(new HashMap<>());\n'
             '    given(invocation.getInvoker()).willReturn(firstInvoker);\n'
             '    given(firstInvoker.getUrl()).willReturn(url.addParameter(GROUP_KEY, "first"));\n'
             '    given(firstInvoker.getInterface()).willReturn(MenuService.class);\n'
             '    given(firstInvoker.invoke(invocation)).willReturn(new AppResponse());\n'
             '    given(firstInvoker.isAvailable()).willReturn(true);\n'
             '    given(firstInvoker.invoke(invocation))\n'
             '        .willThrow(new RpcException(RpcException.NETWORK_EXCEPTION));\n'
             '    given(secondInvoker.getUrl()).willReturn(url.addParameter(GROUP_KEY, "second"));\n'
             '    given(secondInvoker.getInterface()).willReturn(MenuService.class);\n'
             '    given(secondInvoker.invoke(invocation)).willReturn(new AppResponse());\n'
             '    given(secondInvoker.isAvailable()).willReturn(true);\n'
             '    given(secondInvoker.invoke(invocation))\n'
             '        .willThrow(new RpcException(RpcException.NETWORK_EXCEPTION));\n'
             '    given(directory.list(invocation)).willReturn(new ArrayList() {\n'
             '        {\n'
             '            add(firstInvoker);\n'
             '            add(secondInvoker);\n'
             '        }\n'
             '    });\n'
             '    given(directory.getUrl()).willReturn(url);\n'
             '    given(directory.getConsumerUrl()).willReturn(url);\n'
             '    given(directory.getConsumerUrl()).willReturn(url);\n'
             '    given(directory.getInterface()).willReturn(MenuService.class);\n'
             '    mergeableClusterInvoker = new MergeableClusterInvoker<MenuService>(directory);\n'
             '    // invoke\n'
             '    try {\n'
             '        Result result = mergeableClusterInvoker.invoke(invocation);\n'
             '        fail();\n'
             '        Assertions.assertNull(result.getValue());\n'
             '    } catch (RpcException expected) {\n'
             '        assertEquals(expected.getCode(), RpcException.NETWORK_EXCEPTION);\n'
             '    }\n'
             '}')],
         after=[("SUCCESS -- accepted patch (real diff)",
             'private void configureAvailableInvoker(Invoker invoker, String group) {\n'
             '    given(invoker.getUrl()).willReturn(url.addParameter(GROUP_KEY, group));\n'
             '    given(invoker.getInterface()).willReturn(MenuService.class);\n'
             '    given(invoker.invoke(invocation)).willReturn(new AppResponse());\n'
             '    given(invoker.isAvailable()).willReturn(true);\n'
             '}\n\n'
             '// in exception tests, the re-stub stays OUTSIDE the helper, untouched:\n'
             'configureAvailableInvoker(firstInvoker, "first");\n'
             'given(firstInvoker.invoke(invocation))\n'
             '        .willThrow(new RpcException(RpcException.NETWORK_EXCEPTION));')],
         caveat_en="The helper deliberately leaves the later invoke(...).willThrow(...) statements "
                    "in place so Mockito's final exception behavior and stubbing order remain "
                    "unchanged.",
         caveat_zh="这个 helper 特意把后面的 invoke(...).willThrow(...) 留在外面没动，这样 Mockito "
                    "最终生效的异常行为和 stub 顺序才不会变。",
         verdict_en="This is the most precise of the six: the model split the pattern at exactly the "
                     "line where stubbing order starts to matter, extracted only the part before it, "
                     "and said so. The concern from the original static reading (stub-order safety) "
                     "didn't need a runtime check after all &mdash; it just needed a narrower cut.",
         verdict_zh="这是六个里做得最精确的一个：模型精准地在“stub 顺序开始有影响”的那一行"
                     "把模式切开，只提取前面安全的部分，并且说明了原因。最初静态阅读时的顾虑（stub "
                     "顺序安全性）其实不需要真正跑起来验证&mdash;&mdash;只是需要切得更窄一点。",
         extra_seqs=[]),

    dict(fkey="8", mci="org.apache.dubbo.registry.NotifyListener::2", group="ok",
         files="ServiceInstancesChangedListenerTest.java + ...WithoutEmptyProtectTest.java &mdash; "
               "22 sequences across 6 test methods (testServiceListenerNotification shown in full)",
         before=[(None,
             '// normal case: check instance listener -> service listener (Directory) address push flow\n'
             '@Test\n@Order(5)\n'
             'public void testServiceListenerNotification() {\n'
             '    Set<String> serviceNames = new HashSet<>();\n'
             '    serviceNames.add("app1");\n'
             '    serviceNames.add("app2");\n'
             '    listener = new ServiceInstancesChangedListener(serviceNames, serviceDiscovery);\n'
             '    NotifyListener demoServiceListener = Mockito.mock(NotifyListener.class);\n'
             '    when(demoServiceListener.getConsumerUrl()).thenReturn(consumerURL);\n'
             '    NotifyListener demoService2Listener = Mockito.mock(NotifyListener.class);\n'
             '    when(demoService2Listener.getConsumerUrl()).thenReturn(consumerURL2);\n'
             '    listener.addListenerAndNotify(consumerURL, demoServiceListener);\n'
             '    listener.addListenerAndNotify(consumerURL2, demoService2Listener);\n'
             '    // notify app1 instance change\n'
             '    ServiceInstancesChangedEvent app1_event =\n'
             '        new ServiceInstancesChangedEvent("app1", app1Instances);\n'
             '    listener.onEvent(app1_event);\n'
             '    // check\n'
             '    ArgumentCaptor<List<URL>> captor = ArgumentCaptor.forClass(List.class);\n'
             '    Mockito.verify(demoServiceListener, Mockito.times(1)).notify(captor.capture());\n'
             '    List<URL> notifiedUrls = captor.getValue();\n'
             '    Assertions.assertEquals(3, notifiedUrls.size());\n'
             '    ArgumentCaptor<List<URL>> captor2 = ArgumentCaptor.forClass(List.class);\n'
             '    Mockito.verify(demoService2Listener, Mockito.times(1)).notify(captor2.capture());\n'
             '    List<URL> notifiedUrls2 = captor2.getValue();\n'
             '    Assertions.assertEquals(0, notifiedUrls2.size());\n'
             '    // notify app2 instance change\n'
             '    ServiceInstancesChangedEvent app2_event =\n'
             '        new ServiceInstancesChangedEvent("app2", app2Instances);\n'
             '    listener.onEvent(app2_event);\n'
             '    // check\n'
             '    ArgumentCaptor<List<URL>> app2_captor = ArgumentCaptor.forClass(List.class);\n'
             '    Mockito.verify(demoServiceListener, Mockito.times(2)).notify(app2_captor.capture());\n'
             '    List<URL> app2_notifiedUrls = app2_captor.getValue();\n'
             '    Assertions.assertEquals(7, app2_notifiedUrls.size());\n'
             '    ArgumentCaptor<List<URL>> app2_captor2 = ArgumentCaptor.forClass(List.class);\n'
             '    Mockito.verify(demoService2Listener, Mockito.times(2)).notify(app2_captor2.capture());\n'
             '    List<URL> app2_notifiedUrls2 = app2_captor2.getValue();\n'
             '    Assertions.assertEquals(4, app2_notifiedUrls2.size());\n'
             '    // test service listener still get notified when added after instance notification.\n'
             '    NotifyListener demoService3Listener = Mockito.mock(NotifyListener.class);\n'
             '    when(demoService3Listener.getConsumerUrl()).thenReturn(consumerURL3);\n'
             '    listener.addListenerAndNotify(consumerURL3, demoService3Listener);\n'
             '    Mockito.verify(demoService3Listener, Mockito.times(1)).notify(Mockito.anyList());\n'
             '}')],
         after=[("SUCCESS -- accepted patch: a new shared file, used by both test classes",
             '// new file: NotifyListenerTestFixture.java\n'
             'static NotifyListener createFor(URL consumerUrl) {\n'
             '    NotifyListener listener = Mockito.mock(NotifyListener.class);\n'
             '    Mockito.when(listener.getConsumerUrl()).thenReturn(consumerUrl);\n'
             '    return listener;\n'
             '}\n\n'
             '// call sites in both test classes:\n'
             'NotifyListener demoServiceListener = NotifyListenerTestFixture.createFor(consumerURL);')],
         caveat_en="The supplied classes contain broader duplicated test code, but this refactor is "
                    "intentionally limited to the selected NotifyListener mock clone.",
         caveat_zh="提供的这些类里还有更多别的重复测试代码，但这次重构刻意只处理选中的这一个 "
                    "NotifyListener mock clone，不做范围外的事。",
         verdict_en="This is the case that used to need a tool change, not a model change &mdash; "
                     "the model always said the fix was clear, it just couldn't create the shared "
                     "file. Once file creation was allowed, it did exactly what it had already "
                     "described: one shared fixture, both test classes.",
         verdict_zh="这个案例以前需要的是工具能力的改变，而不是模型判断的改变&mdash;&mdash;模型一直"
                     "都说方案很清楚，只是当时不能新建共享文件。放开这个限制之后，它就做了它早就描述过"
                     "的方案：一个共享 fixture，两个测试类一起用。",
         extra_seqs=[]),

    dict(fkey="10", mci="NamingService::4", group="ok",
         files="NacosNamingServiceWrapperTest.java &mdash; 6 sequences, including inside anonymous "
               "createNamingService() overrides (testSubscribeMultiManager shown in full)",
         before=[(None,
             '@Test\n'
             'void testSubscribeMultiManager() throws NacosException {\n'
             '    NacosConnectionManager connectionManager = Mockito.mock(NacosConnectionManager.class);\n'
             '    NamingService namingService1 = Mockito.mock(NamingService.class);\n'
             '    NamingService namingService2 = Mockito.mock(NamingService.class);\n'
             '    NacosNamingServiceWrapper nacosNamingServiceWrapper =\n'
             '        new NacosNamingServiceWrapper(connectionManager, 0, 0);\n'
             '    EventListener eventListener = Mockito.mock(EventListener.class);\n'
             '    Mockito.when(connectionManager.getNamingService()).thenReturn(namingService1);\n'
             '    nacosNamingServiceWrapper.subscribe("service_name", "test", eventListener);\n'
             '    Mockito.verify(namingService1, Mockito.times(1))\n'
             '        .subscribe("service_name", "test", eventListener);\n'
             '    Mockito.when(connectionManager.getNamingService()).thenReturn(namingService2);\n'
             '    nacosNamingServiceWrapper.subscribe("service_name", "test", eventListener);\n'
             '    Mockito.verify(namingService1, Mockito.times(2))\n'
             '        .subscribe("service_name", "test", eventListener);\n'
             '    nacosNamingServiceWrapper.unsubscribe("service_name", "test", eventListener);\n'
             '    Mockito.verify(namingService1, Mockito.times(1))\n'
             '        .unsubscribe("service_name", "test", eventListener);\n'
             '    nacosNamingServiceWrapper.unsubscribe("service_name", "test", eventListener);\n'
             '    Mockito.verify(namingService1, Mockito.times(1))\n'
             '        .unsubscribe("service_name", "test", eventListener);\n'
             '    nacosNamingServiceWrapper.unsubscribe("service_name", "mock", eventListener);\n'
             '    Mockito.verify(namingService1, Mockito.times(0))\n'
             '        .unsubscribe("service_name", "mock", eventListener);\n'
             '    Mockito.verify(namingService2, Mockito.times(0))\n'
             '        .unsubscribe("service_name", "mock", eventListener);\n'
             '}')],
         after=[("SUCCESS -- accepted patch: a private factory, reused at all 6 sites",
             'private NamingService mockNamingService() {\n'
             '    return Mockito.mock(NamingService.class);\n'
             '}\n\n'
             '// e.g.:\n'
             'NamingService namingService1 = mockNamingService();\n'
             'NamingService namingService2 = mockNamingService();')],
         caveat_en="The replacement also updates the two additional NamingService mock creation "
                    "sites in the same class (namingService2 and the Nacos 2.1 client/2.0 server "
                    "test), which use the same unconfigured Mockito mock expression and preserve "
                    "behavior.",
         caveat_zh="这次替换还顺带更新了同一个类里另外两处 NamingService mock 创建点（namingService2，"
                    "以及 Nacos 2.1 客户端/2.0 服务端那个测试），它们用的是同样未配置的 Mockito mock "
                    "表达式，行为不受影响。",
         verdict_en="The earlier objection was about merging <em>configured</em> mocks representing "
                     "different backends. The model sidestepped that question entirely: it only "
                     "extracted the bare, unconfigured <code>mock(NamingService.class)</code> call "
                     "&mdash; identical no matter which backend the caller later wires up &mdash; and "
                     "applied that narrow fix everywhere it occurs, not just the two originally "
                     "compared.",
         verdict_zh="以前的顾虑是关于合并两个<em>已配置成不同后端</em>的 mock。模型这次完全绕开了这个"
                     "问题：它只提取了裸的、未配置的 <code>mock(NamingService.class)</code> "
                     "调用&mdash;&mdash;不管调用方后面把它配成哪个后端，这一行都完全一样&mdash;&mdash;"
                     "并且把这个小范围的修复用到了它出现的所有地方，不只是最初比较的那两处。",
         extra_seqs=[]),

    dict(fkey="2", mci="java.net.InetAddress::2", group="fail",
         files="NetUtilsTest.java",
         before=[(None,
             '@Test\n'
             'void testIsValidAddress() {\n'
             '    assertFalse(NetUtils.isValidV4Address((InetAddress) null));\n'
             '    InetAddress address = mock(InetAddress.class);\n'
             '    when(address.isLoopbackAddress()).thenReturn(true);\n'
             '    assertFalse(NetUtils.isValidV4Address(address));\n'
             '    address = mock(InetAddress.class);\n'
             '    when(address.getHostAddress()).thenReturn("localhost");\n'
             '    assertFalse(NetUtils.isValidV4Address(address));\n'
             '    address = mock(InetAddress.class);\n'
             '    when(address.getHostAddress()).thenReturn("0.0.0.0");\n'
             '    assertFalse(NetUtils.isValidV4Address(address));\n'
             '    address = mock(InetAddress.class);\n'
             '    when(address.getHostAddress()).thenReturn("127.0.0.1");\n'
             '    assertFalse(NetUtils.isValidV4Address(address));\n'
             '    address = mock(InetAddress.class);\n'
             '    when(address.getHostAddress()).thenReturn("1.2.3.4");\n'
             '    assertTrue(NetUtils.isValidV4Address(address));  // the one branch that returns true\n'
             '}')],
         after=[],
         caveat_en=None,
         caveat_zh=None,
         verdict_en="No caveat is on record for this one &mdash; this run was reconstructed from an "
                     "interrupted process's log rather than a normal report, so the model's own "
                     "reasoning text didn't survive. What we do have is the harness's classification: "
                     "<strong>FAILED_BEHAVIORAL_EQUIVALENCE</strong> &mdash; a test that passed before "
                     "the change no longer matches after it. Given the source above (five reassignments "
                     "of the same variable, five deliberately different stub combos, one tuned to hit "
                     "the single true-returning branch), that's exactly the failure mode this pattern "
                     "invites if a merge touches the wrong assignment.",
         verdict_zh="这个案例没有留下 caveat 记录&mdash;&mdash;这一条是从一次被中断的进程日志里"
                     "重建出来的，不是正常的报告产出，模型自己的推理文字没能保留下来。留下来的是 "
                     "harness 的判定结果：<strong>FAILED_BEHAVIORAL_EQUIVALENCE</strong>"
                     "&mdash;&mdash;改动前能过的测试，改动后对不上了。结合上面的源码看（同一个变量"
                     "连续重新赋值 5 次，5 种刻意不同的 stub 组合，其中一种专门用来命中唯一返回 true "
                     "的分支），如果合并动到了错的那次赋值，这正是这种写法最容易触发的失败方式。",
         extra_seqs=[]),

    dict(fkey="13", mci="Directory&lt;DemoService&gt;::1", group="fail",
         files="FailSafeClusterInvokerTest.java",
         before=[(None,
             '@Test\n'
             'void testNoInvoke() {\n'
             '    dic = mock(Directory.class);\n'
             '    given(dic.getUrl()).willReturn(url);\n'
             '    given(dic.getConsumerUrl()).willReturn(url);\n'
             '    given(dic.list(invocation)).willReturn(null);\n'
             '    given(dic.getInterface()).willReturn(DemoService.class);\n'
             '    invocation.setMethodName("method1");\n'
             '    resetInvokerToNoException();\n'
             '    FailsafeClusterInvoker<DemoService> invoker = new FailsafeClusterInvoker<DemoService>(dic);\n'
             '    try {\n'
             '        invoker.invoke(invocation);\n'
             '    } catch (RpcException e) {\n'
             '        Assertions.assertTrue(e.getMessage().contains("No provider available"));\n'
             '        assertFalse(e.getCause() instanceof RpcException);\n'
             '    }\n'
             '}')],
         after=[("FAILED_REFACTORING_GOAL -- accepted by the harness, rejected by the goal-check",
             '@Test\nvoid testNoInvoke() {\n'
             '    dic = mock(Directory.class);\n'
             '    DirectoryTestFixture.stubMetadata(dic, url, DemoService.class);\n'
             '    given(dic.list(invocation)).willReturn(null);\n'
             '    // getUrl/getConsumerUrl/getInterface consolidated into stubMetadata();\n'
             '    // "dic = mock(Directory.class);" itself is untouched, and still repeats\n'
             '    // in BroadCastClusterInvokerTest\'s setUp()\n'
             '}\n\n'
             'final class DirectoryTestFixture {\n'
             '    static <T> void stubMetadata(Directory<T> directory, URL url, Class<T> type) {\n'
             '        given(directory.getUrl()).willReturn(url);\n'
             '        given(directory.getConsumerUrl()).willReturn(url);\n'
             '        given(directory.getInterface()).willReturn(type);\n'
             '    }\n'
             '}')],
         caveat_en="In testNoInvoke, the interface stub is now registered before the null list stub "
                    "rather than after it. These stubs target different methods, so Mockito behavior "
                    "is unchanged.",
         caveat_zh="在 testNoInvoke 里，interface 的 stub 现在注册在 null list 的 stub 之前而不是"
                    "之后。这两个 stub 针对不同方法，所以 Mockito 的行为不受影响。",
         verdict_en="Compiled, tests passed &mdash; the model's own caveat (about stub order) checked "
                     "out fine. What actually failed is a separate, mechanical check: the goal-check "
                     "counts how many times the MCI's flagged duplicate line occurs before and after. "
                     "&ldquo;dic = mock(Directory.class);&rdquo; &mdash; the line the MCI was actually "
                     "about &mdash; still occurs twice, unchanged, because it also appears in a sibling "
                     "test class's setUp() that this patch never touched. Good partial refactor, "
                     "incomplete by the harness's own count.",
         verdict_zh="编译测试都过了&mdash;&mdash;模型自己关于 stub 顺序的顾虑经核实是没问题的。真正"
                     "失败的是另一项独立的、纯机械的检查：goal-check 会数这个 MCI 标记的重复行在改动"
                     "前后各出现几次。而“dic = mock(Directory.class);”&mdash;&mdash;这个 MCI "
                     "真正针对的那一行&mdash;&mdash;改动前后都还是 2 次，因为它还出现在这次改动完全"
                     "没碰过的另一个测试类的 setUp() 里。是个不错的部分重构，但按 harness 自己的计数"
                     "标准，没有做完。",
         extra_seqs=[]),
]

print("stage3 cases2 defined", len(CASES2))


def render_code2(heading, java_src, key):
    parts = []
    if heading:
        parts.append(f'<p class="code-label">{heading}</p>')
    parts.append(f'<pre class="code"><code class="hlj">{hl(java_src)}</code></pre>')
    return "\n".join(parts)


def pick_extra_sequences(fkey, shown_methods, n=3):
    """Pick up to n more sequences from this MCI (beyond the ones already
    shown in full), dedup by (class, method), for the expand panel's
    'a few more, in code' examples."""
    seqs = FULL[fkey]["sequences"]
    seen_methods = set(shown_methods)
    picked, seen_pair = [], set()
    for s in seqs:
        pair = (s["className"], s["testMethodName"])
        if pair in seen_pair or s["testMethodName"] in seen_methods:
            continue
        seen_pair.add(pair)
        picked.append(s)
        if len(picked) >= n:
            break
    return picked


def render_extra_seq(s):
    lines = sorted(s["testMockLines"].items(), key=lambda kv: int(kv[0]))
    code = "\n".join(txt.splitlines()[-1] if "\n" in txt else txt for _, txt in lines)
    heading = f'{esc(s["className"])}.{esc(s["testMethodName"])}()'
    return (f'<div class="extra-seq"><p class="extra-seq-h">{heading}</p>'
            f'<pre class="code code-tight"><code class="hlj">{hl(code)}</code></pre></div>')


def render_seq_table(fkey):
    seqs = FULL[fkey]["sequences"]
    rows = []
    for i, s in enumerate(seqs, 1):
        lines = sorted(s["testMockLines"].items(), key=lambda kv: int(kv[0]))
        stmt_html = "".join(
            f'<div class="stmt"><span class="ln">L{esc(ln)}</span>'
            f'<code>{esc(txt.splitlines()[-1].strip())}</code></div>'
            for ln, txt in lines
        )
        rows.append(
            f'<tr><td class="seqno">{i}</td>'
            f'<td class="seqclass">{esc(s["className"])}</td>'
            f'<td class="seqmethod">{esc(s["testMethodName"])}</td>'
            f'<td class="seqstmt">{stmt_html}</td></tr>'
        )
    return "\n".join(rows)


def render_case2(c, idx):
    fkey = c["fkey"]
    full = FULL[fkey]
    total_seq = full["sequenceCount"]
    total_methods = full["testCaseCount"]
    cid = f"pcase-{idx}"

    before_html = "\n".join(render_code2(h, s, cid) for h, s in c["before"])
    after_html = "\n".join(render_code2(h, s, cid) for h, s in c["after"])

    if c["caveat_en"]:
        caveat_html = f'''
        <div class="comment comment-caveat">
          <div class="comment-label">{B("MODEL CAVEAT","模型的顾虑（modelCaveat）")}</div>
          <p class="quote-en">&ldquo;{c["caveat_en"]}&rdquo;</p>
          <p class="quote-zh lang-zh" hidden>{c["caveat_zh"]}</p>
        </div>'''
    else:
        caveat_html = f'''
        <div class="comment comment-missing">
          <div class="comment-label">{B("NO CAVEAT ON RECORD","没有留下 modelCaveat 记录")}</div>
          <p>{B(
            "This run was reconstructed from an interrupted process's log; the model's own "
            "reasoning text was not preserved.",
            "这一条是从一次被中断的进程日志里重建出来的；模型自己的推理文字没能保留下来。"
          )}</p>
        </div>'''

    if c["group"] == "ok":
        result_txt = B("SUCCESS", "SUCCESS（成功）")
    else:
        result_txt = B("FAILED", "FAILED（失败）")

    verdict_cls = "verdict-ok" if c["group"] == "ok" else "verdict-fail"
    extra = pick_extra_sequences(fkey, [], n=3)
    extra_html = "\n".join(render_extra_seq(s) for s in extra)

    table_html = render_seq_table(fkey)

    return f'''
    <article class="case2 {"case2-ok" if c["group"]=="ok" else "case2-fail"}" id="{cid}">
      <header class="case-head">
        <span class="pill {"pill-ok" if c["group"]=="ok" else "pill-fail"}">{result_txt}</span>
        <span class="pill pill-muted">{total_seq} {B("sequences","个 sequence")} &middot; {total_methods} {B("methods","个测试方法")}</span>
      </header>
      <h3 class="case-mci"><code>{c["mci"]}</code></h3>
      <p class="case-file">{c["files"]}</p>
      <p class="code-label">{B("Before","改动前")}</p>
      {before_html}
      {after_html}
      {caveat_html}
      <p class="verdict {verdict_cls}"><span class="lang-en">{c["verdict_en"]}</span><span class="lang-zh" hidden>{c["verdict_zh"]}</span></p>
      <details class="all-seqs">
        <summary>{B(f"Show all {total_seq} sequences in this MCI", f"展开这个 MCI 的全部 {total_seq} 个 sequence")}</summary>
        <p class="all-seqs-sub">{B("A few more, in code","再看几个，仍然是代码")}</p>
        <div class="extra-seq-grid">{extra_html}</div>
        <p class="all-seqs-sub">{B("Every sequence in this MCI","这个 MCI 的完整列表")}</p>
        <div class="seq-table-wrap"><table class="seq-table">
          <thead><tr><th>#</th><th>{B("Test class","测试类")}</th><th>{B("Test method","测试方法")}</th><th>{B("Duplicated statement(s)","重复的语句")}</th></tr></thead>
          <tbody>{table_html}</tbody>
        </table></div>
      </details>
    </article>'''


ok_cases = [c for c in CASES2 if c["group"] == "ok"]
fail_cases = [c for c in CASES2 if c["group"] == "fail"]

cases2_ok_html = "\n".join(render_case2(c, i) for i, c in enumerate(CASES2) if c["group"] == "ok")
cases2_fail_html = "\n".join(render_case2(c, i) for i, c in enumerate(CASES2) if c["group"] == "fail")

part2_html = f'''
<section class="part" id="part-2">
  <div class="part-head">
    <span class="part-no">II</span>
    <h2>{B("When the Model Hesitates, What Actually Happens","模型有顾虑时，实际会发生什么")}</h2>
  </div>
  <p class="part-dek">{B(
    "The model can attach a note to any candidate &mdash; a <code>modelCaveat</code> &mdash; "
    "flagging something it isn't fully certain about. That note never stops the refactor: "
    "the harness always compiles the result, runs the real tests, and checks mutation "
    "coverage before/after. This section is about what that verification actually found, "
    "for the MCIs where the model had something to say.",
    "模型可以给任何一个候选项附上一条备注&mdash;&mdash;<code>modelCaveat</code>&mdash;&mdash;"
    "标出它不完全确定的地方。但这条备注从不会拦下重构：harness 总是会去编译结果、跑真实测试、"
    "核对改动前后的变异覆盖率。这一节讲的就是，对那些模型有话要说的 MCI，验证结果究竟发现了什么。"
  )}</p>

  <div class="glossary">
    <h4>{B("MCI = one duplicated Mock pattern, not one test","MCI = 一个重复 Mock 模式，不是一个测试")}</h4>
    <p>{B(
      "Each item below is one <strong>MCI</strong> &mdash; Mock Clone Instance &mdash; a single "
      "duplicated Mock-setup pattern the detector found. The same MCI can recur across many test "
      "methods, called sequences (2 to 22 in this set). Each one shows the representative "
      "sequence(s) in full, plus a couple more picked from the rest, still as real highlighted "
      "code; open &ldquo;Show all sequences&rdquo; for the complete list.",
      "下面每一项都是一个 <strong>MCI</strong>&mdash;&mdash;Mock Clone Instance&mdash;&mdash;"
      "检测器找到的一个重复 Mock 配置模式。同一个 MCI 可以在很多测试方法里重复出现，每一次"
      "出现叫一个 sequence（这批数据里从 2 个到 22 个不等）。每一项都完整展示代表性的 "
      "sequence，再从其余的里额外挑几个，同样是真实代码高亮呈现；点开“展开全部 sequence”"
      "能看到完整列表。"
    )}</p>
  </div>

  <div class="stats2">
    <div class="stat2"><div class="n">109</div><div class="l">{B("MCIs refactored","个 MCI 被重构")}</div></div>
    <div class="stat2"><div class="n">7</div><div class="l">{B("carried a modelCaveat","个带有 modelCaveat")}</div></div>
    <div class="stat2ok"><div class="n">6</div><div class="l">{B("caveat, then SUCCESS anyway","有顾虑，仍然成功")}</div></div>
    <div class="stat2fail"><div class="n">2</div><div class="l">{B("genuinely failed","真正失败")}</div></div>
  </div>

  <h3 class="rq-head cat-ok">{B("Where the reservation didn't hold up &mdash; 6 cases","顾虑没有真正成为问题 &mdash; 6 个案例")}</h3>
  <p class="cat-desc">{B(
    "In each of these, the model's caveat echoes almost exactly what it used to justify declining "
    "outright. This time it wasn't asked to settle the underlying question &mdash; only to find a "
    "narrower move that's safe regardless of the answer. It did, every time.",
    "下面这几个案例里，模型的顾虑和它以前用来直接拒绝的理由几乎一模一样。但这次它不需要去回答"
    "背后那个根本问题&mdash;&mdash;只需要找到一个不管答案是什么都安全的、更窄的改法。每一次它都"
    "做到了。"
  )}</p>
  {cases2_ok_html}

  <h3 class="rq-head cat-fail">{B("Where the reservation was right &mdash; 2 cases","顾虑是对的 &mdash; 2 个案例","h3")}</h3>
  <p class="cat-desc">{B(
    "These are the only 2 of 109 MCIs where the harness's own verification &mdash; not a self-report "
    "&mdash; found a real problem.",
    "这是 109 个 MCI 里仅有的 2 个&mdash;&mdash;由 harness 自己的验证（不是模型的自我报告）真正"
    "查出问题的案例。"
  )}</p>
  {cases2_fail_html}
</section>'''

print("stage4 part2 ok", len(part2_html))

# ===========================================================================
# PART 3 -- proposed ablation study (not yet run)
# ===========================================================================
ARMS = [
    ("Arm 1", "Current harness + GPT-4.1", "现有 harness + GPT-4.1",
     "This project's existing harness (isolated verification, repair loop, three-tier "
     "RQ2.1 checks), but with GPT-4.1 instead of the current model. Tests whether the "
     "harness's benefit holds across models, or is specific to the model used so far.",
     "沿用本项目现有的 harness（隔离验证、修复循环、RQ2.1 三层检查），只是把当前用的模型换成 "
     "GPT-4.1。用来检验 harness 带来的收益是不是跨模型都成立，还是只对目前用的这个模型有效。"),
    ("Arm 2", "No harness (original approach)", "不用 harness（原始方案）",
     "The pre-harness baseline: a single model call producing a full refactor proposal "
     "with no isolated verification and no automated repair loop, matching how the "
     "original paper's pipeline operated. The control arm for measuring the harness's "
     "own contribution.",
     "harness 出现之前的基线：模型单次调用直接产出完整重构方案，没有隔离验证，也没有自动修复"
     "循环，对应论文原始流程的做法。作为衡量 harness 本身贡献大小的对照组。"),
    ("Arm 3", "General-purpose coding agents in place of the custom harness", "用通用编程 Agent 替代自建 harness",
     "The same task handed directly to existing frontier coding agents (e.g. Codex) using "
     "their own built-in agentic tool-use loops, instead of this project's purpose-built "
     "harness. Tests whether a custom, domain-specific harness adds value beyond what a "
     "general-purpose coding agent's own verification loop already provides.",
     "把同样的任务直接交给现成的前沿编程 Agent（例如 Codex），用它们自带的工具调用循环去做，"
     "而不是用本项目自建的 harness。用来检验一个领域专用的自建 harness 相对于通用编程 Agent "
     "自带的验证循环，到底还能不能带来额外价值。"),
]

arms_html = "\n".join(f'''
  <div class="arm">
    <h4><span class="lang-en">{a_en}</span><span class="lang-zh" hidden>{a_zh}</span></h4>
    <p class="lang-en">{d_en}</p>
    <p class="lang-zh" hidden>{d_zh}</p>
  </div>''' for _, a_en, a_zh, d_en, d_zh in ARMS)

part3_html = f'''
<section class="part" id="part-3">
  <div class="part-head">
    <span class="part-no">III</span>
    <h2>{B("Proposed Ablation Study","计划中的消融实验")}</h2>
  </div>
  <p class="part-dek">{B(
    "Not yet run. Part I shows the redesigned, harness-verified pipeline beats both the "
    "paper's original prompt-only approach and this project's own pre-redesign protocol. "
    "What it doesn't isolate is how much of that improvement comes from (a) the harness "
    "itself, versus (b) the specific model, versus (c) simply handing the task to a "
    "general-purpose coding agent's own built-in loop. Three arms, same MCI set, for "
    "direct comparability.",
    "还没有跑。第一部分说明了重新设计、由 harness 验证的流程，效果好于论文原本纯 prompt 的"
    "做法，也好于本项目重新设计之前的协议。但这还没有把“改进具体来自哪里”拆开来看："
    "(a) harness 本身，(b) 具体用的模型，还是 (c) 单纯交给一个通用编程 Agent 自带的循环去做。"
    "下面三组，用同一批 MCI，保证可直接比较。"
  )}</p>
  <div class="arms">{arms_html}</div>
  <h3 class="rq-head">{B("What this is designed to answer","这是为了回答什么")}</h3>
  <ol class="answer-list">
    <li>{B(
      "Does a verification harness improve refactoring success/quality at all, and by how "
      "much (Arm 1/3 vs. Arm 2)?",
      "验证型 harness 到底能不能提升重构的成功率/质量，能提升多少（Arm 1/3 对比 Arm 2）？"
    )}</li>
    <li>{B(
      "Is a custom, domain-specific harness worth building, or does a general-purpose "
      "coding agent's own agentic loop already capture most of the benefit (Arm 1 vs. "
      "Arm 3)?",
      "自建一个领域专用的 harness 值不值得，还是通用编程 Agent 自带的循环已经能拿到大部分"
      "收益（Arm 1 对比 Arm 3）？"
    )}</li>
  </ol>
  <p class="note">{B(
    "Open questions before running: which MCI subset to use (all 109, or a fixed sample "
    "for cost control), and whether Arm 3's agents should receive the same structured MCI "
    "context this project's prompt uses, or only the raw source (to isolate agent "
    "capability from prompt engineering).",
    "跑之前还要定下来：用哪个 MCI 子集（全部 109 个，还是为了控制成本取一个固定样本），"
    "以及 Arm 3 的 Agent 要不要拿到和本项目 prompt 一样的结构化 MCI 上下文，还是只给原始"
    "源码（这样才能把“Agent 本身能力”和“prompt 工程”的影响分开）。"
  )}</p>
</section>'''

print("stage5 part3 ok", len(part3_html))

# ===========================================================================
# Nav / TOC
# ===========================================================================
nav_html = f'''
<div class="nav-group">
  <a href="#part-1" class="nav-cat">I. {B("Comparison vs. paper","与论文对比")}</a>
</div>
<div class="nav-group">
  <a href="#part-2" class="nav-cat">II. {B("Model hesitation, in practice","模型的顾虑，实际结果")}</a>
  {"".join(f'<a href="#pcase-{i}" class="nav-case">{c["mci"].split("::")[0].split(".")[-1]}::{c["mci"].split("::")[-1]}</a>' for i, c in enumerate(CASES2))}
</div>
<div class="nav-group">
  <a href="#part-3" class="nav-cat">III. {B("Proposed ablation","计划中的消融实验")}</a>
</div>'''

# ===========================================================================
# CSS
# ===========================================================================
CSS = r"""
:root{
  --bg:#F4F5F8; --surface:#FFFFFF; --surface-2:#EDEFF3; --border:#DDE1E8;
  --ink:#1A1F29; --ink-muted:#5B6472; --ink-faint:#8A93A3;
  --accent:#265E8C; --accent-ink:#154266; --accent-soft:#E6EEF6;
  --fail:#B4530A; --fail-soft:#FBF0E4; --fail-ink:#7A3806;
  --ok:#1F7A4D; --ok-soft:#E7F4ED;
  --code-bg:#F7F8FA; --code-border:#E3E6EC; --code-ink:#232838;
  --code-comment:#6E7887; --code-keyword:#265E8C; --code-type:#7A3E9D;
  --code-string:#1F7A4D; --code-number:#B4530A; --code-annot:#B4530A;
  --chart-paper:#9AA3B2; --chart-ours:#265E8C; --chart-ours2:#1F7A4D;
  --shadow: 0 1px 2px rgba(20,24,32,.04), 0 4px 16px rgba(20,24,32,.05);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#10131A; --surface:#171B24; --surface-2:#1D2230; --border:#2A2F3D;
    --ink:#E7E9EE; --ink-muted:#9AA3B2; --ink-faint:#6B7280;
    --accent:#7CB2E0; --accent-ink:#BFE0FF; --accent-soft:#1D2B3C;
    --fail:#E8A164; --fail-soft:#2E2517; --fail-ink:#F4C495;
    --ok:#71C79A; --ok-soft:#152A20;
    --code-bg:#12151D; --code-border:#262C3A; --code-ink:#E4E7EF;
    --code-comment:#828DA0; --code-keyword:#7CB2E0; --code-type:#CDA0E8;
    --code-string:#82D2A4; --code-number:#E8A164; --code-annot:#E8A164;
    --chart-paper:#5B6472; --chart-ours:#7CB2E0; --chart-ours2:#71C79A;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 6px 20px rgba(0,0,0,.35);
  }
}
:root[data-theme="dark"]{
  --bg:#10131A; --surface:#171B24; --surface-2:#1D2230; --border:#2A2F3D;
  --ink:#E7E9EE; --ink-muted:#9AA3B2; --ink-faint:#6B7280;
  --accent:#7CB2E0; --accent-ink:#BFE0FF; --accent-soft:#1D2B3C;
  --fail:#E8A164; --fail-soft:#2E2517; --fail-ink:#F4C495;
  --ok:#71C79A; --ok-soft:#152A20;
  --code-bg:#12151D; --code-border:#262C3A; --code-ink:#E4E7EF;
  --code-comment:#828DA0; --code-keyword:#7CB2E0; --code-type:#CDA0E8;
  --code-string:#82D2A4; --code-number:#E8A164; --code-annot:#E8A164;
  --chart-paper:#5B6472; --chart-ours:#7CB2E0; --chart-ours2:#71C79A;
  --shadow: 0 1px 2px rgba(0,0,0,.3), 0 6px 20px rgba(0,0,0,.35);
}

*{box-sizing:border-box;}
body{
  background:var(--bg); color:var(--ink);
  font-family:'IBM Plex Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  padding-inline:16px; -webkit-font-smoothing:antialiased;
}
h1,h2,h3{font-family:'IBM Plex Serif',Georgia,serif; text-wrap:balance; margin:0;}
code{font-family:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;}
p{line-height:1.65;}
a{color:var(--accent);}
em{color:inherit;}
.wrap{max-width:1180px; margin:0 auto;}
[hidden]{display:none!important;}

/* ---- lang toggle ---- */
.langbar{
  position:fixed; top:14px; right:16px; z-index:50;
  display:flex; background:var(--surface); border:1px solid var(--border);
  border-radius:99px; box-shadow:var(--shadow); overflow:hidden;
}
.langbar button{
  border:none; background:none; padding:7px 14px; font-size:.8rem; font-weight:600;
  cursor:pointer; color:var(--ink-muted); font-family:'IBM Plex Sans',sans-serif;
}
.langbar button.active{background:var(--accent); color:#fff;}

/* ---- hero ---- */
.hero{padding-block:56px 24px; max-width:920px;}
.hero .kicker{font-size:.75rem; letter-spacing:.08em; text-transform:uppercase; color:var(--accent-ink); font-weight:600; margin-bottom:10px;}
.hero h1{font-size:clamp(1.6rem,3.6vw,2.3rem); font-weight:600; line-height:1.2;}
.hero .dek{color:var(--ink-muted); font-size:1.02rem; margin-top:14px; max-width:66ch; line-height:1.65;}

/* ---- layout ---- */
.layout{display:grid; grid-template-columns:230px 1fr; gap:36px; align-items:start;}
@media (max-width:960px){ .layout{grid-template-columns:1fr;} }
details.toc{position:sticky; top:16px; max-height:calc(100vh - 32px); overflow-y:auto; font-size:.83rem;}
details.toc > summary{display:none;}
.toc-body{padding-right:4px;}
@media (max-width:960px){
  details.toc{position:static; max-height:none; margin-bottom:22px; border:1px solid var(--border); border-radius:8px; background:var(--surface); padding:2px 14px;}
  details.toc > summary{display:block; cursor:pointer; font-weight:600; padding:12px 0; font-size:.9rem; list-style:none;}
  details.toc > summary::-webkit-details-marker{display:none;}
  details.toc > summary::after{content:" \25BE"; color:var(--ink-muted);}
  .toc-body{padding-bottom:12px;}
}
.nav-group{margin-bottom:14px;}
.nav-cat{display:block; font-weight:600; color:var(--ink); text-decoration:none; padding:3px 0;}
.nav-cat:hover{color:var(--accent);}
.nav-case{display:block; color:var(--ink-muted); text-decoration:none; padding:3px 0 3px 14px; font-size:.78rem; border-left:2px solid var(--border); margin-left:2px;}
.nav-case:hover{color:var(--accent); border-left-color:var(--accent);}
main{min-width:0; max-width:900px;}

/* ---- parts ---- */
.part{margin-bottom:52px; scroll-margin-top:16px;}
.part-head{display:flex; align-items:baseline; gap:14px; margin-bottom:8px;}
.part-no{font-family:'IBM Plex Serif',serif; font-size:1.5rem; font-weight:600; color:var(--accent);}
.part h2{font-size:1.5rem; font-weight:600;}
.part-dek{color:var(--ink-muted); max-width:75ch; margin:10px 0 26px; font-size:.98rem;}
.rq-head{font-size:1.12rem; font-weight:600; margin:30px 0 8px; font-family:'IBM Plex Serif',serif;}
.rq-head.cat-ok{color:var(--ok);}
.rq-head.cat-fail{color:var(--fail-ink);}
.cat-desc{color:var(--ink-muted); font-size:.92rem; max-width:75ch; margin-bottom:18px;}
.note{font-size:.86rem; color:var(--ink-muted); background:var(--surface-2); border-radius:6px; padding:10px 14px; margin:10px 0 6px; max-width:78ch;}

/* ---- charts / tables ---- */
.chart-row{display:flex; gap:20px; flex-wrap:wrap; margin-bottom:8px;}
.chart-box{background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:12px 14px 4px; box-shadow:var(--shadow); flex:1 1 280px; min-width:0;}
.chart-svg{width:100%; height:auto; display:block;}
.chart-grid{stroke:var(--border); stroke-width:1;}
.chart-axisline{stroke:var(--ink-faint); stroke-width:1;}
.chart-axis{font-size:9px; fill:var(--ink-faint); font-family:'IBM Plex Sans',sans-serif;}
.chart-cat{font-size:10px; fill:var(--ink-muted); font-family:'IBM Plex Sans',sans-serif;}
.chart-val{font-size:9.5px; fill:var(--ink); font-weight:600; font-family:'IBM Plex Sans',sans-serif;}
.chart-legend{font-size:10px; fill:var(--ink-muted); font-family:'IBM Plex Sans',sans-serif;}
.pipeline-box{background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px 18px 10px; box-shadow:var(--shadow); margin:16px 0 6px; overflow-x:auto;}
.pipeline-svg{width:100%; height:auto; display:block; min-width:520px;}
.pl-label{font-size:12px; font-weight:600; fill:var(--ink); font-family:'IBM Plex Sans',sans-serif;}
.pl-sub{font-size:9.5px; fill:var(--ink-muted); font-family:'IBM Plex Sans',sans-serif;}
.pl-new{font-size:9px; font-weight:700; fill:#fff; font-family:'IBM Plex Sans',sans-serif; letter-spacing:.03em;}
.pl-loop{font-size:9.5px; fill:var(--fail); font-family:'IBM Plex Sans',sans-serif; font-style:italic;}
.pipeline-note{max-width:78ch;}
.table-wrap{overflow-x:auto; margin:10px 0 6px;}
table.data-table{width:100%; border-collapse:collapse; font-size:.88rem; min-width:420px;}
table.data-table th{text-align:left; font-weight:600; color:var(--ink-muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.04em; padding:7px 12px; border-bottom:2px solid var(--border);}
table.data-table td{padding:8px 12px; border-bottom:1px solid var(--border);}
table.data-table td.hl{color:var(--accent-ink); font-weight:600;}

/* ---- glossary / stats2 ---- */
.glossary{background:var(--surface); border:1px solid var(--border); border-left:4px solid var(--accent); border-radius:8px; padding:16px 20px; margin:20px 0 24px; box-shadow:var(--shadow);}
.glossary h4{font-size:.76rem; text-transform:uppercase; letter-spacing:.06em; color:var(--accent-ink); margin-bottom:8px; font-weight:700;}
.glossary p{font-size:.92rem; margin:0;}
.stats2{display:flex; gap:10px; flex-wrap:wrap; margin:18px 0 30px;}
.stats2 > div{background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:12px 16px; min-width:110px; box-shadow:var(--shadow);}
.stats2 .n{font-family:'IBM Plex Serif',serif; font-size:1.7rem; font-weight:600; line-height:1;}
.stats2 .l{font-size:.75rem; color:var(--ink-muted); margin-top:5px;}
.stat2ok .n{color:var(--ok);}
.stat2fail .n{color:var(--fail);}

/* ---- case cards ---- */
.case2{background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:20px 22px 24px; margin-bottom:20px; box-shadow:var(--shadow); scroll-margin-top:16px;}
.case2-fail{border-left:3px solid var(--fail);}
.case2-ok{border-left:3px solid var(--ok);}
.case-head{display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:10px;}
.pill{font-size:.72rem; font-weight:600; padding:3px 10px; border-radius:99px;}
.pill-ok{background:var(--ok-soft); color:var(--ok);}
.pill-fail{background:var(--fail-soft); color:var(--fail-ink);}
.pill-muted{background:var(--surface-2); color:var(--ink-muted); font-weight:500;}
.case-mci{font-size:1.05rem; font-weight:500; margin-bottom:4px; word-break:break-word;}
.case-mci code{background:none; color:var(--ink);}
.case-file{color:var(--ink-muted); font-size:.85rem; margin-bottom:12px;}
.code-label{font-size:.76rem; font-weight:600; color:var(--ink-muted); margin:14px 0 6px;}
pre.code{background:var(--code-bg); border:1px solid var(--code-border); border-radius:8px; padding:13px 15px; overflow-x:auto; margin:0 0 6px;}
pre.code code{font-size:.8rem; line-height:1.55; color:var(--code-ink); white-space:pre;}
pre.code-tight{padding:9px 12px;}
pre.code-tight code{font-size:.75rem;}
.hlj .c1,.hlj .c,.hlj .cm{color:var(--code-comment); font-style:italic;}
.hlj .k,.hlj .kd,.hlj .kc,.hlj .kn,.hlj .kr,.hlj .ow{color:var(--code-keyword); font-weight:600;}
.hlj .kt{color:var(--code-type);}
.hlj .s,.hlj .s1,.hlj .s2,.hlj .sa,.hlj .sc{color:var(--code-string);}
.hlj .m,.hlj .mi,.hlj .mf,.hlj .mh,.hlj .mo{color:var(--code-number);}
.hlj .nd{color:var(--code-annot);}
.hlj .nc{color:var(--code-type); font-weight:600;}
.hlj .nf{color:var(--code-ink); font-weight:600;}
.hlj .n,.hlj .na,.hlj .nv,.hlj .no{color:var(--code-ink);}
.hlj .o,.hlj .p{color:var(--code-ink); opacity:.7;}

.comment{border-radius:8px; padding:12px 16px; margin:14px 0 12px; border:1px solid;}
.comment-caveat{background:var(--accent-soft); border-color:color-mix(in srgb, var(--accent) 35%, transparent);}
.comment-missing{background:var(--surface-2); border-color:var(--border);}
.comment-label{font-size:.72rem; font-weight:700; letter-spacing:.05em; margin-bottom:5px; color:var(--accent-ink);}
.comment-missing .comment-label{color:var(--ink-muted);}
.comment p{margin:0 0 4px; font-size:.9rem;}
.comment .quote-en{font-style:italic; color:var(--ink);}
.comment .quote-zh{color:var(--ink-muted);}

.verdict{font-size:.92rem; font-weight:500; margin:0 0 4px;}
.verdict-ok{color:var(--ok);}
.verdict-fail{color:var(--fail-ink);}

details.all-seqs{margin-top:16px; border-top:1px dashed var(--border); padding-top:12px;}
details.all-seqs summary{cursor:pointer; font-size:.85rem; font-weight:600; color:var(--accent); list-style:none; display:flex; align-items:center; gap:6px;}
details.all-seqs summary::-webkit-details-marker{display:none;}
details.all-seqs summary::before{content:"\25B8"; display:inline-block; transition:transform .15s;}
details.all-seqs[open] summary::before{transform:rotate(90deg);}
.all-seqs-sub{font-size:.78rem; font-weight:600; color:var(--ink-muted); margin:16px 0 8px; text-transform:uppercase; letter-spacing:.04em;}
.extra-seq-grid{display:grid; gap:10px;}
.extra-seq-h{font-size:.76rem; font-family:'IBM Plex Mono',monospace; color:var(--ink-muted); margin:0 0 4px;}
.seq-table-wrap{overflow-x:auto; margin-top:6px;}
table.seq-table{width:100%; border-collapse:collapse; font-size:.76rem; min-width:520px;}
table.seq-table th{text-align:left; font-weight:600; color:var(--ink-muted); font-size:.7rem; text-transform:uppercase; letter-spacing:.03em; padding:6px 10px; border-bottom:1px solid var(--border);}
table.seq-table td{padding:6px 10px; border-bottom:1px solid var(--border); vertical-align:top;}
table.seq-table tr:last-child td{border-bottom:none;}
td.seqno{color:var(--ink-faint); font-variant-numeric:tabular-nums;}
td.seqclass, td.seqmethod{white-space:nowrap; font-family:'IBM Plex Mono',monospace; font-size:.74rem; color:var(--ink-muted);}
td.seqmethod{color:var(--ink);}
.stmt{display:flex; gap:8px; align-items:baseline; margin-bottom:3px;}
.stmt:last-child{margin-bottom:0;}
.stmt .ln{font-family:'IBM Plex Mono',monospace; font-size:.68rem; color:var(--ink-faint); flex-shrink:0; width:34px;}
.stmt code{font-family:'IBM Plex Mono',monospace; font-size:.74rem; color:var(--ink); word-break:break-word; white-space:pre-wrap;}

/* ---- ablation arms ---- */
.arms{display:grid; gap:14px; margin:18px 0 24px;}
.arm{background:var(--surface); border:1px solid var(--border); border-left:3px solid var(--accent); border-radius:8px; padding:14px 18px; box-shadow:var(--shadow);}
.arm h4{font-size:.98rem; font-weight:600; color:var(--accent-ink); margin-bottom:6px;}
.arm p{font-size:.9rem; margin:0; color:var(--ink-muted);}
.answer-list{padding-left:20px; margin:10px 0; max-width:78ch;}
.answer-list li{margin-bottom:8px; font-size:.94rem;}

footer{color:var(--ink-faint); font-size:.8rem; padding:30px 0 46px; max-width:900px;}
"""

TITLE = "CloneDeMocker Report"

TOGGLE_JS = r"""
(function(){
  function setLang(lang){
    document.documentElement.setAttribute('lang-mode', lang);
    document.querySelectorAll('.lang-en').forEach(function(el){ el.hidden = lang !== 'en'; });
    document.querySelectorAll('.lang-zh').forEach(function(el){ el.hidden = lang !== 'zh'; });
    document.getElementById('btn-en').classList.toggle('active', lang === 'en');
    document.getElementById('btn-zh').classList.toggle('active', lang === 'zh');
    try { localStorage.setItem('cdm-lang', lang); } catch (e) {}
  }
  window.__setLang = setLang;
  var saved = 'en';
  try { saved = localStorage.getItem('cdm-lang') || 'en'; } catch (e) {}
  setLang(saved);
})();
"""

PAGE = f"""<title>{TITLE}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Serif:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>

<div class="langbar">
  <button id="btn-en" onclick="__setLang('en')">EN</button>
  <button id="btn-zh" onclick="__setLang('zh')">中文</button>
</div>

<div class="wrap">
  <div class="hero">
    <div class="kicker">CloneDeMocker &middot; Apache Dubbo 3.3.6</div>
    <h1>{B("Mock-Clone Refactoring: Results, Case Studies &amp; Next Steps",
           "Mock Clone 重构：结果、案例分析与下一步计划")}</h1>
    <p class="dek">{B(
      "How CloneDeMocker's LLM-based refactoring compares to the FSE paper baseline, what "
      "actually happens when the model isn't fully certain about a candidate, and the "
      "ablation study planned to isolate where the improvement comes from.",
      "CloneDeMocker 基于 LLM 的重构效果和 FSE 论文基线相比如何、模型对某个候选项不完全确定时"
      "实际会发生什么、以及计划用来拆解“改进到底来自哪里”的消融实验。"
    )}</p>
  </div>

  <div class="layout">
    <details class="toc" open>
      <summary>{B("Contents","目录")}</summary>
      <nav class="toc-body">{nav_html}</nav>
    </details>
    <main>
      {part1_html}
      {part2_html}
      {part3_html}
      <footer>{B(
        "Sources: validation/results (pilot-merged-redesign-round1, the final run) and "
        "data/dubbo/detection.json (the detector's raw captured source). Code excerpts are "
        "real, reflowed for width, not rewritten.",
        "数据来源：validation/results（pilot-merged-redesign-round1，最终一轮跑批）和 "
        "data/dubbo/detection.json（检测器捕获的原始源码）。代码片段均为真实代码，只做了"
        "换行排版调整，内容未改写。"
      )}</footer>
    </main>
  </div>
</div>
<script>{TOGGLE_JS}</script>
"""

OUT.write_text(PAGE, encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")





