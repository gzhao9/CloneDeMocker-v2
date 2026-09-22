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
  progressSeen: {},
  lastScanFile: "",
  lastPhaseKey: "",
  refactoringInFlight: false,
  lastRefactorJobId: null,
  progressStartedAt: null,
  progressTimer: null,
  agentResults: [],
  activeAgentResult: 0,
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
    busy_scan_title: "Scanning mock logic",
    busy_scan_detail: "Analyzing selected Java files. Large multi-module projects can take a few minutes.",
    busy_detect_title: "Mining clone instances",
    busy_detect_detail: "Building frequent stub sets from the selected mock objects.",
    busy_refactor_title: "Generating and verifying refactoring",
    busy_refactor_detail: "The candidate is being validated in an isolated workspace. This can take several minutes.",
    busy_elapsed: (seconds) => `Elapsed: ${seconds}s`,

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
    btn_browse: "📁 Browse…",
    warning_path_title: "Windows Path Encoding Warning:",
    warning_path_desc: "Non-ASCII characters or spaces detected in path. On Windows, native javac/Maven encoding mismatches may cause silent compilation failures. We strongly recommend placing the target project in a pure ASCII path (e.g., C:\\projects\\dubbo).",
    tree_title: "Java Source Tree",
    btn_expand_all: "Expand All",
    btn_collapse_all: "Collapse All",
    btn_select_all: "Select All",
    btn_clear_all: "Clear",
    tree_empty_hint: "Enter project path above and click 'Load File Tree'",
    package_filter_label: "Package Prefixes",
    package_filter_sub: "One per line; leave blank for all",
    exclude_paths_label: "Exclude Patterns",
    exclude_paths_sub: "Relative paths or folders to skip",
    toggle_deps: "Enhanced Maven/Gradle Type Resolution (Optional)",
    hint_deps: "Does not compile the project. It only reads the test classpath for more precise type names; mock detection falls back to source-only analysis if skipped or unavailable.",
    btn_scan_mocks: "Scan Mock Objects →",

    // Panel 2: Objects
    panel2_title: "Select Mock Objects Pool",
    panel2_desc: "Filter and exclude objects you do not wish to participate in Frequent Stub Set Mining.",
    metric_selected_mocks: "Selected / Available",
    warning_zero_mocks: "No mock objects were found in the selected scope. An empty log below usually means none of the selected files import org.mockito.* (check that you selected directories that actually contain Mockito-based tests, e.g. under src/test/java — not interface/constant-only folders). A '[WARN] Skipping file' line means some files were skipped due to parse errors.",
    scan_log_toggle: "Scan Log / Diagnostics",
    scan_log_empty: "(No output from the detector — no Mockito-related files matched the selected scope.)",
    mock_search_ph: "Filter by type, variable, file, or method...",
    btn_select_visible: "Select Visible",
    btn_clear_visible: "Deselect Visible",
    btn_select_all_mocks: "Select All",
    btn_clear_all_mocks: "Deselect All",
    filter_all_roles: "All roles",
    filter_mocks: "Mocks only",
    filter_spies: "Spies only",
    filter_all_locations: "All locations",
    filter_test_locations: "Used in test cases",
    filter_other_locations: "Other locations",
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
    btn_accept: "Apply Verified Refactoring",
    btn_export_data: "Save report to data/",
    export_done: "Merged into data/{project}/refactoring/{setup}: {written} MCI(s) written this time, {total} stored in total, {ok} successful.",
    auto_save_done: "Results were saved automatically to data/{project}/refactoring/{setup}.",
    auto_save_failed: "Automatic save to data/ failed: {error}. Use Save report to data/ to retry.",
    export_none: "This batch produced no results to export.",
    export_cctr_ok: " CCTR computed over {n} test method(s).",
    export_cctr_failed: " CCTR could not be computed; the data itself was written (see console).",
    btn_force_apply: "Force Apply Despite Risks",
    status_verified: "✓ Verified — ready to apply",
    status_review_required: "Review required — verification did not pass",
    result_title: "Refactoring Proposal Evidence",
    metric_compile: "Compilation",
    metric_tests: "Regression Tests",
    metric_pit: "Mutation Score",
    metric_tokens: "Token Usage",
    metric_baseline: "Baseline (before change)",
    metric_scope: "Verified scope",
    busy_cache_title: "Checking for cached proposals",
    busy_cache_detail: "Fingerprinting the sources behind each selected MCI.",
    err_already_running: "A refactoring batch is already running; wait for it to finish or reload the page.",
    err_lost_job: "Lost contact with the refactoring job; it may still be running on the server",
    busy_reconnecting: "Connection lost, retrying ({n}/{max})…",
    busy_reattached: "Reattached to a refactoring job that was already running",
    cache_prompt_title: "Existing refactoring proposals found",
    cache_prompt_body: "{n} of {total} selected MCI(s) already have a verified proposal from an earlier run. Reusing them spends no tokens, but the answers come from that earlier run.",
    cache_prompt_reuse: "Use cache",
    cache_prompt_fresh: "Clear cache and regenerate",
    cache_prompt_cancel: "Cancel",
    saved_detection_title: "Saved detection found",
    saved_detection_body: "data/{project} already holds a detection result: {mcis} MCIs from {mos} mock objects, detected {when}. Use it and skip scanning and detection?",
    saved_detection_moved: "\n\nNote: it was detected at {recorded}, not the current project path. The MCIs' file paths may not resolve.",
    saved_detection_use: "Use saved result",
    saved_detection_fresh: "Detect again",
    status_detection_restored: (n) => `Loaded ${n} Mock Clone Instances from data/`,
    detection_saved_to_data: "Detection saved to data/ — next time it can be reused without scanning.",
    cache_cleared: "Cleared {n} cached proposal(s); regenerating from scratch.",
    metric_model_calls: "Model Calls",
    status_baseline_broken: "Environment not ready — no model call was made",

    // Preflight
    preflight_ok_title: "Project is ready",
    preflight_warn_title: "Check the project root",
    preflight_blocker_title: "This project cannot be verified on this host",
    preflight_probe_toggle: "Build probe output",
    preflight_checking: "Checking project...",
    btn_use_reactor_root: "Use reactor root instead",
    preflight_sag_hint: "The source can still be scanned, but refactorings cannot be verified until the build works. Setting the project up in a container (SAG) is the usual fix.",

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
    busy_scan_title: "正在扫描 Mock 逻辑",
    busy_scan_detail: "正在分析所选 Java 文件；大型多模块项目可能需要数分钟。",
    busy_detect_title: "正在挖掘克隆实例",
    busy_detect_detail: "正在从已选 Mock 对象中构建频繁打桩集合。",
    busy_refactor_title: "正在生成并验证重构",
    busy_refactor_detail: "候选补丁正在隔离工作区中验证，可能需要数分钟。",
    busy_elapsed: (seconds) => `已耗时：${seconds} 秒`,

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
    btn_browse: "📁 浏览选择…",
    warning_path_title: "Windows 路径编码安全提示:",
    warning_path_desc: "检测到输入路径包含非 ASCII 字符或空格。在 Windows 上，JDK/Maven 底层字符集与系统 GBK 编码差异极易导致 javac 静默编译失败。强烈建议将测试项目置于纯英文路径（例如 C:\\projects\\dubbo）。",
    tree_title: "Java 源码树",
    btn_expand_all: "全部展开",
    btn_collapse_all: "全部折叠",
    btn_select_all: "全选",
    btn_clear_all: "清空",
    tree_empty_hint: "在上方输入项目根路径后点击“读取文件树”",
    package_filter_label: "Package 前缀过滤",
    package_filter_sub: "每行一个；空白表示不作限制",
    exclude_paths_label: "排除路径列表",
    exclude_paths_sub: "相对项目根目录路径，每行一个",
    toggle_deps: "增强 Maven/Gradle 类型解析（可选）",
    hint_deps: "不会编译项目，仅读取 test classpath 以提高类型名称精度；未勾选或依赖解析失败时，仍会自动使用纯源码检测 Mock。",
    btn_scan_mocks: "扫描 Mock Objects →",

    // Panel 2: Objects
    panel2_title: "筛选参与检测的 Mock Objects",
    panel2_desc: "勾选或剔除对象，仅让目标 Mock 参与后续频繁存根挖掘。",
    metric_selected_mocks: "已选择 / 可用总数",
    warning_zero_mocks: "在所选范围内未发现任何 Mock 对象。若下方日志为空，通常说明所勾选的目录/文件中没有 import org.mockito.* 的代码（请检查是否勾选到了真正包含 Mockito 单测的目录，例如 src/test/java 下的模块，而不是接口/常量定义等目录）；若日志中出现 [WARN] Skipping file，则说明部分文件因解析失败被跳过。",
    scan_log_toggle: "扫描日志 / 诊断信息",
    scan_log_empty: "（检测器没有输出任何日志——说明所选范围内没有匹配到含 Mockito 相关 import 的文件。）",
    mock_search_ph: "按类型、变量名、测试文件或方法名过滤...",
    btn_select_visible: "选择当前可见项",
    btn_clear_visible: "反选当前可见项",
    btn_select_all_mocks: "全选全部",
    btn_clear_all_mocks: "取消全选",
    filter_all_roles: "全部角色",
    filter_mocks: "仅 Mock",
    filter_spies: "仅 Spy",
    filter_all_locations: "全部位置",
    filter_test_locations: "测试用例中使用",
    filter_other_locations: "其他位置",
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
    btn_accept: "应用已验证的重构",
    btn_export_data: "报告存入 data/",
    export_done: "已并入 data/{project}/refactoring/{setup}：本次写入 {written} 个 MCI，累计 {total} 个，成功 {ok} 个。",
    auto_save_done: "结果已自动保存到 data/{project}/refactoring/{setup}。",
    auto_save_failed: "自动保存到 data/ 失败：{error}。请点击“报告存入 data/”重试。",
    export_none: "这一批没有可导出的结果。",
    export_cctr_ok: " CCTR 已覆盖 {n} 个测试方法。",
    export_cctr_failed: " CCTR 未能计算，但数据本身已写入（详见控制台）。",
    btn_force_apply: "忽略风险，强制应用",
    status_verified: "✓ 已验证，可安全应用",
    status_review_required: "需要审查：验证门禁未全部通过",
    result_title: "重构提案与三层保真证据",
    metric_compile: "语法编译",
    metric_tests: "测试用例回归",
    metric_pit: "变异得分保持",
    metric_tokens: "Token 消耗",
    metric_baseline: "基线（改动前）",
    metric_scope: "验证范围",
    busy_cache_title: "正在检查缓存",
    busy_cache_detail: "正在为每个所选 MCI 计算源码指纹。",
    err_already_running: "已有一批重构在运行中；请等它跑完，或刷新页面。",
    err_lost_job: "与重构作业失去联系；它可能仍在服务端运行",
    busy_reconnecting: "连接中断，正在重试（{n}/{max}）…",
    busy_reattached: "已重新接上正在运行的重构作业",
    cache_prompt_title: "检测到已有的重构方案",
    cache_prompt_body: "所选 {total} 个 MCI 中有 {n} 个已有上一次生成并验证过的方案。复用不消耗 token，但答案来自上一次运行。",
    cache_prompt_reuse: "使用缓存",
    cache_prompt_fresh: "清除缓存，重新生成",
    cache_prompt_cancel: "取消",
    saved_detection_title: "发现已保存的检测结果",
    saved_detection_body: "data/{project} 中已有一份检测结果：{mos} 个 mock 对象中识别出 {mcis} 个 MCI，检测于 {when}。直接使用这份结果、跳过扫描和检测？",
    saved_detection_moved: "\n\n注意：这份结果是在 {recorded} 下检测的，与当前项目路径不同，MCI 里的文件路径可能对不上。",
    saved_detection_use: "使用已有结果",
    saved_detection_fresh: "重新检测",
    status_detection_restored: (n) => `已从 data/ 载入 ${n} 个 Mock 克隆实例`,
    detection_saved_to_data: "检测结果已存入 data/，下次可以直接复用、跳过扫描。",
    cache_cleared: "已清除 {n} 个缓存方案，将重新生成。",
    metric_model_calls: "模型调用次数",
    status_baseline_broken: "环境未就绪 —— 未发起任何模型调用",

    // Preflight
    preflight_ok_title: "项目可用",
    preflight_warn_title: "请确认项目根目录",
    preflight_blocker_title: "该项目在本机无法完成验证",
    preflight_probe_toggle: "构建探针输出",
    preflight_checking: "正在检查项目...",
    btn_use_reactor_root: "改用 reactor 根目录",
    preflight_sag_hint: "源码仍然可以扫描，但在构建跑通之前无法验证重构结果。通常的解决办法是用容器（SAG）把项目配置起来。",

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

// 进度窗只有一个，但可能有多个异步流程同时以为自己拥有它：点击处理器、页面加载时的
// 作业重连、上一次点击残留的轮询循环。它们各自在 finally 里无条件 setProgress(false)，
// 于是谁先结束谁就把别人的进度窗关掉——界面上看起来是"任务没了"，实际后台还在跑。
// 认领时拿一个号，只有持号人能关；号被别人接手过，旧持有者的关闭请求就作废。
// There is one progress overlay but several async flows can each believe they own it: the
// click handler, the page-load job reattach, and a polling loop left over from an earlier
// click. Each called setProgress(false) unconditionally in its finally, so whichever finished
// first closed someone else's overlay — the task looked gone while it kept running.
// Claiming takes a ticket; only the ticket holder can close it, and once another flow has
// claimed, a stale holder's close request is void.
let progressTicket = 0;

function claimProgress(title, detail, batch = true) {
  setProgress(true, title, detail, batch);
  progressTicket += 1;
  return progressTicket;
}

function releaseProgress(ticket) {
  if (ticket === progressTicket) setProgress(false);
}

// 扫描和克隆检测是"一件事跑到几成"，重构是"一批里第几个、成了几个"。原来两种任务共用
// 同一张卡片，于是扫描阶段也摆出了"当前 MCI / 整体"两条进度条和成功失败计数——那时候
// 连一个 MCI 都还没有，三个数字只能是 0，两条进度条永远同步走，纯属噪音。
// Scanning and clone mining are "how far along is this one thing"; refactoring is "which of a
// batch, and how many passed". One card served both, so scanning also displayed the
// current-MCI/overall pair and a success tally — with no MCI in existence yet, the numbers
// could only be zero and the two bars moved in lockstep, which is noise.
function setProgress(active, title = "", detail = "", batch = false) {
  const overlay = $("#progress-overlay");
  if (!overlay) return;
  if (!active) {
    overlay.classList.add("hidden");
    document.body.classList.remove("is-busy");
    clearInterval(state.progressTimer);
    state.progressTimer = null;
    return;
  }
  $("#progress-title").textContent = title;
  $("#progress-detail").textContent = detail;
  $("#progress-bar").style.width = "8%";
  $("#progress-current-percent").textContent = "0%";
  $("#progress-bar-overall").style.width = "0%";
  $("#progress-percent").textContent = "0%";
  // 计数要等第一次轮询拿到真实状态再显示，否则会先闪一下全 0 的假数据。
  // The tally waits for the first poll's real state; showing it now would flash zeros first.
  $("#progress-tally").hidden = !batch;
  $("#progress-overall-meter").hidden = !batch;
  $("#progress-current-label").textContent = batch ? "Current MCI" : "Progress";
  // 新任务从空日志开始，否则上一次运行的结果会留在框里冒充这一次的。
  // A new task starts with an empty log; otherwise the previous run's lines would sit there
  // posing as this one's.
  $("#progress-log").replaceChildren();
  state.progressSeen = {};
  state.lastPhaseKey = "";
  state.progressStartedAt = Date.now();
  const renderElapsed = () => {
    const seconds = Math.floor((Date.now() - state.progressStartedAt) / 1000);
    $("#progress-elapsed").textContent = t("busy_elapsed", seconds);
  };
  renderElapsed();
  clearInterval(state.progressTimer);
  state.progressTimer = setInterval(renderElapsed, 1000);
  overlay.classList.remove("hidden");
  document.body.classList.add("is-busy");
}

function updateScanProgress(completed, total, currentFile = "") {
  if (!total) return;
  const percent = Math.min(100, Math.round((completed / total) * 100));
  $("#progress-detail").textContent = `${t("busy_scan_detail")} ${completed.toLocaleString()} / ${total.toLocaleString()} Java files (${percent}%).`;
  $("#progress-bar").style.width = `${Math.max(8, percent)}%`;
  $("#progress-current-percent").textContent = `${percent}%`;
  // 扫描几千个文件时，一条静默移动的进度条说不出它卡在哪。检测器每 10 个文件报一次
  // 当前文件名，这里把它写进日志——卡住时一眼能看到停在哪个文件上。
  // A silent bar over thousands of files cannot say where it is stuck. The detector reports
  // the current file every ten, and logging it makes a stall point at the file it stalled on.
  if (currentFile && currentFile !== state.lastScanFile) {
    state.lastScanFile = currentFile;
    const log = $("#progress-log");
    const row = document.createElement("div");
    row.className = "dim";
    // 只甩一个文件名，读起来像半句话。写清楚这一行在说什么，日志才是日志。
    // A bare file name reads like half a sentence; saying what the line reports is what makes
    // it a log.
    row.textContent = `[${String(completed).padStart(String(total).length, " ")}/${total}] Scanning ${currentFile}`;
    log.append(row);
    while (log.childElementCount > 300) log.removeChild(log.firstElementChild);
    log.scrollTop = log.scrollHeight;
  }
}

function updateRefactoringProgress(job) {
  const items = job.items || [];
  const total = Math.max(1, job.total || 1);
  const currentIndex = Math.min(total - 1, job.current || 0);
  const item = items[currentIndex] || {};
  const currentPercent = Math.min(100, item.percent || 0);

  // 一批跑到一半时最该知道的三件事：成了几个、废了几个、还剩几个。之前只显示
  // "第 N 个 / 共 M 个"，一个跑了一半的批次里有多少已经失败，完全看不出来。
  // Half-way through a batch the three things worth knowing are how many succeeded, how many
  // failed and how many are left. The previous "MCI N of M" said nothing about how many of a
  // half-finished batch had already failed.
  const succeeded = items.filter((entry) => entry.state === "COMPLETED").length;
  const failed = items.filter((entry) => entry.state === "FAILED").length;
  // 只在当前项确实还在跑的时候才把它的进度算进整体。已经结束的项早就计入 succeeded/failed，
  // 再加一次它的 100% 会让整体冲过头（实测一个 3/3 跑完的批次算出 133%）。
  // The current item's progress counts toward the total only while it is actually running. A
  // finished one is already in succeeded/failed, and adding its 100% again overshoots — a
  // completed 3-of-3 batch measured 133%.
  const runningShare = item.state === "RUNNING" ? currentPercent / 100 : 0;
  const overall = Math.min(100, Math.round(((succeeded + failed + runningShare) / total) * 100));
  // 计数区只留结果：成了几个、废了几个。"还剩几个"不是结果，它是位置——放在整体进度条
  // 旁边写成 14 / 212 才看得出走到哪了，摆在上面反而要读者自己做减法。
  // The tally keeps outcomes only: how many passed, how many failed. "Remaining" is not an
  // outcome but a position, and it reads as one beside the overall bar — 14 / 212 shows where
  // the batch is, where a count at the top leaves the reader to subtract.
  $("#tally-succeeded").textContent = succeeded;
  $("#tally-failed").textContent = failed;
  $("#progress-tally").hidden = false;
  $("#progress-overall-meter").hidden = false;

  $("#progress-title").textContent = `Refactoring MCI ${currentIndex + 1} of ${total}`;
  $("#progress-detail").textContent = `${item.mciId || ""} · ${item.detail || item.phase || "Preparing"}`;
  $("#progress-bar").style.width = `${currentPercent}%`;
  $("#progress-current-percent").textContent = `${currentPercent}%`;
  $("#progress-bar-overall").style.width = `${overall}%`;
  $("#progress-percent").textContent = `${overall}%`;
  $("#progress-overall-position").textContent = `${succeeded + failed} / ${total} MCI`;
  // 生成阶段的 detail 形如 "[3/12] Integrating Foo::testBar"，把那个分数提到标签上：
  // 一个 MCI 有二十几条 sequence 时，百分比在窄区间里几乎不动，分数才看得出在推进。
  // A generation detail reads "[3/12] Integrating Foo::testBar"; lifting that fraction to the
  // label matters because with two dozen sequences the percentage barely moves inside its
  // narrow band while the fraction visibly advances.
  const step = /^\[(\d+)\/(\d+)\]/.exec(item.detail || "");
  $("#progress-current-position").textContent = step ? `step ${step[1]} / ${step[2]}` : "";

  appendProgressLog(items);
}

// 短名字够用了：日志一行只需要认出是哪个 MCI，包名在上面的明细行里已经完整显示过。
// The short name suffices: a log line only has to identify which MCI, and the package is
// already spelled out in full on the detail line above.
function shortMciId(mciId) {
  const [type, index] = String(mciId || "").split("::");
  return `${type.split(".").pop()}${index ? `::${index}` : ""}`;
}

function appendProgressLog(items) {
  const log = $("#progress-log");
  // 一个 MCI 从开始到结束可能要几分钟（复制工作区、冷编译、模型调用、回归测试）。只在结束
  // 时记一行，这几分钟里日志框就是空的，看不出还在动还是卡死了。阶段变化每一步都记下来，
  // 这个框才真的说明问题。
  // One MCI can take minutes end to end — copying the workspace, a cold compile, the model
  // calls, the regression run. Logging only on completion leaves the box empty throughout,
  // indistinguishable from a hang. Recording each phase change is what makes it informative.
  const running = items[Math.min(items.length - 1, 0)] && items.find((entry) => entry.state === "RUNNING");
  if (running) {
    const phaseKey = `${running.mciId}:${running.phase}`;
    if (state.lastPhaseKey !== phaseKey) {
      state.lastPhaseKey = phaseKey;
      const row = document.createElement("div");
      row.className = "dim";
      row.textContent = `${shortMciId(running.mciId)} · ${running.detail || running.phase}`;
      log.append(row);
      while (log.childElementCount > 300) log.removeChild(log.firstElementChild);
      log.scrollTop = log.scrollHeight;
    }
  }
  items.forEach((entry, index) => {
    const seen = state.progressSeen[entry.mciId];
    if (seen === entry.state) return;
    state.progressSeen[entry.mciId] = entry.state;
    if (entry.state !== "COMPLETED" && entry.state !== "FAILED") return;

    const row = document.createElement("div");
    const ok = entry.state === "COMPLETED";
    row.className = ok ? "ok" : "bad";
    // 失败时把原因带上——这正是这个框存在的理由，不然"失败了"三个字帮不上任何忙。
    // A failure carries its reason: that is why this box exists, since "it failed" alone
    // helps with nothing.
    const reason = ok ? "" : ` — ${entry.error || (entry.result && entry.result.validationReason) || "see the result panel"}`;
    row.textContent = `[${String(index + 1).padStart(2, " ")}/${items.length}] ${ok ? "OK  " : "FAIL"} ${shortMciId(entry.mciId)}${reason}`;
    log.append(row);
  });
  log.scrollTop = log.scrollHeight;
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForScan(jobId) {
  while (true) {
    const job = await request("/api/detection/scan-status?jobId=" + encodeURIComponent(jobId));
    updateScanProgress(job.completed || 0, job.total || 0, job.currentFile || "");
    if (job.state === "COMPLETED") return job.result;
    if (job.state === "FAILED") throw new Error(job.error || t("err_request_failed"));
    await delay(400);
  }
}

const REFACTOR_JOB_KEY = "clonedemocker_refactor_job";

// 一次轮询失败不代表作业出事。作业跑在服务端线程里，网络抖一下、或者服务端正忙着
// 编译，前端这边的 fetch 就可能失败一次——而原来的写法会把它当成作业失败直接抛出，
// 外层 finally 关掉进度窗，后台却还在跑，界面从此再也看不到它。
// One failed poll does not mean the job died. The job runs in a server-side thread, and a
// network blip — or the server being busy compiling — can fail a single fetch. The previous
// code treated that as job failure and threw, the outer finally closed the progress overlay,
// and the job kept running with nothing left watching it.
const POLL_FAILURES_BEFORE_GIVING_UP = 6;

async function waitForRefactoring(jobId) {
  let consecutiveFailures = 0;
  while (true) {
    let job;
    try {
      job = await request(`/api/refactoring/status?jobId=${encodeURIComponent(jobId)}&summary=1`);
      consecutiveFailures = 0;
    } catch (error) {
      consecutiveFailures += 1;
      if (consecutiveFailures >= POLL_FAILURES_BEFORE_GIVING_UP) {
        throw new Error(`${t("err_lost_job")} (${error.message})`);
      }
      $("#progress-detail").textContent = t("busy_reconnecting")
        .replace("{n}", consecutiveFailures).replace("{max}", POLL_FAILURES_BEFORE_GIVING_UP);
      await delay(1500);
      continue;
    }
    updateRefactoringProgress(job);
    if (job.state === "COMPLETED") {
      localStorage.removeItem(REFACTOR_JOB_KEY);
      return job;
    }
    if (job.state === "FAILED") {
      localStorage.removeItem(REFACTOR_JOB_KEY);
      throw new Error(job.error || "Refactoring job failed");
    }
    await delay(800);
  }
}

// 刷新页面不该丢掉正在跑的作业。作业 ID 只活在前端内存里的话，一次误刷新就让一批
// 几小时的重构彻底失联——后台照跑，但没有任何办法再看到它的进度或结果。
// A page reload should not lose a running job. With the job id living only in front-end
// memory, one accidental refresh orphans a batch that may run for hours: it keeps going
// server-side with no way left to see its progress or collect its results.
async function reattachRefactoringJob() {
  let ticket = null;
  const jobId = localStorage.getItem(REFACTOR_JOB_KEY);
  if (!jobId) return;
  try {
    const probe = await request(`/api/refactoring/status?jobId=${encodeURIComponent(jobId)}&summary=1`);
    if (probe.state !== "RUNNING") {
      localStorage.removeItem(REFACTOR_JOB_KEY);
      return;
    }
    showStep(4);
    status(t("status_refactoring"), "var(--status-active)");
    state.lastRefactorJobId = jobId;
    ticket = claimProgress(t("busy_refactor_title"), t("busy_reattached"));
    await collectRefactoringResults(jobId);
  } catch (error) {
    localStorage.removeItem(REFACTOR_JOB_KEY);
  } finally {
    if (ticket !== null) releaseProgress(ticket);
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
  if (number >= 2 && !state.runId) {
    toast("Run discovery before opening this workspace.");
    return;
  }
  if (number >= 3 && !state.instances.length) {
    toast("Detect clone candidates before opening this workspace.");
    return;
  }
  ["scope", "objects", "clones", "agent"].forEach((name, index) => {
    const panel = $("#" + name + "-panel");
    if (panel) panel.classList.toggle("hidden", index + 1 !== number);
  });
  $$(".step").forEach((step, index) => step.classList.toggle("active", index + 1 === number));
}

function treeNode(node) {
  const li = document.createElement("li");
  li.className = node.type;

  const row = document.createElement("div");
  row.className = "node-row";

  if (node.children) {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "tree-toggle";
    toggle.textContent = "▼";
    toggle.setAttribute("aria-label", "Collapse/Expand");
    toggle.addEventListener("click", () => {
      const collapsed = li.classList.toggle("collapsed");
      toggle.classList.toggle("collapsed", collapsed);
    });
    row.append(toggle);
  } else {
    const spacer = document.createElement("span");
    spacer.className = "tree-toggle-spacer";
    row.append(spacer);
  }

  const label = document.createElement("label");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.className = "scope-item";
  input.value = node.path;
  const text = document.createElement("span");
  text.textContent = (node.type === "directory" ? "📁 " : "📄 ") + node.name;
  label.append(input, text);
  row.append(label);
  li.append(row);

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

function setTreeCollapsed(collapsed) {
  $$("#source-tree li.directory").forEach((li) => {
    li.classList.toggle("collapsed", collapsed);
    const toggle = li.querySelector(":scope > .node-row > .tree-toggle");
    if (toggle) toggle.classList.toggle("collapsed", collapsed);
  });
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

// ================= Preflight =================
// 在用户选定项目目录的那一刻就判定环境，而不是等到流水线最后一步的 Harness 才失败。
// 扫描、选 MCI、调模型都不需要项目能构建，所以这里失败的代价接近零；等到 Harness
// 才发现的话，用户已经花掉了时间和 token。
// Decide the environment the moment a project root is chosen, instead of failing at the
// harness — the last step of the pipeline. Scanning, MCI selection and the model call need
// nothing built, so failing here costs almost nothing; discovering it at the harness means
// the user has already spent both time and tokens.
let preflightToken = 0;

function renderPreflight(result) {
  const box = $("#preflight-box");
  if (!box) return;
  const findingsBox = $("#preflight-findings");
  const actionsBox = $("#preflight-actions");
  const probeBox = $("#preflight-probe");
  findingsBox.replaceChildren();
  actionsBox.replaceChildren();
  probeBox.classList.add("hidden");

  if (!result || result.severity === "OK") {
    box.classList.add("hidden");
    return;
  }

  const blocking = result.severity === "BLOCKER";
  box.classList.remove("hidden");
  box.classList.toggle("blocker", blocking);
  $("#preflight-icon").textContent = blocking ? "⛔" : "⚠️";
  $("#preflight-title").textContent = blocking ? t("preflight_blocker_title") : t("preflight_warn_title");

  for (const finding of result.findings || []) {
    const line = document.createElement("div");
    line.className = "preflight-finding";
    line.textContent = finding.message;
    findingsBox.append(line);
  }

  // 有 reactor 根可推荐时给一个直接改正的按钮——这类问题换个路径就好了，
  // 不该让用户自己去读 Maven 报错才反应过来。
  // When a reactor root can be suggested, offer a one-click correction: this class of
  // problem is fixed by pointing somewhere else, and the user shouldn't have to read a
  // Maven stack trace to work that out.
  const suggested = (result.findings || []).map((item) => item.suggestedRoot).find(Boolean);
  if (suggested) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = `${t("btn_use_reactor_root")} — ${suggested}`;
    button.onclick = () => {
      $("#project-root").value = suggested;
      $("#project-root").dispatchEvent(new Event("input", { bubbles: true }));
      runPreflight(suggested, true);
    };
    actionsBox.append(button);
  }

  if (result.sagRecommended) {
    const hint = document.createElement("div");
    hint.className = "preflight-finding";
    hint.textContent = t("preflight_sag_hint");
    findingsBox.append(hint);
  }

  const probe = result.buildProbe;
  if (probe && probe.output && probe.status !== "SKIPPED") {
    probeBox.classList.remove("hidden");
    $("#preflight-probe-output").textContent = probe.output;
  }
}

async function runPreflight(path, withBuildProbe) {
  const box = $("#preflight-box");
  if (!box) return null;
  if (!path) {
    box.classList.add("hidden");
    return null;
  }
  // 输入框每敲一个字符都会触发，只认最后一次的结果，避免旧响应盖掉新响应。
  // Fires on every keystroke, so only the latest response is honoured — an older
  // in-flight response must not overwrite a newer one.
  const token = ++preflightToken;
  try {
    const query = `/api/preflight?projectRoot=${encodeURIComponent(path)}`
      + `&buildProbe=${withBuildProbe ? "1" : "0"}`;
    const result = await request(query);
    if (token !== preflightToken) return null;
    renderPreflight(result);
    return result;
  } catch (error) {
    if (token !== preflightToken) return null;
    box.classList.add("hidden");
    return null;
  }
}

let preflightDebounce = null;

$("#project-root").addEventListener("input", (e) => {
  const path = e.target.value.trim();
  const card = $("#readiness-card");
  if (path) { $(".project-row").after(card); card.classList.remove("hidden"); }
  checkPathSafety(path);
  // 边打字边跑静态检查（毫秒级、不起进程），构建探针留给"加载文件树"那一步。
  // The static tier runs while typing (milliseconds, spawns no process); the build probe
  // is left to the "Load File Tree" step.
  clearTimeout(preflightDebounce);
  preflightDebounce = setTimeout(() => runPreflight(path, false), 400);
  $("#baseline-status").textContent = path
    ? "Ready. Each refactoring verifies its own baseline, scoped to the modules that MCI touches."
    : "Choose a project root to continue.";
});

// ================= Step 1: Scope Events =================
$("#load-tree").addEventListener("click", async () => {
  try {
    const root = $("#project-root").value.trim();
    checkPathSafety(root);
    status(t("status_loading_tree"), "var(--status-active)");
    // 构建探针跟加载文件树并发跑，而且探针结果不阻断后续步骤：检测、选 MCI、调模型
    // 都不需要项目能构建，只有 Harness 验证需要。这里只是尽早把结论摆出来。
    // The build probe runs alongside loading the tree, and its verdict does not block
    // anything: detection, MCI selection and the model call need nothing built — only
    // harness validation does. This merely surfaces the verdict as early as possible.
    runPreflight(root, true);
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
$("#expand-all-tree").onclick = () => setTreeCollapsed(false);
$("#collapse-all-tree").onclick = () => setTreeCollapsed(true);

$("#browse-root").addEventListener("click", async () => {
  try {
    const result = await request("/api/pick-directory");
    if (result.path) {
      $("#project-root").value = result.path;
      $("#project-root").dispatchEvent(new Event("input", { bubbles: true }));
    }
  } catch (error) {
    toast(error.message);
  }
});

// data/<project>/ 里已有检测结果时，先问一句要不要直接用。检测结果对同一份源码是确定的，
// 每次都重扫一遍大项目只是在等。选"重新检测"就照常扫描。
// If data/<project>/ already holds a detection, ask whether to use it first. Detection is
// deterministic for the same sources, so rescanning a large project each time is just waiting.
// "Detect again" scans as usual.
// 返回 "use" / "fresh" / "cancel"。 / Returns "use", "fresh" or "cancel".
async function offerSavedDetection(projectRoot) {
  let saved;
  try {
    saved = await request("/api/detection/cached?projectRoot=" + encodeURIComponent(projectRoot));
  } catch (error) {
    return "fresh";
  }
  if (!saved.available) return "fresh";

  const dialog = $("#cache-prompt");
  $("#cache-prompt-title").textContent = t("saved_detection_title");
  let body = t("saved_detection_body")
    .replace("{project}", saved.project)
    .replace("{mcis}", saved.mciCount)
    .replace("{mos}", saved.mockObjectCount)
    .replace("{when}", new Date(saved.detectedAt).toLocaleString());
  if (!saved.projectRootMatches) body += t("saved_detection_moved").replace("{recorded}", saved.recordedProjectRoot);
  $("#cache-prompt-body").textContent = body;
  $("#cache-prompt-body").style.whiteSpace = "pre-line";
  $("#cache-prompt-reuse").textContent = t("saved_detection_use");
  $("#cache-prompt-fresh").textContent = t("saved_detection_fresh");
  $("#cache-prompt-cancel").textContent = t("cache_prompt_cancel");
  return new Promise((resolve) => {
    const finish = (value) => { dialog.close(); resolve(value); };
    $("#cache-prompt-reuse").onclick = () => finish("use");
    $("#cache-prompt-fresh").onclick = () => finish("fresh");
    $("#cache-prompt-cancel").onclick = () => finish("cancel");
    dialog.oncancel = () => resolve("cancel");
    dialog.showModal();
  });
}

async function loadSavedDetection(projectRoot) {
  const result = await request("/api/detection/load-cached", {
    method: "POST",
    body: JSON.stringify({ projectRoot }),
  });
  state.runId = result.runId;
  // 这种 run 没有扫描出来的 mock 对象列表，第 2 步是空的，直接到第 3 步。
  // Such a run has no scanned mock object list, so step 2 is empty; go straight to step 3.
  state.mocks = [];
  renderMocks();
  state.instances = (result.mockCloneInstances || []).map((item) => ({ ...item, selected: false }));
  renderInstances();
  showStep(3);
  status(t("status_detection_restored", state.instances.length), "var(--status-ready)");
}

$("#scan-mocks").addEventListener("click", async () => {
  try {
    const projectRoot = $("#project-root").value.trim();
    const choice = await offerSavedDetection(projectRoot);
    if (choice === "cancel") return;
    if (choice === "use") {
      await loadSavedDetection(projectRoot);
      return;
    }
    const selected = $$(".scope-item:checked").map((item) => item.value);
    if (!selected.length) throw new Error(t("err_select_one_file"));
    const includePaths = selected.filter(
      (path) => !selected.some((parent) => parent !== path && (parent === "." || path.startsWith(parent + "/")))
    );
    status(t("status_scanning"), "var(--status-active)");
    setProgress(true, t("busy_scan_title"), t("busy_scan_detail"));
    const started = await request("/api/detection/scan", {
      method: "POST",
      body: JSON.stringify({
        projectRoot: $("#project-root").value.trim(),
        includePaths: includePaths,
        excludePaths: $("#excludes").value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean),
        packagePrefixes: $("#packages").value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean),
        resolveDependencies: $("#resolve-dependencies").checked,
      }),
    });
    const result = await waitForScan(started.jobId);
    state.runId = result.runId;
    state.mocks = (result.mockObjects || []).map((item) => ({ ...item, selected: true }));
    renderMocks();
    renderScanDiagnostics(result.diagnostics || "", state.mocks.length === 0);
    showStep(2);
    status(t("status_scanned", state.mocks.length), "var(--status-ready)");
  } catch (error) {
    status(t("status_error"), "var(--status-error)");
    toast(error.message);
  } finally {
    setProgress(false);
  }
});

function renderScanDiagnostics(diagnostics, zeroResults) {
  const warningBox = $("#scan-zero-warning");
  const detailsBox = $("#scan-diagnostics");
  const content = $("#scan-diagnostics-content");
  warningBox.classList.toggle("hidden", !zeroResults);
  const trimmed = diagnostics.trim();
  content.textContent = trimmed || t("scan_log_empty");
  detailsBox.classList.toggle("hidden", !trimmed && !zeroResults);
  detailsBox.open = zeroResults;
}

// ================= Step 2: Objects Events =================
function renderMocks() {
  const query = ($("#mock-search").value || "").toLowerCase();
  const role = $("#mock-role-filter").value;
  const location = $("#mock-location-filter").value;
  state.visibleMocks = state.mocks.filter((item) => {
    const haystack = [item.variableName, item.mockedClass, item.filePath, (item.testMethods || []).join(" ")]
      .join(" ")
      .toLowerCase();
    const roleMatches = role === "all" || String(item.mockRole || "mock").toLowerCase() === role;
    const inTest = (item.testMethods || []).length > 0;
    const locationMatches = location === "all" || (location === "test" && inTest) || (location === "other" && !inTest);
    return haystack.includes(query) && roleMatches && locationMatches;
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
    typeTd.innerHTML = `<strong>${escapeHtml(item.mockedClass || "-")}</strong>`;

    const fileTd = document.createElement("td");
    fileTd.className = "subtle";
    const preview = document.createElement("button");
    preview.className = "text-button";
    preview.type = "button";
    preview.textContent = item.filePath || "Preview source";
    preview.onclick = () => showMockPreview(item, tr);
    fileTd.append(preview);

    const testTd = document.createElement("td");
    testTd.textContent = (item.testMethods || []).length;

    const stmtTd = document.createElement("td");
    stmtTd.textContent = item.statementCount || 0;

    tr.append(checkTd, varTd, typeTd, fileTd, testTd, stmtTd);
    tbody.append(tr);
  });
  updateObjectCount();
}

function highlightJava(code, terms) {
  let escaped = escapeHtml(code || "");
  terms.filter(Boolean).forEach((term) => {
    const escapedTerm = escapeHtml(term).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    escaped = escaped.replace(new RegExp(`\\b${escapedTerm}\\b`, "g"), '<mark class="code-target">$&</mark>');
  });
  return escaped.replace(/\b(when|given|verify|mock|spy|thenReturn)\b/g, '<span class="code-keyword">$1</span>');
}

async function showMockPreview(item, row) {
  const next = row.nextElementSibling;
  if (next && next.classList.contains("source-preview-row")) { next.remove(); return; }
  const data = await request(`/api/detection/mock-preview?runId=${encodeURIComponent(state.runId)}&id=${item.id}`);
  const detailRow = document.createElement("tr");
  detailRow.className = "source-preview-row";
  const cell = document.createElement("td");
  cell.colSpan = 6;
  cell.innerHTML = `<strong>Original source — ${escapeHtml(data.variableName)} : ${escapeHtml(data.mockedClass)}</strong>`
    + data.snippets.map((snippet) => `<details><summary>${escapeHtml(snippet.methodName || "source")} · line ${snippet.line || "?"}</summary>`
      + `<pre class="java-preview">${highlightJava(snippet.code, [data.variableName])}</pre></details>`).join("");
  detailRow.append(cell);
  row.after(detailRow);
}

function updateObjectCount() {
  const selectedCount = state.mocks.filter((x) => x.selected).length;
  $("#object-count").textContent = `${selectedCount} / ${state.mocks.length}`;
}

$("#mock-search").addEventListener("input", renderMocks);
$("#mock-role-filter").addEventListener("change", renderMocks);
$("#mock-location-filter").addEventListener("change", renderMocks);

$("#select-all-mocks").onclick = () => {
  state.mocks.forEach((item) => (item.selected = true));
  renderMocks();
};

$("#clear-all-mocks").onclick = () => {
  state.mocks.forEach((item) => (item.selected = false));
  renderMocks();
};

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
    setProgress(true, t("busy_detect_title"), t("busy_detect_detail"));
    const result = await request("/api/detection/detect", {
      method: "POST",
      body: JSON.stringify({
        runId: state.runId,
        selectedMockIds: selectedMockIds,
      }),
    });
    // Batch mode is the primary workflow. The server queues one independently reviewable,
    // isolated and full-regression-gated proposal per selected MCI.
    // 默认不选。每多跑一个 MCI 都是一次真实的模型调用加一次 Maven 构建，所以"跑什么"
    // 必须是一次明确的动作，而不是一个需要你记得去取消的默认值。
    // Nothing is selected by default. Every extra MCI is a real model call plus a Maven build,
    // so what runs has to be an explicit act rather than a default someone must remember to undo.
    state.instances = (result.mockCloneInstances || []).map((item) => ({ ...item, selected: false }));
    renderInstances();
    showStep(3);
    status(t("status_detected", state.instances.length), "var(--status-ready)");
    if (result.savedToData) toast(t("detection_saved_to_data"), false);
  } catch (error) {
    status(t("status_error"), "var(--status-error)");
    toast(error.message);
  } finally {
    setProgress(false);
  }
});

// ================= Step 3: MCI Events =================
// 运行列表就是"接下来会跑什么"，没有第二份真相。清单本身可见、可数、可逐条移除，
// 所以不再需要靠一个数字去暗示有多少条目被筛选藏在了别处。
// The run list is what will run, with no second source of truth. It is visible, countable and
// removable item by item, so no number has to hint at how many entries a filter is hiding.
function renderRunList(selected, batchMode) {
  const container = $("#mci-run-list");
  const note = $("#mci-selection-note");
  container.dataset.empty = "Nothing selected yet — add candidates from the pool above.";
  note.textContent = selected.length
    ? (batchMode
      ? `${selected.length} MCI(s) will run`
      : `Review mode: ${selected[0].id}`)
    : "Nothing selected yet";
  container.replaceChildren(...selected.map((instance) => {
    const row = document.createElement("div");
    row.className = "run-item";

    const label = document.createElement("span");
    label.className = "run-item-id";
    label.textContent = instance.id;
    label.title = instance.id;

    const meta = document.createElement("span");
    meta.className = "run-item-meta";
    const sequences = (instance.sequences || []).length;
    const stubs = (instance.sharedStatements || []).length;
    meta.textContent = `${sequences} seq · ${stubs} stub`;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "run-item-remove";
    remove.textContent = "×";
    remove.title = "Remove from the run list";
    remove.setAttribute("aria-label", `Remove ${instance.id}`);
    remove.onclick = () => {
      instance.selected = false;
      renderInstances();
    };

    const right = document.createElement("span");
    right.style.display = "flex";
    right.style.alignItems = "center";
    right.style.gap = "8px";
    right.append(meta, remove);
    row.append(label, right);
    return row;
  }));
  // 条目少的时候折叠没有意义，反而让人多点一下才能确认自己选了什么。
  // Collapsing a short list serves nothing and makes confirming the selection an extra click.
  const toggle = $("#mci-run-list-toggle");
  if (selected.length <= 3) {
    container.classList.remove("collapsed");
    toggle.setAttribute("aria-expanded", "true");
  }
}

function renderInstances() {
  const list = $("#mci-list");
  list.replaceChildren();
  const query = ($("#mci-search").value || "").toLowerCase();
  const minSequences = Number($("#mci-min-sequences").value || 0);
  const minStubs = Number($("#mci-min-stubs").value || 0);
  // 候选池只放"还没被选中"的。筛选作用在这里，选中的条目已经移到下面的运行列表，
  // 不受筛选影响——这正是之前把 3 个的意图变成 99 个的原因：筛选只是隐藏，被隐藏的
  // 条目仍然选中并被提交。
  // The pool holds only what has not been picked. Filtering acts here; picked entries have
  // moved to the run list below and are untouched by it — which is exactly what turned an
  // intent of 3 into a run of 99, when filtering merely hid entries that stayed selected.
  state.visibleInstances = state.instances.filter((instance) => {
    if (instance.selected) return false;
    const sequences = instance.sequences || [];
    const stubs = instance.sharedStatements || [];
    const text = [instance.id, ...stubs, ...sequences.map((s) => `${s.filePath} ${s.testMethodName}`)].join(" ").toLowerCase();
    return sequences.length >= minSequences && stubs.length >= minStubs && text.includes(query);
  });
  const selected = state.instances.filter((item) => item.selected);
  $("#mci-count").textContent = `${state.visibleInstances.length} / ${state.instances.length}`;
  const batchMode = $("#mci-execution-mode").value === "batch";
  const selectedCount = selected.length;
  $("#mci-pool-note").textContent = state.visibleInstances.length
    ? `${state.visibleInstances.length} candidate(s) match the filter. Adding one moves it to the run list below.`
    : (selectedCount ? "Every candidate matching this filter is already in the run list."
                     : "No candidate matches this filter.");
  renderRunList(selected, batchMode);
  $("#prepare-refactor").disabled = !selectedCount || (!batchMode && selectedCount !== 1);
  $("#prepare-refactor").textContent = batchMode
    ? `Prepare ${selectedCount || "selected"} candidate${selectedCount === 1 ? "" : "s"} →`
    : "Prepare selected candidate →";

  state.visibleInstances.forEach((instance) => {
    const card = document.createElement("div");
    card.className = `mci${instance.selected ? " selected" : ""}`;
    const checkbox = document.createElement("input");
    checkbox.type = batchMode ? "checkbox" : "radio";
    checkbox.name = batchMode ? "" : "selected-mci";
    checkbox.checked = instance.selected;
    checkbox.setAttribute("aria-label", `Select ${instance.id}`);
    checkbox.addEventListener("change", () => {
      if (batchMode) {
        instance.selected = checkbox.checked;
      } else {
        state.instances.forEach((item) => (item.selected = item === instance));
      }
      renderInstances();
    });

    const content = document.createElement("div");
    const h3 = document.createElement("h3");
    h3.textContent = instance.id;

    const files = [...new Set((instance.sequences || []).map((seq) => seq.filePath).filter(Boolean))];
    const filesDesc = document.createElement("p");
    filesDesc.textContent = `${t("mci_files")}: ${files.join(", ") || "-"}`;

    const chips = document.createElement("div");
    chips.className = "chips";
    (instance.sharedStatements || []).forEach((sig) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = sig;
      chips.append(chip);
    });

    content.append(h3, filesDesc, chips);
    const inspect = document.createElement("button");
    inspect.className = "text-button";
    inspect.type = "button";
    inspect.textContent = "View duplicate source evidence";
    inspect.onclick = () => showMciPreview(instance, content);
    content.append(inspect);

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
        seqLabel.textContent = `${seq.testMethodName || "seq"} (ID: ${seq.mockObjectId})`;
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

async function showMciPreview(instance, container) {
  const existing = container.querySelector(".mci-source-evidence");
  if (existing) { existing.remove(); return; }
  const data = await request(`/api/detection/mci-preview?runId=${encodeURIComponent(state.runId)}&mciId=${encodeURIComponent(instance.id)}`);
  const evidence = document.createElement("div");
  evidence.className = "mci-source-evidence";
  evidence.innerHTML = `<strong>Duplicated mock setup (${data.occurrences.length} occurrences)</strong>`
    + `<div class="chips">${(data.sharedStatements || []).map((stub) => `<span class="chip">${escapeHtml(stub)}</span>`).join("")}</div>`
    + data.occurrences.map((occurrence) => `<details><summary>${escapeHtml(occurrence.filePath || "")} · ${escapeHtml(occurrence.methodName || "")}</summary>`
      + `<pre class="java-preview">${highlightJava(occurrence.code, [occurrence.variableName, ...(occurrence.sharedLines || [])])}</pre></details>`).join("");
  container.append(evidence);
}

$("#mci-search").addEventListener("input", renderInstances);
$("#mci-min-sequences").addEventListener("input", renderInstances);
$("#mci-min-stubs").addEventListener("input", renderInstances);
$("#mci-execution-mode").addEventListener("change", () => {
  if ($("#mci-execution-mode").value === "single") {
    const first = state.instances.find((item) => item.selected);
    state.instances.forEach((item) => (item.selected = item === first));
  }
  renderInstances();
});
// 把当前筛选出来的全部加进运行列表。候选池里只有未选中的，所以可以换一组筛选条件
// 再加一次，运行列表会累加——这样"多轮筛选拼出一批"是自然的做法。
// Adds everything the current filter matched. The pool holds only unpicked candidates, so
// another filter can be applied and added again, accumulating in the run list — which makes
// assembling a batch through several filter passes the natural way to work.
$("#select-visible-mcis").onclick = () => {
  const visible = state.visibleInstances || [];
  if ($("#mci-execution-mode").value === "single") {
    const first = visible[0];
    state.instances.forEach((item) => (item.selected = item === first));
  } else {
    visible.forEach((item) => (item.selected = true));
  }
  renderInstances();
};

$("#mci-run-list-toggle").onclick = () => {
  const items = $("#mci-run-list");
  const expanded = items.classList.toggle("collapsed") === false;
  $("#mci-run-list-toggle").setAttribute("aria-expanded", String(expanded));
};
$("#clear-visible-mcis").onclick = () => { state.instances.forEach((item) => (item.selected = false)); renderInstances(); };

$("#back-objects").onclick = () => showStep(2);
function selectedRefactoringPayload() {
  const selectedMciIds = state.instances.filter((item) => item.selected).map((item) => item.id);
  const sequenceSelection = {};
  state.instances.forEach((instance) => {
    if (instance.sequences) {
      sequenceSelection[instance.id] = instance.sequences
        .filter((sequence) => sequence.selected !== false)
        .map((sequence) => sequence.mockObjectId);
    }
  });
  return {
    runId: state.runId, selectedMciIds, sequenceSelection,
    model: $("#model").value.trim(), apiProfile: $("#api-profile").value.trim(),
    instruction: $("#agent-instruction").value.trim(), runPit: $("#run-pit").checked,
    useMock: $("#use-mock").checked, maxRetries: Number($("#max-retries").value || 0),
  };
}

async function refreshCacheStatus() {
  const payload = selectedRefactoringPayload();
  const label = $("#cache-status");
  const choice = $("#cache-choice");
  if (!payload.selectedMciIds.length) return;
  label.textContent = "Checking exact MCI and source fingerprint…";
  choice.classList.add("hidden");
  try {
    const result = await request("/api/refactoring/cache-status", {
      method: "POST", body: JSON.stringify(payload),
    });
    if (result.available && result.validated) {
      label.textContent = `Verified cache found for ${result.availableCount || 1} of ${result.total || 1} selected MCI(s) · ${(result.changedFiles || []).length} changed file(s)`;
      choice.classList.remove("hidden");
    } else {
      label.textContent = "No exact verified cache match; a new proposal will be generated.";
    }
  } catch (error) {
    label.textContent = `Cache check unavailable: ${error.message}`;
  }
}

let cacheRefreshTimer = null;
function scheduleCacheRefresh() {
  if ($("#agent-panel").classList.contains("hidden")) return;
  clearTimeout(cacheRefreshTimer);
  cacheRefreshTimer = setTimeout(refreshCacheStatus, 350);
}
$("#agent-instruction").addEventListener("input", scheduleCacheRefresh);
$("#run-pit").addEventListener("change", scheduleCacheRefresh);

$("#prepare-refactor").onclick = async () => {
  const selected = state.instances.filter((item) => item.selected);
  if (!selected.length || ($("#mci-execution-mode").value === "single" && selected.length !== 1)) return;
  showStep(4);
  await refreshCacheStatus();
};

// ================= Step 4: Agent & IDE Studio Events =================
$("#back-clones").onclick = () => showStep(3);

$("#run-agent").addEventListener("click", async () => {
  // 一次只允许跑一批。少了这道闸，第二次点击会在第一批还在跑的时候再起一个作业：两个
  // 批次抢同一份共享工作区，缓存确认框叠在运行中的进度窗上，而 localStorage 里的作业号
  // 被后者覆盖——先跑的那批就此失联。
  // One batch at a time. Without this gate a second click starts another job while the first
  // is still running: two batches contend for the same shared workspace, the cache dialog
  // stacks on top of a live progress overlay, and the job id in localStorage is overwritten
  // by the later one, orphaning the first.
  if (state.refactoringInFlight) {
    toast(t("err_already_running"));
    return;
  }
  let ticket = null;
  state.refactoringInFlight = true;
  try {
    const payload = selectedRefactoringPayload();
    const selectedMciIds = payload.selectedMciIds;
    if (!selectedMciIds.length) throw new Error("Select at least one MCI.");
    if ($("#mci-execution-mode").value === "single" && selectedMciIds.length !== 1) {
      throw new Error("Review mode requires exactly one MCI.");
    }
    payload.useCache = $("input[name='cache-policy']:checked")?.value !== "fresh";
    // 缓存命中一分钱 token 都不花，所以界面上没有任何迹象告诉你这份方案是上一次生成的。
    // 正式跑实验之前把这件事明确问一次，比事后发现数据里混着旧答案要便宜得多。
    // A cache hit spends no tokens, so nothing on screen reveals that a proposal came from an
    // earlier run. Asking once before a real experiment costs far less than discovering stale
    // answers mixed into the data afterwards.
    if (payload.useCache && !(await confirmCacheReuse(payload))) return;
    status(t("status_refactoring"), "var(--status-active)");
    ticket = claimProgress(t("busy_refactor_title"), t("busy_refactor_detail"));
    const started = await request("/api/refactoring/run", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    localStorage.setItem(REFACTOR_JOB_KEY, started.jobId);
    state.lastRefactorJobId = started.jobId;
    await collectRefactoringResults(started.jobId);
  } catch (error) {
    status(t("status_error"), "var(--status-error)");
    toast(error.message);
  } finally {
    state.refactoringInFlight = false;
    // 没认领过就什么都不关：取消缓存弹窗时本来就没显示进度窗，这时去关只会误伤别人的。
    // Never claimed, never close: cancelling the cache dialog showed no overlay, and closing
    // here would only take down someone else's.
    if (ticket !== null) releaseProgress(ticket);
  }
});

async function collectRefactoringResults(jobId) {
  await waitForRefactoring(jobId);
  // 轮询走的是精简模式（不带每项的完整 result），所以结果在这里单取一次完整的。
  // Polling runs in summary mode without each item's full result, so the complete one is
  // fetched here, once.
  const job = await request(`/api/refactoring/status?jobId=${encodeURIComponent(jobId)}`);
  state.agentResults = (job.items || []).map((item) => ({ mciId: item.mciId, result: item.result, error: item.error }))
    .filter((entry) => entry.result || entry.error);
  state.activeAgentResult = 0;
  renderProposalQueue();
  if (job.exportError) {
    const message = t("auto_save_failed").replace("{error}", job.exportError);
    status(message, "var(--status-error)");
    toast(message);
  } else {
    status(t("status_refactor_done"), "var(--status-ready)");
    const saved = job.export || {};
    if (saved.project && (saved.setupDirectory || saved.setup)) {
      toast(t("auto_save_done")
        .replace("{project}", saved.project)
        .replace("{setup}", saved.setupDirectory || saved.setup), false);
    }
  }
}

// 返回 true 表示继续跑。选择"清除缓存重新生成"时会真的把条目删掉，而不只是这一次
// 绕过——绕过的条目下次还在，还会再命中。
// Returns true to proceed. Choosing "clear and regenerate" really deletes the entries rather
// than bypassing them for one run: a bypassed entry is still there and hits again next time.
async function confirmCacheReuse(payload) {
  let status;
  // 这一步要为每个 MCI 算源码哈希，MCI 多时并非瞬时。没有提示的话，点下按钮之后就是
  // 一段沉默——实测 99 个 MCI 曾空等 7 秒，看起来像点了没反应。
  // This hashes the sources behind every MCI and is not instant when there are many. Without
  // an indicator the click is followed by silence — 99 MCIs once sat idle for seven seconds,
  // which reads as a dead button.
  const ticket = claimProgress(t("busy_cache_title"), t("busy_cache_detail"), false);
  try {
    status = await request("/api/refactoring/cache-status", {
      method: "POST", body: JSON.stringify(payload),
    });
  } catch (error) {
    return true;
  } finally {
    releaseProgress(ticket);
  }
  if (!status.available || !status.validated) return true;

  const dialog = $("#cache-prompt");
  $("#cache-prompt-title").textContent = t("cache_prompt_title");
  $("#cache-prompt-body").textContent = t("cache_prompt_body")
    .replace("{n}", status.availableCount || 1)
    .replace("{total}", status.total || 1);
  $("#cache-prompt-reuse").textContent = t("cache_prompt_reuse");
  $("#cache-prompt-fresh").textContent = t("cache_prompt_fresh");
  $("#cache-prompt-cancel").textContent = t("cache_prompt_cancel");

  const answer = await new Promise((resolve) => {
    const finish = (value) => { dialog.close(); resolve(value); };
    $("#cache-prompt-reuse").onclick = () => finish("reuse");
    $("#cache-prompt-fresh").onclick = () => finish("fresh");
    $("#cache-prompt-cancel").onclick = () => finish("cancel");
    dialog.oncancel = () => resolve("cancel");
    dialog.showModal();
  });

  if (answer === "cancel") return false;
  if (answer === "fresh") {
    const cleared = await request("/api/refactoring/cache-clear", {
      method: "POST", body: JSON.stringify(payload),
    });
    payload.useCache = false;
    toast(t("cache_cleared").replace("{n}", cleared.cleared || 0), false);
  }
  return true;
}

function renderProposalQueue() {
  const queue = $("#proposal-queue");
  queue.replaceChildren();
  queue.classList.toggle("hidden", state.agentResults.length <= 1);
  state.agentResults.forEach((entry, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `proposal-queue-item${index === state.activeAgentResult ? " active" : ""}`;
    const verified = Boolean(entry.result && entry.result.harness && entry.result.harness.equivalent);
    button.textContent = `${index + 1}. ${entry.mciId} · ${verified ? "verified" : "needs attention"}`;
    button.onclick = () => {
      state.activeAgentResult = index;
      renderProposalQueue();
    };
    queue.append(button);
  });
  const active = state.agentResults[state.activeAgentResult];
  if (!active) return;
  const payload = active.result || {
    stage: "FAILED", reason: active.error || "Refactoring failed", validationReason: active.error || "Refactoring failed",
    diff: "", changedFiles: [], harness: null, usage: {}, modelCalls: 0,
  };
  state.lastAgentPayload = payload;
  renderAgentResult(payload);
}

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
  const harness = payload.harness || {};
  const baseline = harness.baseline || {};
  const evidence = harness.candidate || payload.evidence || {};
  const verified = harness.equivalent === true;
  const compile = evidence.compileStatus || evidence.compile_status || "NOT_RUN";
  const tests = evidence.testStatus || evidence.test_status || "NOT_RUN";
  const pit = evidence.pitStatus || evidence.pit_status || "NOT_RUN";
  const beforeScore = baseline.mutationScore;
  const afterScore = evidence.mutationScore;
  const scoreText = beforeScore == null || afterScore == null
    ? pit
    : `${(beforeScore * 100).toFixed(1)}% → ${(afterScore * 100).toFixed(1)}%`;
  const usage = payload.usage || {};
  // 基线坏掉和模型没写好补丁是两件完全不同的事，界面必须分得开：前者要去修环境，
  // 后者才该去看 diff。之前只渲染 candidate，两种情况长得一模一样。
  // A broken baseline and a bad patch are entirely different situations, and the UI has to
  // tell them apart: the first means go fix the environment, only the second means go read
  // the diff. Previously only the candidate was rendered, so the two looked identical.
  const baselineBroken = harness.baselineBroken === true;
  const baselineCompile = baseline.compileStatus || baseline.compile_status || "NOT_RUN";
  const baselineTests = baseline.testStatus || baseline.test_status || "NOT_RUN";
  const baselineText = baselineCompile !== "PASSED" ? `COMPILE ${baselineCompile}`
    : baselineTests !== "PASSED" ? `TESTS ${baselineTests}` : "PASSED";
  const baselineClass = baselineCompile === "PASSED" && baselineTests === "PASSED" ? "passed" : "failed";
  const modelCalls = payload.modelCalls != null ? payload.modelCalls : "-";
  const metricsBox = $("#result-metrics-box");
  metricsBox.innerHTML = `
    <div class="result-grid">
      <div>
        <small>${t("metric_baseline")}</small>
        <strong class="${baselineClass}">${baselineText}</strong>
      </div>
      <div>
        <small>${t("metric_scope")}</small>
        <strong>${baseline.scope || "the whole project"}</strong>
      </div>
      <div>
        <small>${t("metric_compile")}</small>
        <strong class="${compile.toLowerCase()}">
          ${compile}
        </strong>
      </div>
      <div>
        <small>${t("metric_tests")}</small>
        <strong class="${tests.toLowerCase()}">
          ${tests}
        </strong>
      </div>
      <div>
        <small>${t("metric_pit")}</small>
        <strong>${scoreText}</strong>
      </div>
      <div>
        <small>${t("metric_model_calls")}</small>
        <strong>${modelCalls}</strong>
      </div>
      <div>
        <small>${t("metric_tokens")}</small>
        <strong>${usage.total_tokens || usage.totalTokens || 0}</strong>
      </div>
    </div>
  `;

  const headline = verified ? t("status_verified")
    : baselineBroken ? t("status_baseline_broken")
    : t("status_review_required");
  const validation = document.createElement("div");
  validation.className = verified ? "notice" : "scan-warning";
  validation.innerHTML = `<strong>${headline}</strong>`
    + `<p>${escapeHtml(payload.validationReason || payload.reason || "-")}</p>`
    + `<p class="subtle">Tests: baseline ${Object.keys(baseline.testResults || {}).length} / candidate ${Object.keys(evidence.testResults || {}).length}; `
    + `PIT threshold: ${(harness.mutationScoreThreshold || 0.05) * 100} percentage points.</p>`;
  metricsBox.append(validation);

  // Diagnostics Box
  const diagBox = $("#result-diagnostics-box");
  diagBox.replaceChildren();
  // 基线就失败时候选侧根本没跑，诊断只存在于基线那一份——不回退的话这里会是一片空白，
  // 恰恰把最需要看的那段构建报错藏起来。
  // When the baseline itself failed the candidate side never ran, so the diagnostics only
  // exist on the baseline. Without this fallback the box would be empty — hiding precisely
  // the build error the user most needs to read.
  const diags = (evidence.diagnostics && evidence.diagnostics.length)
    ? evidence.diagnostics
    : (baseline.diagnostics || []);
  if (diags.length) {
    const diagEl = document.createElement("div");
    // 这份日志是成是败由 harness 的判定说了算，不能永远按失败来配色。
    // Whether this log reports success or failure is the harness's verdict to state; it must
    // not be coloured as a failure unconditionally.
    diagEl.className = `diagnostics${harness.equivalent ? " passed" : ""}`;
    diagEl.textContent = diags.join("\n");
    diagBox.append(diagEl);
  }
  if (payload.cache) {
    const cacheLine = document.createElement("div");
    cacheLine.className = "notice compact";
    cacheLine.textContent = payload.cache.hit
      ? "Exact cache match loaded and revalidated; no generation tokens were used."
      : "A fresh proposal was generated (no usable cache match, or cached validation failed).";
    diagBox.append(cacheLine);
  }
  const repairs = payload.repairHistory || [];
  if (repairs.length) {
    const details = document.createElement("details");
    details.className = "repair-history";
    const summary = document.createElement("summary");
    summary.textContent = `Inspect ${repairs.length} repair prompt(s) · ${payload.repairAttemptsRemaining || 0} retries remaining`;
    details.append(summary);
    repairs.forEach((repair) => {
      const heading = document.createElement("strong");
      heading.textContent = `Attempt ${repair.attempt} · ${repair.type}`;
      const prompt = document.createElement("pre");
      prompt.textContent = `${repair.prompt}\n\nINPUT\n${JSON.stringify(repair.input, null, 2)}\n\nRESPONSE\n${repair.response}`;
      details.append(heading, prompt);
    });
    diagBox.append(details);
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
  badge.textContent = headline;
  const acceptBtn = $("#btn-accept-proposal");
  const discardBtn = $("#btn-discard-proposal");
  // 没有任何改动文件时（基线失败、或者模型拒绝重构）没有补丁可应用，强制应用只会
  // 在读不到 manifest.json 时报一个莫名其妙的错。
  // With no changed files (a failed baseline, or the model declining to refactor) there is
  // no patch to apply, and a forced apply would only produce a confusing error when
  // manifest.json turns out not to exist.
  const hasPatch = (payload.changedFiles || []).length > 0;
  acceptBtn.disabled = !hasPatch;
  discardBtn.disabled = false;
  acceptBtn.textContent = `✓ ${t("btn_accept")}`;
  acceptBtn.disabled = !hasPatch || !verified;

  // Accept Handler
  acceptBtn.onclick = async () => {
    try {
      status(t("status_applying"), "var(--status-active)");
      await request("/api/refactoring/apply", {
        method: "POST",
        body: JSON.stringify({
          runId: state.runId,
          proposalId: payload.proposalId,
          force: false,
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

// 报告落盘。在此之前，界面跑出来的结果只活在服务进程的内存里——一次重启就全没了，
// 而一批 99 个 MCI 要跑几个小时。data/ 里按 mciId 合并，所以多次运行会拼成同一份数据集：
// 断在第 60 个，补跑剩下的，两次结果自动并到一起。
// Persisting the report. Until now UI results lived only in the server process memory, which a
// restart erased, and a 99-MCI batch runs for hours. data/ merges by mciId, so runs splice into
// one dataset: stop at the 60th, run the rest later, and the two join.
$("#btn-export-data").addEventListener("click", async () => {
  const jobId = state.lastRefactorJobId;
  if (!jobId) { toast(t("export_none")); return; }
  const ticket = claimProgress(t("btn_export_data"), "", false);
  try {
    const summary = await request("/api/refactoring/export", {
      method: "POST",
      body: JSON.stringify({ jobId, runId: state.runId, model: $("#model").value.trim() }),
    });
    const cctr = summary.cctr || {};
    const cctrNote = cctr.ran ? t("export_cctr_ok").replace("{n}", cctr.methods)
      : (cctr.reason === "skipped" ? "" : t("export_cctr_failed"));
    toast(t("export_done")
      .replace("{project}", summary.project)
      .replace("{setup}", summary.setupDirectory || summary.setup)
      .replace("{written}", summary.writtenThisCall)
      .replace("{total}", summary.totalMcis)
      .replace("{ok}", summary.successes) + cctrNote, false);
    if (!cctr.ran && cctr.reason && cctr.reason !== "skipped") console.warn("CCTR:", cctr.reason);
  } catch (error) {
    toast(error.message);
  } finally {
    releaseProgress(ticket);
  }
});

// ================= Language Switcher Listeners =================
$("#lang-en").onclick = () => setLanguage("en");
$("#lang-zh").onclick = () => setLanguage("zh");

// Init
setLanguage(state.currentLang);
checkPathSafety($("#project-root").value.trim());
runPreflight($("#project-root").value.trim(), false);
// 刷新或误关标签页之后，接回仍在服务端运行的那个批次。
// After a reload or an accidental tab close, reattach to a batch still running server-side.
reattachRefactoringJob();
