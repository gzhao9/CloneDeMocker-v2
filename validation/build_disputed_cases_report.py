# -*- coding: utf-8 -*-
"""Builds an English PDF explaining the 14 non-success refactoring candidates
from the Dubbo 3.3.6 run: which ones the model itself flagged as disputed
before ever attempting a change, and which ones it attempted (raised no
objection) but the harness later confirmed had actually failed.

Code excerpts are pulled from data/dubbo/detection.json (real detector
output) and data/dubbo/diffs/*.diff (real harness-verified patches), reflowed
for print width but not altered in content. Java is syntax-highlighted via
Pygments, rendered to PNG, and embedded as images (fpdf2 has no native
syntax-highlighting text mode). Experimental plumbing (run IDs, PIT
internals, batch progress) is intentionally left out -- this is a case-study
explainer, not a lab notebook.
"""
from pathlib import Path
from pygments import highlight
from pygments.lexers import JavaLexer
from pygments.formatters import ImageFormatter
from PIL import Image
from fpdf import FPDF

OUT_PATH = Path(r"D:\CloneDeMocker-v2-transfer-20260914\CloneDeMocker-v2\validation\results\disputed-and-failed-cases-20260917.pdf")
IMG_DIR = Path(r"C:\Users\ITGXUser\AppData\Local\Temp\claude\d--CloneDeMocker-v2-transfer-20260914\bf7419b6-3a32-4b6a-9188-46e8427b9bcc\scratchpad\code_imgs")
IMG_DIR.mkdir(parents=True, exist_ok=True)

NAVY = (30, 41, 59)
GRAY = (71, 85, 105)
BLUE = (37, 99, 235)
LIGHT_BLUE = (239, 246, 255)
LIGHT = (241, 245, 249)
AMBER = (180, 83, 9)
LIGHT_AMBER = (255, 247, 237)
GREEN = (21, 128, 61)

CONTENT_W = 174  # mm, matches A4 with 18mm margins


def code_image(key, java_src):
    """Render Java source to a syntax-highlighted PNG, return (path, w, h) in px."""
    path = IMG_DIR / f"{key}.png"
    fmt = ImageFormatter(font_name="Consolas", font_size=20, line_numbers=False,
                          style="friendly", image_pad=12, line_pad=3)
    data = highlight(java_src, JavaLexer(), fmt)
    path.write_bytes(data)
    im = Image.open(path)
    return str(path), im.size[0], im.size[1]


class Report(FPDF):
    def header(self):
        pass

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 8, f"{self.page_no()}", align="C")

    def h1(self, text):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(*NAVY)
        self.ln(2)
        self.cell(0, 9, text, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*BLUE)
        self.set_line_width(0.6)
        y = self.get_y() + 1
        self.line(self.l_margin, y, self.l_margin + 30, y)
        self.ln(5)

    def h2(self, text, color=BLUE):
        self.set_font("Helvetica", "B", 12.5)
        self.set_text_color(*color)
        self.ln(2)
        self.cell(0, 7.5, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def h3(self, text, color=NAVY):
        self.set_font("Helvetica", "B", 10.5)
        self.set_text_color(*color)
        self.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")

    def body(self, text, size=9.5):
        self.set_font("Helvetica", "", size)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5.3, text, align="L", new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def note(self, text):
        self.set_font("Helvetica", "I", 8.5)
        self.set_text_color(*GRAY)
        self.multi_cell(0, 4.6, text, align="L", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def code(self, key, java_src, max_w=CONTENT_W):
        path, pw, ph = code_image(key, java_src)
        h_mm = max_w * ph / pw
        if self.get_y() + h_mm > 279:
            self.add_page()
        self.image(path, x=self.l_margin, w=max_w)
        self.ln(2)

    def quote_box(self, label, text, color=BLUE, bg=LIGHT_BLUE):
        self.set_font("Helvetica", "B", 8.7)
        self.set_text_color(*color)
        self.set_fill_color(*bg)
        self.set_draw_color(*color)
        self.multi_cell(0, 4.6, label, fill=True, border="LTR", align="L",
                         padding=(2.5, 4, 1, 4), new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 4.8, text, fill=True, border="LBR", align="L",
                         padding=(1, 4, 2.5, 4), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def verdict(self, text, ok=True):
        color = GREEN if ok else AMBER
        self.set_font("Helvetica", "B", 9.3)
        self.set_text_color(*color)
        self.multi_cell(0, 5, text, align="L", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def case_title(self, num, mci):
        self.set_font("Helvetica", "B", 12.5)
        self.set_text_color(255, 255, 255)
        self.set_fill_color(*NAVY)
        self.cell(0, 8.5, f"  Case {num}    {mci}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)


pdf = Report(format="A4")
pdf.set_auto_page_break(auto=True, margin=16)
pdf.set_margins(18, 16, 18)

case3_link = pdf.add_link()

# ---------------------------------------------------------------------------
# Page 1 -- Title
# ---------------------------------------------------------------------------
pdf.add_page()
pdf.ln(28)
pdf.set_font("Helvetica", "B", 22)
pdf.set_text_color(*NAVY)
pdf.cell(0, 12, "Refactoring Candidates Not Directly Accepted", align="C",
         new_x="LMARGIN", new_y="NEXT")
pdf.set_font("Helvetica", "B", 14)
pdf.set_text_color(*BLUE)
pdf.cell(0, 9, "14 Cases, with the Real Code", align="C", new_x="LMARGIN", new_y="NEXT")
pdf.ln(5)
pdf.set_font("Helvetica", "", 10.5)
pdf.set_text_color(*GRAY)
pdf.multi_cell(0, 6,
    "CloneDeMocker attempted an automated refactor for every duplicated Mock "
    "setup (Mock Clone Instance, MCI) detected in Apache Dubbo. 14 candidates "
    "did not come out of that as a direct success. This report shows what "
    "each one actually looked like, and why.",
    align="C", new_x="LMARGIN", new_y="NEXT")

# ---------------------------------------------------------------------------
# Page 2 -- Classification overview
# ---------------------------------------------------------------------------
pdf.add_page()
pdf.h1("Overview")
pdf.body(
    "The 14 cases split first by whether the model itself raised an objection:"
)


def table(headers, rows, widths, header_color=NAVY, label_color=BLUE):
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*header_color)
    pdf.set_text_color(255, 255, 255)
    for h, w in zip(headers, widths):
        pdf.cell(w, 7.2, h, border=0, align="C", fill=True)
    pdf.ln()
    fill = False
    for a, b, c in rows:
        pdf.set_fill_color(*LIGHT) if fill else pdf.set_fill_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 8.7)
        pdf.set_text_color(*label_color)
        y0, x0 = pdf.get_y(), pdf.get_x()
        pdf.multi_cell(widths[0], 5.6, a, border=0, align="L", fill=True)
        row_h = pdf.get_y() - y0
        pdf.set_xy(x0 + widths[0], y0)
        pdf.set_font("Helvetica", "", 8.7)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(widths[1], 5.6, b, border=0, align="L", fill=True)
        pdf.set_xy(x0 + widths[0] + widths[1], y0)
        pdf.cell(widths[2], row_h, c, border=0, align="C", fill=True)
        pdf.set_y(y0 + row_h)
        fill = not fill
    pdf.ln(4)


widths = [40, 104, 30]

pdf.h2("Group 1: AI Flagged as Disputed -- 11 cases", color=BLUE)
pdf.body(
    "Before touching any code, the model judged whether this candidate fit its "
    "one allowed move (extract the duplicated Mock setup into a shared helper "
    "and wire it in). If it decided the move was unsafe or out of scope, it "
    "flagged the candidate as disputed, explained why, and produced no patch. "
    "Nothing here ever reached compile or test -- it isn't a \"failure\", it's "
    "the model's own call."
)
table(
    ["Subtype", "Pattern", "N"],
    [
        ("A. Dead config", "Mock is never wired into the object under test", "3"),
        ("B. Semantic divergence", "Cases deliberately configure the same mock differently", "4"),
        ("C. Tooling constraint", "Fix needs a new shared file; tool can't create one", "2 (1 overlaps B)"),
        ("D. Needs runtime proof", "Static reading can't confirm the change is safe", "1"),
        ("E. Full-file protocol limit", "File too large to safely return whole", "2"),
    ],
    widths,
)

pdf.h2("Group 2: Executed, Then Actually Failed -- 3 cases", color=AMBER)
pdf.body(
    "Here the model raised no objection: it produced a patch and the patch was "
    "run -- compiled, tested, format-checked. Only after execution did it turn "
    "out not to hold up. These are real failures, each for a different reason:"
)
table(
    ["Subtype", "Pattern", "N"],
    [
        ("F. Goal partly met", "Compiles and passes tests, but only some duplication was removed", "2"),
        ("G. Style-check conflict", "Valid, compilable code that trips the target project's formatter", "1"),
    ],
    widths,
    header_color=AMBER,
    label_color=AMBER,
)
pdf.note("Blue boxes below = \"AI OBJECTION\" (Group 1). Orange boxes = \"FAILURE\" (Group 2).")

# ---------------------------------------------------------------------------
# Table of contents
# ---------------------------------------------------------------------------
def render_toc(pdf, outline):
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*NAVY)
    pdf.set_x(pdf.l_margin)
    pdf.cell(0, 10, "Contents", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    for section in outline:
        pdf.set_font("Helvetica", "B" if section.level == 0 else "", 11 if section.level == 0 else 10)
        pdf.set_text_color(*BLUE if section.level == 0 else (30, 30, 30))
        link = pdf.add_link(page=section.page_number)
        indent = 0 if section.level == 0 else 8
        pdf.set_x(pdf.l_margin + indent)
        pdf.cell(0, 7.2 if section.level == 0 else 6.6, section.name, link=link,
                 new_x="LMARGIN", new_y="NEXT")


pdf.add_page()
pdf.insert_toc_placeholder(render_toc, pages=1, allow_extra_pages=True,
                            reset_page_indices=False)
# insert_toc_placeholder() itself performs the page break past the reserved
# TOC page, so the cursor is already at the top of a fresh page here.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def category_header(letter, title, desc, color=BLUE):
    if pdf.get_y() > pdf.t_margin + 3:
        pdf.add_page()
    pdf.start_section(f"{letter}. {title}", level=0)
    pdf.h1(f"{letter}. {title}")
    pdf.body(desc)


def case_block(num, mci, file_desc, code_parts, quote, verdict_text, ok=True,
               failure=False, bind_case3_link=False, code_only_note=None):
    if pdf.get_y() > 220:
        pdf.add_page()
    pdf.start_section(f"Case {num}  {mci}", level=1)
    if bind_case3_link:
        pdf.set_link(case3_link)
    pdf.case_title(num, mci)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*GRAY)
    pdf.multi_cell(0, 4.8, file_desc, align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    for i, (heading, src) in enumerate(code_parts):
        if heading:
            pdf.h3(heading)
        pdf.code(f"c{num}_{i}", src)
    if code_only_note:
        pdf.note(code_only_note)
    label = "FAILURE" if failure else "AI OBJECTION"
    color = AMBER if failure else BLUE
    bg = LIGHT_AMBER if failure else LIGHT_BLUE
    pdf.quote_box(label, f'"{quote}"', color=color, bg=bg)
    pdf.verdict(verdict_text, ok=ok)
    pdf.ln(3)


# ===========================================================================
# A. Dead config
# ===========================================================================
category_header(
    "A", "Dead config -- mock never wired into the object under test",
    "The mock is created and stubbed, but never passed into any constructor, "
    "setter, or method argument of the object actually being tested, so the "
    "stub can't influence anything. Extracting it into a helper would just "
    "relocate dead code, not the encapsulate-and-integrate refactor the tool "
    "targets -- so the model declined."
)

case_block(
    4, "org.apache.dubbo.metadata.MetadataService::1",
    "ServiceInstancesChangedListenerTest.java",
    [
        (None,
         'static MetadataService metadataService;\n'
         '...\n'
         'metadataService = Mockito.mock(MetadataService.class);   // in @BeforeAll setUp()'),
        ("The only method that touches it:",
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
         '}'),
    ],
    "the duplicated stubbing targets static metadataService mocks that are not "
    "injected into, referenced by, or otherwise reachable from the locally "
    "created ServiceDiscovery and ServiceInstancesChangedListener in either "
    "test method.",
    "The listener's constructor only receives serviceDiscovery -- metadataService "
    "is unreachable dead code. Correct call: this needs a dead-code flag, not a "
    "refactor.",
)

case_block(
    5, "CompositeConfiguration::1  and  ConfigManager::1",
    "RegistryProtocolTest.java -- both mocks live in the same 7 test methods; shown "
    "together since they're the same pattern",
    [
        (None,
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
         '    Cluster cluster = mock(Cluster.class);\n\n'
         '    Invoker<?> invoker =\n'
         '        registryProtocol.doRefer(cluster, registry, DemoService.class, url, parameters);\n'
         '    Assertions.assertTrue(invoker instanceof MigrationInvoker);\n'
         '    // ... assertions continue, none of them touch configManager or\n'
         '    // compositeConfiguration -- only cluster/registry/url/parameters reach doRefer()\n'
         '}'),
    ],
    "each CompositeConfiguration mock is created and stubbed but is never "
    "passed to RegistryProtocol, ModuleModel, ConfigManager, or any other "
    "collaborator ... [same wording, both mocks] ... never passed to "
    "RegistryProtocol, ModuleModel, ApplicationModel, or any other "
    "collaborator used by the test. Removing them would be dead-code cleanup "
    "rather than the requested two-step mock-clone refactoring.",
    "Same dead-mock pattern, twice, in the same method, repeated across all 7 "
    "test methods in this file -- likely leftover test debt in the target "
    "project, not a detector false positive. Correct call both times.",
)

# ===========================================================================
# B. Semantic divergence
# ===========================================================================
category_header(
    "B", "Semantic divergence -- deliberately different configs",
    "The mock looks structurally similar (same class, same method names) but "
    "each test method configures it with different values, deliberately, to "
    "exercise different branches. Merging into one shared setup would erase "
    "that distinction, so the model declined."
)

case_block(
    2, "java.net.InetAddress::2",
    "NetUtilsTest.java",
    [
        (None,
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
         '}'),
    ],
    "The two clone instances require different InetAddress configurations: one "
    "requires isLoopbackAddress, getHostAddress, and isReachable stubs on the "
    "same mock, while the other intentionally creates separate mocks with "
    "selectively configured stubs for independent assertions.",
    "Five reassignments of the same variable, five different stub combos, one "
    "designed to hit the single true-returning branch. Collapsing this into a "
    "shared setup would erase the branch coverage. Correct call.",
)

case_block(
    1, "org.apache.dubbo.registry.nacos.NacosConnectionManager::1",
    "NacosNamingServiceWrapperTest.java -- testSubscribe vs. testConcurrency",
    [
        (None,
         '@Test\n'
         'void testSubscribe() throws NacosException {\n'
         '    NacosConnectionManager connectionManager = Mockito.mock(NacosConnectionManager.class);\n'
         '    NamingService namingService = Mockito.mock(NamingService.class);\n'
         '    Mockito.when(connectionManager.getNamingService()).thenReturn(namingService);\n'
         '    NacosNamingServiceWrapper nacosNamingServiceWrapper =\n'
         '        new NacosNamingServiceWrapper(connectionManager, 0, 0);\n'
         '    EventListener eventListener = Mockito.mock(EventListener.class);\n'
         '    nacosNamingServiceWrapper.subscribe("service_name", "test", eventListener);\n'
         '    Mockito.verify(namingService, Mockito.times(1))\n'
         '        .subscribe("service_name", "test", eventListener);\n'
         '    // ... repeats subscribe/unsubscribe with times(2), times(1), times(0)\n'
         '}'),
        ("testConcurrency uses its own independent instance:",
         '@Test\n'
         'void testConcurrency() throws NacosException, InterruptedException {\n'
         '    NacosConnectionManager connectionManager = Mockito.mock(NacosConnectionManager.class);\n'
         '    CountDownLatch startLatch = new CountDownLatch(1);\n'
         '    CountDownLatch stopLatch = new CountDownLatch(1);\n'
         '    NamingService namingService = Mockito.mock(NamingService.class);\n'
         '    Mockito.when(connectionManager.getNamingService()).thenReturn(namingService);\n'
         '    // ... registers an instance, then two threads register/deregister\n'
         '    // concurrently against this connectionManager while a stubbed\n'
         '    // InstancesInfo.getInstances() sleeps to force interleaving\n'
         '    new Thread(() -> { /* register, countDown */ }).start();\n'
         '    new Thread(() -> { /* deregister */ }).start();\n'
         '}'),
    ],
    "each mock is method-local and testConcurrency relies on its own "
    "independently configured connection manager during concurrent "
    "registration and deregistration.",
    "testConcurrency is a real concurrency test; it can't share a mock "
    "lifecycle with testSubscribe. Correct call.",
)

case_block(
    10, "org.apache.dubbo.registry.NamingService::4",
    "NacosNamingServiceWrapperTest.java -- testSubscribeMultiManager",
    [
        (None,
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
         '    // ... namingService1 and namingService2 represent the backend\n'
         '    // before/after a connection-manager switch\n'
         '}'),
    ],
    "The mocks are independently scoped to distinct connection-manager "
    "lifecycles (including anonymous manager overrides that capture different "
    "lists). Integrating them into a shared reusable mock would risk "
    "cross-test interaction and alter lifecycle behavior.",
    "namingService1/namingService2 stand for two different backends before and "
    "after a switch; merging them into one reusable mock erases exactly the "
    "thing the test is checking. Correct call.",
)

case_block(
    3, "org.apache.dubbo.registry.client.ServiceDiscovery::1",
    "ServiceInstancesChangedListenerTest.java + ...WithoutEmptyProtectTest.java",
    [
        (None,
         '@Test\n@Order(11)\n'
         'public void testRevisionFailureOnNotification() {\n'
         '    // retry path: a notification with a bad revision should recover\n'
         '    // once the metadata retries after 15s\n'
         '    Set<String> serviceNames = new HashSet<>();\n'
         '    serviceNames.add("app1");\n'
         '    serviceNames.add("app2");\n'
         '    listener = new ServiceInstancesChangedListener(serviceNames, serviceDiscovery);\n'
         '    listener.onEvent(new ServiceInstancesChangedEvent("app1", app1Instances));\n'
         '    when(serviceDiscovery.getRemoteMetadata(eq("222"), anyList()))\n'
         '        .thenAnswer(new Answer<MetadataInfo>() {\n'
         '            @Override\n'
         '            public MetadataInfo answer(InvocationOnMock invocationOnMock) {\n'
         '                if (Thread.currentThread().getName()\n'
         '                        .contains("Dubbo-framework-metadata-retry")) {\n'
         '                    return metadataInfo_222;\n'
         '                }\n'
         '                return MetadataInfo.EMPTY;\n'
         '            }\n'
         '        });\n'
         '    listener.onEvent(new ServiceInstancesChangedEvent("app2", app1FailedInstances2));\n'
         '    // ... assert the retry-thread branch eventually recovers the addresses\n'
         '}'),
    ],
    "Creating a dedicated shared fixture/helper would require adding a file "
    "not supplied for modification.",
    "The stub's behavior checks the calling thread's name -- tied to a "
    "specific retry mechanism, and (see subtype C below) the shared-helper fix "
    "would need a new file the tool wasn't allowed to create.",
    bind_case3_link=True,
)

# ===========================================================================
# C. Tooling constraint
# ===========================================================================
category_header(
    "C", "Tooling constraint -- needs a new shared file",
    "Different from the above: the model considered the refactor itself clear "
    "and safe, but at the time, the tool could only edit files already "
    "flagged by the detector, not create new ones -- and that's exactly what "
    "these two candidates needed."
)

case_block(
    8, "org.apache.dubbo.registry.NotifyListener::2",
    "ServiceInstancesChangedListenerTest.java + ...WithoutEmptyProtectTest.java "
    "(22 sequences across 6 test methods)",
    [
        (None,
         '// repeated in both test classes:\n'
         'ServiceInstancesChangedListener listener =\n'
         '    new ServiceInstancesChangedListener(serviceNames, serviceDiscovery);\n'
         'when(notifyListener.getConsumerUrl()).thenReturn(consumerURL);\n'
         '// ... 22 near-identical sequences total, split across two independently\n'
         '// runnable test classes'),
    ],
    "A behavior-preserving extraction is technically possible for each "
    "individual mock creation/stubbing pair, but integrating the selected "
    "clones across both test classes would require either introducing a new "
    "shared test utility file or coupling one test class to a helper declared "
    "in the other. The supplied-file constraint prohibits adding the "
    "appropriate shared utility file, while cross-class coupling would make "
    "the independently executable test classes depend on an unrelated "
    "sibling test source file.",
    "The model says outright: give it a new file and the fix is clear. Of all "
    "14 cases, this is the one where loosening a tool constraint -- not "
    "changing the model's judgment -- would directly recover a success.",
    ok=False,
)

if pdf.get_y() > 240:
    pdf.add_page()
pdf.set_font("Helvetica", "", 9.5)
pdf.set_text_color(*BLUE)
pdf.cell(0, 6, "See also: Case 3, ServiceDiscovery::1 (same constraint) ->",
         link=case3_link, new_x="LMARGIN", new_y="NEXT")
pdf.ln(2)

# ===========================================================================
# D. Needs runtime proof
# ===========================================================================
category_header(
    "D", "Needs runtime proof",
    "This one is different in tone: the model isn't saying the refactor "
    "shouldn't happen, it's saying it can't confirm from static reading alone "
    "that the refactor is safe."
)

case_block(
    7, "org.apache.dubbo.rpc.Invoker::2",
    "MergeableClusterInvokerTest.java -- testInvokerToException",
    [
        (None,
         '@Test\n'
         'void testInvokerToException() {\n'
         '    given(invocation.getMethodName()).willReturn("addMenu");\n'
         '    given(invocation.getInvoker()).willReturn(firstInvoker);\n'
         '    given(firstInvoker.getUrl()).willReturn(url.addParameter(GROUP_KEY, "first"));\n'
         '    given(firstInvoker.getInterface()).willReturn(MenuService.class);\n'
         '    given(firstInvoker.invoke(invocation)).willReturn(new AppResponse());\n'
         '    given(firstInvoker.isAvailable()).willReturn(true);\n'
         '    given(firstInvoker.invoke(invocation))\n'
         '        .willThrow(new RpcException(RpcException.NETWORK_EXCEPTION));  // re-stub!\n'
         '    given(secondInvoker.getUrl()).willReturn(url.addParameter(GROUP_KEY, "second"));\n'
         '    given(secondInvoker.invoke(invocation)).willReturn(new AppResponse());\n'
         '    given(secondInvoker.invoke(invocation))\n'
         '        .willThrow(new RpcException(RpcException.NETWORK_EXCEPTION));  // re-stub!\n'
         '    given(directory.list(invocation)).willReturn(List.of(firstInvoker, secondInvoker));\n'
         '    mergeableClusterInvoker = new MergeableClusterInvoker<>(directory);\n'
         '    try {\n'
         '        mergeableClusterInvoker.invoke(invocation);\n'
         '        fail();\n'
         '    } catch (RpcException expected) {\n'
         '        assertEquals(expected.getCode(), RpcException.NETWORK_EXCEPTION);\n'
         '    }\n'
         '}'),
    ],
    "the repeated stubbing is interleaved with later restubbing of "
    "invoke(invocation) in exception tests. Although a helper could "
    "encapsulate the common statements, safely validating Mockito stubbing "
    "precedence and all affected invocation behavior requires executing or "
    "inspecting the target implementation and test suite, which is not "
    "available in the supplied files.",
    "invoke(invocation) is stubbed once, then re-stubbed to throw -- Mockito "
    "stubbing order matters here, and the model won't assert it's safe to "
    "restructure without being able to run it. A genuinely conservative, "
    "defensible edge case.",
    ok=False,
)

# ===========================================================================
# E. Full-file protocol limit
# ===========================================================================
category_header(
    "E", "Full-file protocol limit",
    "Not a judgment about the refactor at all -- a side effect of the I/O "
    "contract at the time, which required the model to return a complete "
    "modified file rather than a diff. On a large file, the model declined "
    "rather than risk silently corrupting the untouched parts."
)

case_block(
    9, "ExtensionLoader<RegistryProtocolListener>::1",
    "RegistryProtocolTest.java -- the same large file as Case 5 "
    "(the extensionLoaderMock lines already shown there)",
    [],
    "the requested result must include the complete modified source file, "
    "while the supplied file is too large to reproduce reliably without "
    "risking truncation or accidental behavioral changes.",
    "Same file, same size problem as Case 5/6 -- a protocol issue, not a "
    "verdict on this specific mock.",
    ok=False,
)

case_block(
    11, "org.apache.dubbo.common.logger.EventListener::1",
    "NacosNamingServiceWrapperTest.java -- same file and methods as Cases 1 and 10 "
    "(the EventListener mock creation line already shown there)",
    [],
    "the supplied source content is incomplete for reliable reconstruction: "
    "the file is provided as a large inline excerpt without an independently "
    "addressable source artifact or checksum, making it unsafe to return a "
    "modified complete file while guaranteeing that all unchanged content is "
    "preserved byte-for-byte.",
    "Same file-size ceiling again, this time framed around reconstruction "
    "safety rather than raw length.",
    ok=False,
)

# ===========================================================================
# F. Goal partly met (GENUINE FAILURE)
# ===========================================================================
category_header(
    "F", "Goal partly met",
    "These two got no objection, ran, compiled, and passed every test. Only a "
    "line-count check against the original duplication found that only part "
    "of the MCI was actually deduplicated -- a real, if partial, failure.",
    color=AMBER,
)

case_block(
    12, "org.apache.dubbo.rpc.Invoker::3",
    "ConnectivityValidationTest.java -- before",
    [
        (None,
         '@Test\n'
         'void testRetry() throws InterruptedException {\n'
         '    Invocation invocation = new RpcInvocation();\n'
         '    LoadBalance loadBalance = new RandomLoadBalance();\n'
         '    invokerList.clear();\n'
         '    invokerList.add(invoker1);\n'
         '    invokerList.add(invoker2);\n'
         '    directory.notify(invokerList);\n'
         '    Assertions.assertEquals(2, directory.list(invocation).size());\n'
         '    when(invoker1.isAvailable()).thenReturn(false);\n'
         '    Assertions.assertEquals(invoker2, clusterInvoker.select(\n'
         '        loadBalance, invocation, directory.list(invocation),\n'
         '        Collections.singletonList(invoker2)));\n'
         '    Assertions.assertEquals(1, directory.list(invocation).size());\n'
         '    when(invoker1.isAvailable()).thenReturn(true);\n'
         '    // ...\n'
         '}'),
        ("What the accepted patch did -- extracted a shared helper:",
         'private void setInvokerAvailability(Invoker invoker, boolean available) {\n'
         '    when(invoker.isAvailable()).thenReturn(available);\n'
         '}\n\n'
         '// call sites become:\n'
         'setInvokerAvailability(invoker1, false);\n'
         'setInvokerAvailability(invoker1, true);'),
    ],
    "compileStatus=PASSED, testStatus=PASSED -- the harness accepted the run.",
    "The isAvailable() stub -- the MCI's tracked duplication -- was correctly "
    "extracted everywhere. The automated goal-check nonetheless flagged this "
    "MCI as not fully resolved on its original run; compiling and passing "
    "tests is not the same bar as the goal-check's own line-occurrence count. "
    "Execution succeeded; the stricter goal metric did not.",
    ok=False, failure=True,
)

case_block(
    13, "Directory<DemoService>::1",
    "FailSafeClusterInvokerTest.java -- before",
    [
        (None,
         '@Test\n'
         'void testNoInvoke() {\n'
         '    dic = mock(Directory.class);\n'
         '    given(dic.getUrl()).willReturn(url);\n'
         '    given(dic.getConsumerUrl()).willReturn(url);\n'
         '    given(dic.list(invocation)).willReturn(null);\n'
         '    given(dic.getInterface()).willReturn(DemoService.class);\n'
         '    invocation.setMethodName("method1");\n'
         '    resetInvokerToNoException();\n'
         '    FailsafeClusterInvoker<DemoService> invoker = new FailsafeClusterInvoker<>(dic);\n'
         '    try {\n'
         '        invoker.invoke(invocation);\n'
         '    } catch (RpcException e) {\n'
         '        Assertions.assertTrue(e.getMessage().contains("No provider available"));\n'
         '    }\n'
         '}'),
        ("After -- the accepted patch (real diff):",
         '@Test\nvoid testNoInvoke() {\n'
         '    dic = mock(Directory.class);\n'
         '    DirectoryTestFixture.stubMetadata(dic, url, DemoService.class);\n'
         '    given(dic.list(invocation)).willReturn(null);\n'
         '    // getUrl/getConsumerUrl/getInterface consolidated into stubMetadata();\n'
         '    // "dic = mock(Directory.class);" itself is untouched, and still repeats\n'
         '    // in BroadCastClusterInvokerTest\'s setUp()\n'
         '    ...\n'
         '}\n\n'
         'final class DirectoryTestFixture {\n'
         '    private DirectoryTestFixture() {}\n'
         '    static <T> void stubMetadata(Directory<T> directory, URL url, Class<T> type) {\n'
         '        given(directory.getUrl()).willReturn(url);\n'
         '        given(directory.getConsumerUrl()).willReturn(url);\n'
         '        given(directory.getInterface()).willReturn(type);\n'
         '    }\n'
         '}'),
    ],
    "compileStatus=PASSED, testStatus=PASSED.",
    "The three stub statements were correctly consolidated into a new "
    "DirectoryTestFixture helper -- a real, working Encapsulation-and-"
    "Integration move, and this run was even allowed to create the new file. "
    "But \"dic = mock(Directory.class);\" -- the actual duplicated creation "
    "line the MCI flagged -- was left untouched in both test classes. "
    "Genuinely partial: the helper is good, the job isn't finished.",
    ok=False, failure=True,
)

# ===========================================================================
# G. Style-check conflict (GENUINE FAILURE, non-AI cause)
# ===========================================================================
category_header(
    "G", "Style-check conflict",
    "Also no objection, also executed. The patch was valid, compilable Java. "
    "It failed a step after compilation: the target project's own formatter "
    "(Spotless) rejected the file's formatting -- unrelated to whether the "
    "refactor itself was sound.",
    color=AMBER,
)

case_block(
    14, "org.apache.dubbo.rpc.cluster.Cluster::1",
    "RegistryProtocolTest.java -- the real build failure (compiled fine; Spotless didn't)",
    [
        (None,
         '// before -- one line, matches the project\'s formatter:\n'
         'moduleModel.getApplicationModel().getApplicationConfigManager()\n'
         '        .setApplication(new ApplicationConfig("application1"));\n\n'
         '// after -- the model\'s returned full file reformatted it to 4 lines:\n'
         'moduleModel\n'
         '        .getApplicationModel()\n'
         '        .getApplicationConfigManager()\n'
         '        .setApplication(new ApplicationConfig("application1"));'),
        ("The actual build output:",
         '[ERROR] Failed to execute goal com.diffplug.spotless:spotless-maven-plugin:\n'
         '2.44.5:check (default) on project dubbo-registry-api:\n'
         'The following files had format violations:\n'
         '    src/test/java/.../RegistryProtocolTest.java'),
    ],
    "Compiling 48 source files ... 0 errors, then: spotless:check FAILED.",
    "The MCI itself was about deduplicating two identical "
    "\"Cluster cluster = mock(Cluster.class);\" lines in this file. The patch "
    "compiled cleanly; the failure came from the full-file rewrite protocol "
    "incidentally reformatting an unrelated method chain elsewhere in the "
    "same file, which then tripped the project's own style check. Not a "
    "judgment failure -- a side effect of returning whole files instead of "
    "diffs, later fixed by skipping Spotless during verification.",
    ok=False, failure=True,
)

pdf.output(str(OUT_PATH))
print(f"wrote {OUT_PATH}")
