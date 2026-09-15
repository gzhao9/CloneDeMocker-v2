// State & Localized Dictionaries
const state = {
  runId: null,
  mocks: [],
  visibleMocks: [],
  instances: [],
  lastAgentPayload: null,
  activeDiffFileIndex: 0,
  diffViewMode: "unified", // "unified" | "sbs"
  parsedDiffFiles: [],
  currentLang: localStorage.getItem("clonedemocker_lang") || "en",
};

const I18N = {
  en: {
    // Header & Navigation
    header_eyebrow: "MOCK CLONE REFACTORING AGENT",
    header_badge: "FSE '26 Research Prototype",
    header_desc: "Detect and eliminate recurring Mockito setup duplicates across Java test suites with LLM-driven encapsulation & integration.",
    btn_env_check: "Env & Safety Check",
    status_ready: "Ready",
    status_loading_tree: "Loading file tree...",
    status_tree_loaded: "File tree loaded",
    status_scanning: "Extracting mock logic & scanning...",
    status_scanned: (n) => `Discovered ${n} Mock Objects`,
    status_detecting: "Mining frequent stub sets & forming MCIs...",
    status_detected: (n) => `Identified ${n} Mock Clone Instances`,
    status_refactoring: "Agent refactoring & verifying with Harness...",
    status_refactor_done: "Refactoring & validation completed",
    status_applying: "Applying patch to project...",
    status_applied: "Refactoring patch applied successfully",
    status_discarding: "Discarding proposal...",
    status_discarded: "Refactoring proposal discarded",
    status_error: "Execution Error",

    // Steps
    step1_title: "Detection Scope",
    step1_sub: "Project & Files",
    step2_title: "Mock Objects",
    step2_sub: "Candidate Pool",
    step3_title: "Clone Instances",
    step3_sub: "MCI Clustering",
    step4_title: "Refactoring Studio",
    step4_sub: "Diff & Decision",

    // Panel 1: Scope
    panel1_title: "Establish Detection Scope",
    panel1_desc: "Select target project directories, specific packages, or individual Java test suites.",
    proj_root_label: "Java Project Root",
    proj_root_sub: "Absolute path without Chinese characters recommended",
    proj_root_ph: "C:\\projects\\my-java-project",
    btn_load_tree: "Load File Tree",
    warning_path_title: "Windows Path Encoding Warning:",
    warning_path_desc: "Non-ASCII characters or spaces detected in path. On Windows, native javac/Maven encoding mismatches may cause silent compilation failures. We strongly recommend placing the target project in a pure ASCII path (e.g., C:\\projects\\dubbo).",
    tree_title: "Java Source Tree",
    btn_select_all: "Select All",
    btn_clear_all: "Clear",
    tree_empty_hint: "Enter project path above and click 'Load File Tree'",
    package_filter_label: "Package Prefixes",
    package_filter_sub: "One per line; leave blank for all",
    exclude_paths_label: "Exclude Patterns",
    exclude_paths_sub: "Relative paths or folders to skip",
    toggle_deps: "Resolve Maven/Gradle Dependencies",
    hint_deps: "When enabled, resolves external classpath via temporary files without modifying your build files.",
    btn_scan_mocks: "Scan Mock Objects →",

    // Panel 2: Objects
    panel2_title: "Select Mock Objects Pool",
    panel2_desc: "Filter and exclude objects you do not wish to participate in Frequent Stub Set Mining.",
    metric_selected_mocks: "Selected / Available",
    mock_search_ph: "Filter by type, variable, file, or method...",
    btn_select_visible: "Select Visible",
    btn_clear_visible: "Deselect Visible",
    th_variable: "Variable",
    th_mocked_class: "Mocked Class",
    th_test_file: "Test Suite / File",
    th_test_cases: "Test Cases",
    th_statements: "Stmts",
    btn_back_scope: "← Back to Scope",
    btn_detect_clones: "Detect Clones in Selected →",

    // Panel 3: MCIs
    panel3_title: "Mock Clone Instances (MCIs)",
    panel3_desc: "Discovered recurring mock setup clusters. Choose instances to submit to the Refactoring Agent.",
    metric_mcis_found: "MCIs Discovered",
    btn_back_objects: "← Back to Object Pool",
    btn_prepare_refactor: "Prepare Refactoring Agent →",
    mci_test_cases: "Test Cases Involved",
    mci_files: "Target File(s)",
    mci_stubs: "Core Frequent Stub Set",
    mci_sequences_toggle: "Inspect / filter individual test sequences",

    // Panel 4: Refactoring Studio
    panel4_title: "Automated Refactoring Studio",
    panel4_desc: "Modular pipeline: Encapsulation → Integration → Multi-tier Harness Validation with Diff Review & Decision",
    model_label: "LLM Model",
    model_sub: "Default: gpt-5.6-terra",
    profile_label: "API Key Profile",
    profile_sub: "Profile in CLONEDEMOCKER_OPENAI_KEYS or default",
    instruction_label: "Custom Instructions",
    instruction_sub: "Optional / e.g., 'Extract helper method into private scope within the same test class'",
    toggle_pit: "Run PIT Mutation Testing (Higher runtime overhead)",
    toggle_mock: "Debug Mode: Dry-run without LLM API token consumption",
    notice_title: "Isolated Safety Guarantee",
    notice_body: "The agent executes on an isolated workspace replica. Original project files will never be directly overwritten during candidate proposal generation.",
    btn_back_clones: "← Back to MCIs",
    btn_run_agent: "Generate Refactoring Proposal",

    // Diff Workbench & Decision
    diff_files_title: "Changed Files:",
    view_unified: "Unified Diff",
    view_sbs: "Side-by-Side",
    btn_copy_diff: "📋 Copy Diff",
    diff_copied: "Diff copied to clipboard!",
    decision_status_label: "Proposal Status:",
    status_awaiting_review: "Awaiting Review",
    status_applied_badge: "✓ Applied to Project",
    status_discarded_badge: "✗ Discarded",
    btn_discard: "Discard Proposal",
    btn_accept: "Accept & Apply Refactoring",
    result_title: "Refactoring Proposal Evidence",
    metric_compile: "Compilation",
    metric_tests: "Regression Tests",
    metric_pit: "Mutation Score",
    metric_tokens: "Token Usage",

    // Environment Diagnostics Modal
    modal_env_title: "🛡️ Environment & Safety Diagnostics",
    modal_env_desc: "Verification of operating system encodings, toolchains, and file isolation policies to prevent Windows-specific compilation and formatting issues.",
    loading_env: "Inspecting environment...",
    env_os: "Operating System",
    env_py_enc: "Python Encoding",
    env_fs_enc: "File System Encoding",
    env_locale_enc: "Locale Preferred Encoding",
    env_java_home: "JAVA_HOME Path",
    env_java_bin: "Java Executable",
    env_javac_bin: "Javac Compiler",
    env_mvn_home: "Maven Home Path",
    env_mvn_bin: "Maven Executable",
    env_eol_policy: "Line Ending (EOL) Policy",
    env_workspace_policy: "Workspace Isolation Policy",

    // Errors & Toast
    err_select_one_file: "Please select at least one directory or Java file in the source tree.",
    err_select_one_mci: "Please select at least one MCI for refactoring.",
    err_request_failed: "Request failed",
    err_no_mock_objects: "No mock objects detected in the selected scope.",
  },

  zh: {
    // Header & Navigation
    header_eyebrow: "MOCK 克隆检测与自动化重构 AGENT",
    header_badge: "FSE '26 研究原型",
    header_desc: "基于程序静态分析挖掘频繁存根集，并通过 LLM Agent 进行封装与集成，消除 Java 单元测试中的 Mockito 重复代码。",
    btn_env_check: "环境与安全诊断",
    status_ready: "就绪 · Ready",
    status_loading_tree: "正在读取文件树...",
    status_tree_loaded: "文件树读取完成",
    status_scanning: "正在提取 Mock Logic 并扫描...",
    status_scanned: (n) => `已扫描发现 ${n} 个 Mock Objects`,
    status_detecting: "正在挖掘频繁存根集并生成 MCIs...",
    status_detected: (n) => `成功识别 ${n} 个 Mock Clone Instances`,
    status_refactoring: "Agent 正在重构并在隔离沙箱中跑 Harness 验证...",
    status_refactor_done: "重构与三层保真验证已完成",
    status_applying: "正在将补丁写回原项目...",
    status_applied: "重构补丁已成功应用至源码",
    status_discarding: "正在丢弃重构提案...",
    status_discarded: "已安全丢弃本次重构提案",
    status_error: "执行出错 · Error",

    // Steps
    step1_title: "检测范围",
    step1_sub: "项目与文件池",
    step2_title: "Mock 对象",
    step2_sub: "候选对象池",
    step3_title: "克隆实例",
    step3_sub: "MCI 聚类结果",
    step4_title: "重构工作台",
    step4_sub: "Diff 审视与采纳",

    // Panel 1: Scope
    panel1_title: "建立检测范围池",
    panel1_desc: "选择目标项目根目录、限定 Package 前缀或挑选中意测试文件。",
    proj_root_label: "Java 项目根目录",
    proj_root_sub: "建议使用纯英文无空格路径，避免 Windows 编码问题",
    proj_root_ph: "C:\\projects\\my-java-project",
    btn_load_tree: "读取文件树",
    warning_path_title: "Windows 路径编码安全提示:",
    warning_path_desc: "检测到输入路径包含非 ASCII 字符或空格。在 Windows 上，JDK/Maven 底层字符集与系统 GBK 编码差异极易导致 javac 静默编译失败。强烈建议将测试项目置于纯英文路径（例如 C:\\projects\\dubbo）。",
    tree_title: "Java 源码树",
    btn_select_all: "全选",
    btn_clear_all: "清空",
    tree_empty_hint: "在上方输入项目根路径后点击“读取文件树”",
    package_filter_label: "Package 前缀过滤",
    package_filter_sub: "每行一个；空白表示不作限制",
    exclude_paths_label: "排除路径列表",
    exclude_paths_sub: "相对项目根目录路径，每行一个",
    toggle_deps: "解析 Maven/Gradle 依赖 Classpath",
    hint_deps: "勾选后通过临时文件解析依赖，不会修改目标项目任何构建配置。",
    btn_scan_mocks: "扫描 Mock Objects →",

    // Panel 2: Objects
    panel2_title: "筛选参与检测的 Mock Objects",
    panel2_desc: "勾选或剔除对象，仅让目标 Mock 参与后续频繁存根挖掘。",
    metric_selected_mocks: "已选择 / 可用总数",
    mock_search_ph: "按类型、变量名、测试文件或方法名过滤...",
    btn_select_visible: "选择当前可见项",
    btn_clear_visible: "反选当前可见项",
    th_variable: "变量名",
    th_mocked_class: "Mock 目标类",
    th_test_file: "所属测试文件",
    th_test_cases: "覆盖用例数",
    th_statements: "语句数",
    btn_back_scope: "← 返回范围配置",
    btn_detect_clones: "开始挖掘所选对象克隆 →",

    // Panel 3: MCIs
    panel3_title: "Mock Clone Instances (MCIs)",
    panel3_desc: "已识别的重复 Mock 打桩聚类实例。选择需要交由 Agent 实施重构的目标。",
    metric_mcis_found: "检测出的 MCI 总数",
    btn_back_objects: "← 返回对象池",
    btn_prepare_refactor: "准备自动重构 Agent →",
    mci_test_cases: "涉及的测试用例",
    mci_files: "目标测试文件",
    mci_stubs: "核心频繁打桩集 (Core Stub Set)",
    mci_sequences_toggle: "展开/勾选具体参与重构的 Sequence",

    // Panel 4: Refactoring Studio
    panel4_title: "自动化重构工作台 (Refactoring Studio)",
    panel4_desc: "多阶段 Agent 执行：封装 → 集成 → 三层保真度 Harness 验证，并提供 IDE 级 Diff 审视与采纳控制",
    model_label: "大模型选择",
    model_sub: "默认: gpt-5.6-terra",
    profile_label: "API 密钥 Profile",
    profile_sub: "对应环境变量中 JSON 配置的 Key 名称，默认 default",
    instruction_label: "给 Agent 的定制指令",
    instruction_sub: "可选 / 例如：'优先抽离到当前测试类私有 helper 方法'",
    toggle_pit: "运行 PIT 变异测试（执行耗时相对较长）",
    toggle_mock: "调试模式：本地模拟回填，不消耗真实 API Token",
    notice_title: "严格副本隔离机制",
    notice_body: "Agent 在独立工作区副本中执行编译、单测和 PIT 检验，在生成与确认阶段绝不直接修改原工程代码。",
    btn_back_clones: "← 返回 MCIs 列表",
    btn_run_agent: "生成隔离重构提案",

    // Diff Workbench & Decision
    diff_files_title: "改动涉及的文件:",
    view_unified: "统一 Diff 视图",
    view_sbs: "双栏并排对比",
    btn_copy_diff: "📋 复制 Diff 内容",
    diff_copied: "Diff 内容已复制到剪贴板！",
    decision_status_label: "提案审查状态:",
    status_awaiting_review: "待人工审查",
    status_applied_badge: "✓ 已安全写回原项目",
    status_discarded_badge: "✗ 已安全丢弃",
    btn_discard: "丢弃本次提案 (Discard)",
    btn_accept: "审查通过并采纳写回 (Accept)",
    result_title: "重构提案与三层保真证据",
    metric_compile: "语法编译",
    metric_tests: "测试用例回归",
    metric_pit: "变异得分保持",
    metric_tokens: "Token 消耗",

    // Environment Diagnostics Modal
    modal_env_title: "🛡️ 环境与安全诊断",
    modal_env_desc: "核验当前操作系统编码、Java/Maven 工具链以及文件隔离策略，杜绝 Windows 编码及 Spotless 格式校验失败。",
    loading_env: "正在核验环境...",
    env_os: "操作系统及平台",
    env_py_enc: "Python 内部字符集",
    env_fs_enc: "文件系统编码",
    env_locale_enc: "系统区域推荐编码",
    env_java_home: "JAVA_HOME 路径",
    env_java_bin: "Java 运行时",
    env_javac_bin: "Javac 编译器",
    env_mvn_home: "Maven 主目录",
    env_mvn_bin: "Maven 可执行命令",
    env_eol_policy: "换行符强制规范",
    env_workspace_policy: "隔离编译区安全策略",

    // Errors & Toast
    err_select_one_file: "请至少勾选一个源码目录或 Java 文件。",
    err_select_one_mci: "请至少勾选一个需要重构的 MCI 实例。",
    err_request_failed: "请求执行失败",
    err_no_mock_objects: "在所选范围内未发现任何 Mock 对象。",
  }
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function t(key, ...args) {
  const dict = I18N[state.currentLang] || I18N.en;
  const val = dict[key];
  if (typeof val === "function") return val(...args);
  return val || key;
}

function setLanguage(lang) {
  state.currentLang = lang;
  localStorage.setItem("clonedemocker_lang", lang);
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";

  $("#lang-en").classList.toggle("active", lang === "en");
  $("#lang-zh").classList.toggle("active", lang === "zh");

  // Update all static text with data-i18n
  $$("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (I18N[lang] && I18N[lang][key]) {
      el.textContent = I18N[lang][key];
    }
  });

  // Update placeholders
  $$("[data-i18n-ph]").forEach((el) => {
    const key = el.getAttribute("data-i18n-ph");
    if (I18N[lang] && I18N[lang][key]) {
      el.placeholder = I18N[lang][key];
    }
  });

  // Re-render dynamic elements
  if (state.mocks.length) renderMocks();
  if (state.instances.length) renderInstances();
  if (state.lastAgentPayload) renderAgentResult(state.lastAgentPayload);
}

function status(message, dotColor = "var(--status-ready)") {
  $("#global-status").textContent = message;
  const dot = $("#status-dot");
  if (dot) {
    dot.style.backgroundColor = dotColor;
    dot.style.boxShadow = `0 0 8px ${dotColor}`;
  }
}

function toast(message, isError = true) {
  const node = $("#toast");
  node.textContent = message;
  node.className = isError ? "show error" : "show";
  clearTimeout(node.timer);
  node.timer = setTimeout(() => {
    node.className = "";
  }, 5000);
}

async function request(url, options) {
  const settings = options || {};
  settings.headers = { "Content-Type": "application/json", ...(settings.headers || {}) };
  const response = await fetch(url, settings);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || t("err_request_failed"));
  return payload;
}

function showStep(number) {
  ["scope", "objects", "clones", "agent"].forEach((name, index) => {
    const panel = $("#" + name + "-panel");
    if (panel) panel.classList.toggle("hidden", index + 1 !== number);
  });
  $$(".step").forEach((step, index) => step.classList.toggle("active", index + 1 === number));
}

function treeNode(node) {
  const li = document.createElement("li");
  li.className = node.type;
  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.className = "scope-item";
  input.value = node.path;
  const text = document.createElement("span");
  text.textContent = (node.type === "directory" ? "📁 " : "📄 ") + node.name;
  label.append(input, text);
  li.append(label);
  if (node.children) {
    const ul = document.createElement("ul");
    node.children.forEach((child) => ul.append(treeNode(child)));
    li.append(ul);
    input.addEventListener("change", () => {
      li.querySelectorAll("input.scope-item").forEach((child) => (child.checked = input.checked));
    });
  }
  return li;
}

// ================= Windows Path Encoding Inspector =================
function checkPathSafety(path) {
  const warningBox = $("#path-warning-box");
  if (!warningBox) return;
  // Check for non-ASCII characters or spaces in path
  const hasNonAscii = /[^\x00-\x7F]/.test(path);
  const hasSpace = /\s/.test(path);
  if (hasNonAscii || hasSpace) {
    warningBox.classList.remove("hidden");
  } else {
    warningBox.classList.add("hidden");
  }
}

$("#project-root").addEventListener("input", (e) => {
  checkPathSafety(e.target.value.trim());
});

// ================= Step 1: Scope Events =================
$("#load-tree").addEventListener("click", async () => {
  try {
    const root = $("#project-root").value.trim();
    checkPathSafety(root);
    status(t("status_loading_tree"), "var(--status-active)");
    const tree = await request("/api/tree?projectRoot=" + encodeURIComponent(root));
    const container = $("#source-tree");
    container.className = "tree";
    container.replaceChildren();
    const ul = document.createElement("ul");
    ul.append(treeNode(tree));
    container.append(ul);
    status(t("status_tree_loaded"), "var(--status-ready)");
  } catch (error) {
    status(t("status_error"), "var(--status-error)");
    toast(error.message);
  }
});

$("#select-all-files").onclick = () => $$(".scope-item").forEach((item) => (item.checked = true));
$("#clear-files").onclick = () => $$(".scope-item").forEach((item) => (item.checked = false));

$("#scan-mocks").addEventListener("click", async () => {
  try {
    const selected = $$(".scope-item:checked").map((item) => item.value);
    if (!selected.length) throw new Error(t("err_select_one_file"));
    const includePaths = selected.filter(
      (path) => !selected.some((parent) => parent !== path && (parent === "." || path.startsWith(parent + "/")))
    );
    status(t("status_scanning"), "var(--status-active)");
    const result = await request("/api/detection/scan", {
      method: "POST",
      body: JSON.stringify({
        projectRoot: $("#project-root").value.trim(),
        includePaths: includePaths,
        excludePaths: $("#excludes").value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean),
        packagePrefixes: $("#packages").value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean),
        resolveDependencies: $("#resolve-dependencies").checked,
      }),
    });
    state.runId = result.runId;
    state.mocks = (result.mockObjects || []).map((item) => ({ ...item, selected: true }));
    renderMocks();
    showStep(2);
    status(t("status_scanned", state.mocks.length), "var(--status-ready)");
  } catch (error) {
    status(t("status_error"), "var(--status-error)");
    toast(error.message);
  }
});

// ================= Step 2: Objects Events =================
function renderMocks() {
  const query = ($("#mock-search").value || "").toLowerCase();
  state.visibleMocks = state.mocks.filter((item) => {
    const haystack = [item.variableName, item.mockedType, item.testFilePath, (item.testMethods || []).join(" ")]
      .join(" ")
      .toLowerCase();
    return haystack.includes(query);
  });
  const tbody = $("#mock-table");
  tbody.replaceChildren();
  state.visibleMocks.forEach((item) => {
    const tr = document.createElement("tr");
    const checkTd = document.createElement("td");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = item.selected;
    input.addEventListener("change", () => {
      item.selected = input.checked;
      updateObjectCount();
    });
    checkTd.append(input);

    const varTd = document.createElement("td");
    varTd.innerHTML = `<code>${escapeHtml(item.variableName || "-")}</code>`;

    const typeTd = document.createElement("td");
    typeTd.innerHTML = `<strong>${escapeHtml(item.mockedType || "-")}</strong>`;

    const fileTd = document.createElement("td");
    fileTd.className = "subtle";
    fileTd.textContent = item.testFilePath || "-";

    const testTd = document.createElement("td");
    testTd.textContent = (item.testMethods || []).length;

    const stmtTd = document.createElement("td");
    stmtTd.textContent = item.statementCount || 0;

    tr.append(checkTd, varTd, typeTd, fileTd, testTd, stmtTd);
    tbody.append(tr);
  });
  updateObjectCount();
}

function updateObjectCount() {
  const selectedCount = state.mocks.filter((x) => x.selected).length;
  $("#object-count").textContent = `${selectedCount} / ${state.mocks.length}`;
}

$("#mock-search").addEventListener("input", renderMocks);

$("#select-visible").onclick = () => {
  state.visibleMocks.forEach((item) => (item.selected = true));
  renderMocks();
};

$("#clear-visible").onclick = () => {
  state.visibleMocks.forEach((item) => (item.selected = false));
  renderMocks();
};

$("#back-scope").onclick = () => showStep(1);

$("#detect-clones").addEventListener("click", async () => {
  try {
    const selectedMockIds = state.mocks.filter((item) => item.selected).map((item) => item.id);
    if (!selectedMockIds.length) throw new Error(t("err_no_mock_objects"));
    status(t("status_detecting"), "var(--status-active)");
    const result = await request("/api/detection/detect", {
      method: "POST",
      body: JSON.stringify({
        runId: state.runId,
        selectedMockIds: selectedMockIds,
      }),
    });
    state.instances = (result.instances || []).map((item) => ({ ...item, selected: true }));
    renderInstances();
    showStep(3);
    status(t("status_detected", state.instances.length), "var(--status-ready)");
  } catch (error) {
    status(t("status_error"), "var(--status-error)");
    toast(error.message);
  }
});

// ================= Step 3: MCI Events =================
function renderInstances() {
  const list = $("#mci-list");
  list.replaceChildren();
  $("#mci-count").textContent = state.instances.length;

  state.instances.forEach((instance) => {
    const card = document.createElement("div");
    card.className = "mci";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = instance.selected;
    checkbox.addEventListener("change", () => (instance.selected = checkbox.checked));

    const content = document.createElement("div");
    const h3 = document.createElement("h3");
    h3.textContent = instance.id;

    const filesDesc = document.createElement("p");
    filesDesc.textContent = `${t("mci_files")}: ${(instance.files || []).join(", ") || "-"}`;

    const chips = document.createElement("div");
    chips.className = "chips";
    (instance.stubSignatures || []).forEach((sig) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = sig;
      chips.append(chip);
    });

    content.append(h3, filesDesc, chips);

    // Sequences toggle
    if (instance.sequences && instance.sequences.length) {
      const details = document.createElement("details");
      details.className = "mci-sequences";
      const summary = document.createElement("summary");
      summary.textContent = `${t("mci_sequences_toggle")} (${instance.sequences.length})`;
      details.append(summary);

      instance.sequences.forEach((seq) => {
        const row = document.createElement("div");
        row.className = "sequence-row";
        const seqCheck = document.createElement("input");
        seqCheck.type = "checkbox";
        seqCheck.checked = seq.selected !== false;
        seqCheck.addEventListener("change", () => (seq.selected = seqCheck.checked));
        const seqLabel = document.createElement("span");
        seqLabel.textContent = `${seq.methodName || seq.testMethod || "seq"} (ID: ${seq.mockObjectId})`;
        row.append(seqCheck, seqLabel);
        details.append(row);
      });
      content.append(details);
    }

    const stat = document.createElement("div");
    stat.className = "mci-stat";
    stat.innerHTML = `<strong>${(instance.sequences || []).length}</strong><span>${t("mci_test_cases")}</span>`;

    card.append(checkbox, content, stat);
    list.append(card);
  });
}

$("#back-objects").onclick = () => showStep(2);
$("#prepare-refactor").onclick = () => {
  const selected = state.instances.filter((item) => item.selected);
  if (!selected.length) {
    toast(t("err_select_one_mci"));
    return;
  }
  showStep(4);
};

// ================= Step 4: Agent & IDE Studio Events =================
$("#back-clones").onclick = () => showStep(3);

$("#run-agent").addEventListener("click", async () => {
  try {
    const selectedMciIds = state.instances.filter((item) => item.selected).map((item) => item.id);
    if (!selectedMciIds.length) throw new Error(t("err_select_one_mci"));

    const sequenceSelection = {};
    state.instances.forEach((instance) => {
      if (instance.sequences) {
        sequenceSelection[instance.id] = instance.sequences
          .filter((s) => s.selected !== false)
          .map((s) => s.mockObjectId);
      }
    });

    status(t("status_refactoring"), "var(--status-active)");
    const result = await request("/api/refactoring/run", {
      method: "POST",
      body: JSON.stringify({
        runId: state.runId,
        selectedMciIds: selectedMciIds,
        model: $("#model").value.trim(),
        apiProfile: $("#api-profile").value.trim(),
        instruction: $("#agent-instruction").value.trim(),
        runPit: $("#run-pit").checked,
        useMock: $("#use-mock").checked,
        sequenceSelection: sequenceSelection,
      }),
    });
    state.lastAgentPayload = result;
    renderAgentResult(result);
    status(t("status_refactor_done"), "var(--status-ready)");
  } catch (error) {
    status(t("status_error"), "var(--status-error)");
    toast(error.message);
  }
});

// ================= Unified & Side-by-Side Diff Parser =================
function parseUnifiedDiff(rawDiff) {
  if (!rawDiff) return [];
  const files = [];
  const lines = rawDiff.split("\n");
  let currentFile = null;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("--- a/") || line.startsWith("--- ")) {
      const fileName = line.replace(/^--- [ab]\//, "").trim();
      currentFile = { fileName: fileName, hunks: [], rawText: "" };
      files.push(currentFile);
    } else if (line.startsWith("+++ b/") || line.startsWith("+++ ")) {
      if (currentFile && !currentFile.fileName) {
        currentFile.fileName = line.replace(/^\+\+\+ [ab]\//, "").trim();
      }
    } else if (line.startsWith("@@")) {
      if (!currentFile) {
        currentFile = { fileName: "Changes", hunks: [], rawText: "" };
        files.push(currentFile);
      }
      // parse @@ -oldStart,oldLen +newStart,newLen @@
      const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      let oldLine = match ? parseInt(match[1], 10) : 1;
      let newLine = match ? parseInt(match[2], 10) : 1;
      const hunk = { header: line, lines: [] };
      currentFile.hunks.push(hunk);
      currentFile.rawText += line + "\n";

      // Read lines until next @@ or EOF
      while (i + 1 < lines.length && !lines[i + 1].startsWith("@@") && !lines[i + 1].startsWith("--- ")) {
        i++;
        const dl = lines[i];
        currentFile.rawText += dl + "\n";
        if (dl.startsWith("+")) {
          hunk.lines.push({ type: "add", oldNo: "", newNo: newLine++, text: dl.substring(1) });
        } else if (dl.startsWith("-")) {
          hunk.lines.push({ type: "del", oldNo: oldLine++, newNo: "", text: dl.substring(1) });
        } else {
          hunk.lines.push({ type: "ctx", oldNo: oldLine++, newNo: newLine++, text: dl.substring(1) });
        }
      }
    }
  }
  if (!files.length && rawDiff.trim()) {
    files.push({ fileName: "Patch", hunks: [{ header: "@@ -1 +1 @@", lines: [{ type: "ctx", oldNo: 1, newNo: 1, text: rawDiff }] }], rawText: rawDiff });
  }
  return files;
}

function renderDiffView() {
  const container = $("#diff-content-area");
  if (!container) return;
  container.replaceChildren();

  const activeFile = state.parsedDiffFiles[state.activeDiffFileIndex];
  if (!activeFile) return;

  if (state.diffViewMode === "unified") {
    const table = document.createElement("table");
    table.className = "diff-table";
    activeFile.hunks.forEach((hunk) => {
      const hunkRow = document.createElement("tr");
      hunkRow.className = "diff-row-hunk";
      hunkRow.innerHTML = `<td class="diff-line-no">...</td><td class="diff-line-no">...</td><td class="diff-line-code">${escapeHtml(hunk.header)}</td>`;
      table.append(hunkRow);

      hunk.lines.forEach((line) => {
        const tr = document.createElement("tr");
        tr.className = line.type === "add" ? "diff-row-add" : line.type === "del" ? "diff-row-del" : "diff-row-ctx";
        const sign = line.type === "add" ? "+" : line.type === "del" ? "-" : " ";
        tr.innerHTML = `
          <td class="diff-line-no">${line.oldNo}</td>
          <td class="diff-line-no">${line.newNo}</td>
          <td class="diff-line-code">${sign} ${escapeHtml(line.text)}</td>
        `;
        table.append(tr);
      });
    });
    container.append(table);
  } else {
    // Side-by-Side View
    const sbsWrapper = document.createElement("div");
    sbsWrapper.className = "sbs-wrapper";

    const leftCol = document.createElement("div");
    leftCol.className = "sbs-col";
    leftCol.innerHTML = `<div class="sbs-header">Original Code (Before)</div>`;
    const leftTable = document.createElement("table");
    leftTable.className = "diff-table";

    const rightCol = document.createElement("div");
    rightCol.className = "sbs-col";
    rightCol.innerHTML = `<div class="sbs-header">Refactored Code (After)</div>`;
    const rightTable = document.createElement("table");
    rightTable.className = "diff-table";

    activeFile.hunks.forEach((hunk) => {
      hunk.lines.forEach((line) => {
        if (line.type === "del") {
          const ltr = document.createElement("tr");
          ltr.className = "diff-row-del";
          ltr.innerHTML = `<td class="diff-line-no">${line.oldNo}</td><td class="diff-line-code">- ${escapeHtml(line.text)}</td>`;
          leftTable.append(ltr);

          const rtr = document.createElement("tr");
          rtr.innerHTML = `<td class="diff-line-no"></td><td class="diff-line-code"></td>`;
          rightTable.append(rtr);
        } else if (line.type === "add") {
          const ltr = document.createElement("tr");
          ltr.innerHTML = `<td class="diff-line-no"></td><td class="diff-line-code"></td>`;
          leftTable.append(ltr);

          const rtr = document.createElement("tr");
          rtr.className = "diff-row-add";
          rtr.innerHTML = `<td class="diff-line-no">${line.newNo}</td><td class="diff-line-code">+ ${escapeHtml(line.text)}</td>`;
          rightTable.append(rtr);
        } else {
          const ltr = document.createElement("tr");
          ltr.innerHTML = `<td class="diff-line-no">${line.oldNo}</td><td class="diff-line-code">  ${escapeHtml(line.text)}</td>`;
          leftTable.append(ltr);

          const rtr = document.createElement("tr");
          rtr.innerHTML = `<td class="diff-line-no">${line.newNo}</td><td class="diff-line-code">  ${escapeHtml(line.text)}</td>`;
          rightTable.append(rtr);
        }
      });
    });

    leftCol.append(leftTable);
    rightCol.append(rightTable);
    sbsWrapper.append(leftCol, rightCol);
    container.append(sbsWrapper);
  }
}

// Diff View Mode Switches
$("#btn-view-unified").onclick = () => {
  state.diffViewMode = "unified";
  $("#btn-view-unified").classList.add("active");
  $("#btn-view-sbs").classList.remove("active");
  renderDiffView();
};

$("#btn-view-sbs").onclick = () => {
  state.diffViewMode = "sbs";
  $("#btn-view-sbs").classList.add("active");
  $("#btn-view-unified").classList.remove("active");
  renderDiffView();
};

$("#btn-copy-diff").onclick = () => {
  if (state.lastAgentPayload && state.lastAgentPayload.diff) {
    navigator.clipboard.writeText(state.lastAgentPayload.diff);
    toast(t("diff_copied"), false);
  }
};

function renderAgentResult(payload) {
  const container = $("#agent-result");
  container.classList.remove("hidden");

  // Summary Card
  const summaryBox = $("#result-summary-box");
  summaryBox.innerHTML = `
    <div class="result-summary-card">
      <h4>${escapeHtml(payload.summary || t("result_title"))}</h4>
      <p class="subtle" style="margin: 0;">${escapeHtml(payload.reason || "-")}</p>
    </div>
  `;

  // Metrics Grid
  const evidence = (payload.harness && payload.harness.candidate) || payload.evidence || {};
  const usage = payload.usage || {};
  const metricsBox = $("#result-metrics-box");
  metricsBox.innerHTML = `
    <div class="result-grid">
      <div>
        <small>${t("metric_compile")}</small>
        <strong class="${(evidence.compile_status || evidence.compileStatus || "").toLowerCase()}">
          ${evidence.compile_status || evidence.compileStatus || "PASSED"}
        </strong>
      </div>
      <div>
        <small>${t("metric_tests")}</small>
        <strong class="${(evidence.test_status || evidence.testStatus || "").toLowerCase()}">
          ${evidence.test_status || evidence.testStatus || "PASSED"}
        </strong>
      </div>
      <div>
        <small>${t("metric_pit")}</small>
        <strong>${evidence.pit_status || evidence.pitStatus || "NOT_RUN"}</strong>
      </div>
      <div>
        <small>${t("metric_tokens")}</small>
        <strong>${usage.total_tokens || usage.totalTokens || 0}</strong>
      </div>
    </div>
  `;

  // Diagnostics Box
  const diagBox = $("#result-diagnostics-box");
  diagBox.replaceChildren();
  const diags = evidence.diagnostics || [];
  if (diags.length) {
    const diagEl = document.createElement("div");
    diagEl.className = "diagnostics";
    diagEl.textContent = diags.join("\n");
    diagBox.append(diagEl);
  }

  // Parse Diff & Tabs
  state.parsedDiffFiles = parseUnifiedDiff(payload.diff || "");
  state.activeDiffFileIndex = 0;
  const tabsContainer = $("#diff-file-tabs");
  tabsContainer.replaceChildren();

  state.parsedDiffFiles.forEach((file, idx) => {
    const btn = document.createElement("button");
    btn.className = `file-tab ${idx === 0 ? "active" : ""}`;
    btn.type = "button";
    btn.textContent = file.fileName;
    btn.onclick = () => {
      state.activeDiffFileIndex = idx;
      $$(".file-tab").forEach((b, i) => b.classList.toggle("active", i === idx));
      renderDiffView();
    };
    tabsContainer.append(btn);
  });

  renderDiffView();

  // Reset Decision Status
  const badge = $("#decision-badge");
  badge.className = "decision-badge badge-pending";
  badge.textContent = t("status_awaiting_review");
  const acceptBtn = $("#btn-accept-proposal");
  const discardBtn = $("#btn-discard-proposal");
  acceptBtn.disabled = false;
  discardBtn.disabled = false;
  acceptBtn.textContent = `✓ ${t("btn_accept")}`;

  // Accept Handler
  acceptBtn.onclick = async () => {
    try {
      status(t("status_applying"), "var(--status-active)");
      await request("/api/refactoring/apply", {
        method: "POST",
        body: JSON.stringify({
          runId: state.runId,
          proposalId: payload.proposalId,
        }),
      });
      badge.className = "decision-badge badge-applied";
      badge.textContent = t("status_applied_badge");
      acceptBtn.disabled = true;
      discardBtn.disabled = true;
      status(t("status_applied"), "var(--status-ready)");
      toast(t("status_applied"), false);
    } catch (err) {
      status(t("status_error"), "var(--status-error)");
      toast(err.message);
    }
  };

  // Discard Handler
  discardBtn.onclick = async () => {
    try {
      status(t("status_discarding"), "var(--status-warning)");
      await request("/api/refactoring/discard", {
        method: "POST",
        body: JSON.stringify({
          runId: state.runId,
          proposalId: payload.proposalId,
        }),
      });
      badge.className = "decision-badge badge-discarded";
      badge.textContent = t("status_discarded_badge");
      acceptBtn.disabled = true;
      discardBtn.disabled = true;
      status(t("status_discarded"), "var(--text-muted)");
      toast(t("status_discarded"), false);
    } catch (err) {
      status(t("status_error"), "var(--status-error)");
      toast(err.message);
    }
  };
}

// ================= Environment & Safety Modal Dialog =================
const envModal = $("#env-modal");
const envGrid = $("#env-grid-content");

$("#btn-open-env").onclick = async () => {
  envModal.classList.add("show");
  envGrid.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 20px;">${t("loading_env")}</div>`;
  try {
    const data = await request("/api/env/check");
    envGrid.innerHTML = `
      <div class="env-item">
        <span class="env-key">${t("env_os")}</span>
        <span class="env-val">${escapeHtml(data.platform || data.osName || "-")}</span>
      </div>
      <div class="env-item">
        <span class="env-key">${t("env_py_enc")}</span>
        <span class="env-val">${escapeHtml(data.pythonEncoding || "-")}</span>
      </div>
      <div class="env-item">
        <span class="env-key">${t("env_fs_enc")}</span>
        <span class="env-val">${escapeHtml(data.fsEncoding || "-")}</span>
      </div>
      <div class="env-item">
        <span class="env-key">${t("env_locale_enc")}</span>
        <span class="env-val">${escapeHtml(data.preferredEncoding || "-")}</span>
      </div>
      <div class="env-item highlight">
        <span class="env-key">${t("env_java_home")}</span>
        <span class="env-val">${escapeHtml(data.javaHome || "-")}</span>
      </div>
      <div class="env-item highlight">
        <span class="env-key">${t("env_mvn_home")}</span>
        <span class="env-val">${escapeHtml(data.mavenHome || "-")}</span>
      </div>
      <div class="env-item highlight">
        <span class="env-key">${t("env_eol_policy")}</span>
        <span class="env-val">${escapeHtml(data.eolPolicy || "-")}</span>
      </div>
      <div class="env-item highlight">
        <span class="env-key">${t("env_workspace_policy")}</span>
        <span class="env-val">${escapeHtml(data.workspacePolicy || "-")}</span>
      </div>
    `;
  } catch (err) {
    envGrid.innerHTML = `<div style="color: var(--status-error); padding: 12px;">Failed to load env: ${escapeHtml(err.message)}</div>`;
  }
};

$("#btn-close-env").onclick = () => envModal.classList.remove("show");
envModal.onclick = (e) => {
  if (e.target === envModal) envModal.classList.remove("show");
};

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ================= Language Switcher Listeners =================
$("#lang-en").onclick = () => setLanguage("en");
$("#lang-zh").onclick = () => setLanguage("zh");

// Init
setLanguage(state.currentLang);
checkPathSafety($("#project-root").value.trim());
