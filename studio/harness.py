from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import os
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any, Callable

from studio import gradle_support
from studio.long_paths import long_path


def _strip_long_path_prefix(path: Path) -> Path:
    r"""
    cmd.exe（用来跑 mvnw.cmd/gradlew.bat）明确不支持 \\?\ 扩展前缀作为当前目录，
    即使路径本身没超过 MAX_PATH 也会直接报错，所以传给 subprocess 的 cwd 必须是裸路径。
    cmd.exe (used to run mvnw.cmd/gradlew.bat) rejects the \\?\ long-path prefix as a
    current directory outright, even when the path itself is well under MAX_PATH, so the
    cwd handed to subprocess must be the plain path.
    """
    text = str(path)
    if text.startswith("\\\\?\\"):
        return Path(text[4:])
    return path


class HarnessStatus(StrEnum):
    """Harness 状态 / Harness execution state."""

    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass
class HarnessEvidence:
    """
    编译、测试和可选 PIT 的机器证据。
    Machine evidence for compilation, tests, and optional PIT.
    """

    compile_status: HarnessStatus = HarnessStatus.NOT_RUN
    test_status: HarnessStatus = HarnessStatus.NOT_RUN
    pit_status: HarnessStatus = HarnessStatus.NOT_RUN
    commands: list[list[str]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    # {"pkg.Class#method": "PASSED"|"FAILED"|"ERROR"|"SKIPPED"}，按测试身份比较，
    # 而不是只比较总数（两个不同的测试集合总数可能凑巧相等）。
    # Keyed by test identity so two different test sets with the same aggregate
    # counts are not mistaken for an identical result.
    test_results: dict[str, str] = field(default_factory=dict)
    # PIT 的变异测试证据：总数、按状态分类计数、mutation score，以及每个变异体的
    # 身份 -> 状态映射（用于比较 baseline 里已被杀死的变异体是否在 candidate 里仍被杀死）。
    # PIT mutation evidence: totals, per-status counts, mutation score, and a
    # mutant-identity -> status map (used to check that every mutant killed in the
    # baseline is still killed in the candidate).
    mutation_total: int = 0
    mutation_score: float | None = None
    mutation_counts: dict[str, int] = field(default_factory=dict)
    mutants: dict[str, str] = field(default_factory=dict)
    # 这次验证实际覆盖了什么范围。裁剪到相关模块之后，"测试通过"不再等于"整个项目通过"，
    # 报告里必须能看出区别，否则读的人会把一个窄得多的结论当成全量回归。
    # What this validation actually covered. Once narrowed to the relevant modules, "tests
    # passed" no longer means "the whole project passed"; the report has to show the
    # difference, or a much narrower result gets read as a full regression.
    scope: str = "the whole project"
    # 每个阶段各花了多少秒。论文 RQ3 需要这个数字，而它补不回来——跑的时候没记，事后
    # 没有任何办法还原。OPTIMIZATION_LOG 里"目前只能手工处理"说的就是这件事。
    # PIT 单独成项而不是并进"重构耗时"，因为它常常比编译加测试本身还久，混在一起会让
    # 那个数字失去意义。
    # Seconds spent in each phase. RQ3 needs these and they cannot be reconstructed later —
    # unrecorded at run time, they are simply gone, which is what OPTIMIZATION_LOG means by
    # "has to be handled by hand". PIT is its own entry rather than folded into refactoring
    # time, because it often outlasts compile and test combined and would drown that number.
    durations: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "compileStatus": self.compile_status,
            "testStatus": self.test_status,
            "pitStatus": self.pit_status,
            "commands": self.commands,
            "diagnostics": self.diagnostics,
            "testResults": self.test_results,
            "mutationTotal": self.mutation_total,
            "mutationScore": self.mutation_score,
            "mutationCounts": self.mutation_counts,
            "mutants": self.mutants,
            "scope": self.scope,
            "durations": self.durations,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HarnessEvidence":
        return cls(
            compile_status=HarnessStatus(str(value.get("compileStatus", "NOT_RUN"))),
            test_status=HarnessStatus(str(value.get("testStatus", "NOT_RUN"))),
            pit_status=HarnessStatus(str(value.get("pitStatus", "NOT_RUN"))),
            commands=[list(command) for command in value.get("commands", [])],
            diagnostics=[str(item) for item in value.get("diagnostics", [])],
            test_results={str(key): str(status) for key, status in (value.get("testResults") or {}).items()},
            mutation_total=int(value.get("mutationTotal", 0) or 0),
            mutation_score=value.get("mutationScore"),
            mutation_counts={str(key): int(count) for key, count in (value.get("mutationCounts") or {}).items()},
            mutants={str(key): str(status) for key, status in (value.get("mutants") or {}).items()},
            scope=str(value.get("scope", "the whole project")),
            durations={str(k): float(v) for k, v in (value.get("durations") or {}).items()},
        )


def verification_failure_reason(evidence: HarnessEvidence | dict[str, Any], run_pit: bool,
                                expected_test_classes: list[str] | None = None) -> str | None:
    """Return why an execution cannot serve as before/after verification evidence.

    Exit status alone is insufficient: a build can exit zero while selecting no tests,
    while every selected test is skipped, or while PIT emits no usable report. Both the
    interactive and batch paths use this gate so they cannot silently accept different
    evidence standards.
    """
    values = evidence.as_dict() if isinstance(evidence, HarnessEvidence) else evidence
    if str(values.get("compileStatus")) != HarnessStatus.PASSED:
        return "Compilation did not pass / 编译未通过"
    if str(values.get("testStatus")) != HarnessStatus.PASSED:
        return "Test execution did not pass / 测试执行未通过"

    results = values.get("testResults") or {}
    executed_classes = {
        key.partition("#")[0] for key, status in results.items()
        if status != "SKIPPED" and "#" in key
    }
    if not executed_classes:
        return "No non-skipped test result was produced / 没有产生实际执行的测试结果"
    missing = sorted(set(expected_test_classes or []) - executed_classes)
    if missing:
        return "Selected test classes produced no non-skipped result: " + ", ".join(missing)

    if run_pit:
        if str(values.get("pitStatus")) != HarnessStatus.PASSED:
            return "PIT did not pass / PIT 未通过"
        if not values.get("mutants") or values.get("mutationScore") is None:
            return "PIT produced no usable mutation evidence / PIT 未产生可用的变异证据"
    return None


def mutation_regressed(baseline: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """
    因为 MCI 重构只改测试文件，生产代码不变，所以同一个变异体在 baseline 和 candidate
    里应该是同一批。比总分是否下降更强的判据是：baseline 里已经被杀死的每个变异体，在
    candidate 里必须仍然被杀死。
    Because an MCI refactor only touches test files (production code is untouched), the
    same mutants should exist in both baseline and candidate. A stronger check than "did
    the aggregate score drop" is: every mutant killed in the baseline must still be killed
    in the candidate.
    """
    baseline_mutants: dict[str, str] = baseline.get("mutants") or {}
    candidate_mutants: dict[str, str] = candidate.get("mutants") or {}
    return any(
        status == "KILLED" and candidate_mutants.get(key) != "KILLED"
        for key, status in baseline_mutants.items()
    )


_POM_NS = "http://maven.apache.org/POM/4.0.0"
# 这个版本兼容目前常见的 pitest-maven 版本；真正要对齐的是 junit-platform-launcher，
# 下面会按目标项目实际解析出的 junit-platform-engine 版本动态匹配。
# This version is compatible with the pitest-maven releases in common use; the piece
# that actually needs to match the target project is junit-platform-launcher, matched
# dynamically below against whatever junit-platform-engine version the project resolves.
_PITEST_JUNIT5_PLUGIN_VERSION = "1.2.1"


def _detect_junit_platform_engine_version(root: Path, maven_repo_local: str | None) -> str | None:
    """跑一次很快的、非递归的 dependency:tree（只看聚合根自己，不遍历整个 reactor）
    找出这个项目实际解析到的 junit-platform-engine 版本。
    Runs a fast, non-recursive dependency:tree (just the aggregator root, not the whole
    reactor) to find the junit-platform-engine version this project actually resolves."""
    executable = "mvn.cmd" if os.name == "nt" else "mvn"
    command = [executable]
    if maven_repo_local:
        command.append(f"-Dmaven.repo.local={maven_repo_local}")
    command += ["-N", "dependency:tree", "-Dincludes=org.junit.platform:junit-platform-engine"]
    try:
        completed = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"junit-platform-engine:jar:([\d.]+)", completed.stdout)
    return match.group(1) if match else None


def ensure_pit_junit5_support(root: Path, maven_repo_local: str | None = None) -> bool:
    """
    直接用命令行调用 `org.pitest:pitest-maven:mutationCoverage`（不是项目自己 pom 里
    配置好的构建步骤）时，PIT 完全不知道要用 pitest-junit5-plugin 去识别 JUnit 5
    测试——这个信息只能通过某个 pom.xml 的 <plugin><dependencies> 声明传递给 Maven，
    没有任何 -D 参数能做到。而且只加 pitest-junit5-plugin 还不够：它自带的
    junit-platform-launcher 版本如果比项目实际用的 junit-platform-engine 旧，minion
    子进程会在测试发现阶段抛 "OutputDirectoryProvider not available" 直接崩溃
    （UNKNOWN_ERROR）。两个都要显式声明，且 launcher 版本必须跟这个项目实际解析到的
    engine 版本对齐，所以这里会先跑一次快速探测再决定版本号，不能写死。
    只改隔离副本（workspace）的根 pom.xml，绝不碰被检测项目自己的源码；已经配置过
    pitest-maven 的项目直接跳过，重复调用是幂等的。只处理 Maven 项目、且项目本身
    用 JUnit 5（探测不到 junit-platform-engine 就说明是 JUnit 4 或没用 JUnit，PIT
    自带的 JUnit4 支持已经够用，不用改 pom）。

    When `org.pitest:pitest-maven:mutationCoverage` is invoked directly from the command
    line (not as a build step the project's own pom already configured), PIT has no way
    of knowing to use pitest-junit5-plugin to recognize JUnit 5 tests — that can only be
    communicated to Maven via some pom.xml's <plugin><dependencies> declaration, and no
    -D flag can do it. Adding pitest-junit5-plugin alone isn't enough either: if the
    junit-platform-launcher version it pulls in transitively is older than the project's
    actual junit-platform-engine, the minion subprocess crashes during test discovery
    with "OutputDirectoryProvider not available" (UNKNOWN_ERROR). Both must be declared
    explicitly, and the launcher version must match whatever engine version this project
    actually resolves to — hence the quick detection pass before deciding on a version,
    rather than hard-coding one. Only ever patches the isolated workspace copy's root
    pom.xml, never the analyzed project's own source; a project that already configures
    pitest-maven is left alone, and calling this repeatedly is a no-op. Maven projects
    only, and only those actually on JUnit 5 (failing to detect junit-platform-engine
    means JUnit 4 or no JUnit, where PIT's built-in JUnit4 support is already enough and
    the pom doesn't need touching).
    """
    pom_path = root / "pom.xml"
    if not pom_path.is_file():
        return False
    ET.register_namespace("", _POM_NS)
    tree = ET.parse(pom_path)
    project = tree.getroot()

    def tag(name: str) -> str:
        return f"{{{_POM_NS}}}{name}"

    for plugin in project.iter(tag("plugin")):
        artifact_id = plugin.find(tag("artifactId"))
        if artifact_id is not None and artifact_id.text == "pitest-maven":
            return False

    launcher_version = _detect_junit_platform_engine_version(root, maven_repo_local)
    if launcher_version is None:
        return False

    build = project.find(tag("build"))
    if build is None:
        build = ET.SubElement(project, tag("build"))
    plugins = build.find(tag("plugins"))
    if plugins is None:
        plugins = ET.SubElement(build, tag("plugins"))

    plugin = ET.SubElement(plugins, tag("plugin"))
    ET.SubElement(plugin, tag("groupId")).text = "org.pitest"
    ET.SubElement(plugin, tag("artifactId")).text = "pitest-maven"
    dependencies = ET.SubElement(plugin, tag("dependencies"))

    junit5_dependency = ET.SubElement(dependencies, tag("dependency"))
    ET.SubElement(junit5_dependency, tag("groupId")).text = "org.pitest"
    ET.SubElement(junit5_dependency, tag("artifactId")).text = "pitest-junit5-plugin"
    ET.SubElement(junit5_dependency, tag("version")).text = _PITEST_JUNIT5_PLUGIN_VERSION

    launcher_dependency = ET.SubElement(dependencies, tag("dependency"))
    ET.SubElement(launcher_dependency, tag("groupId")).text = "org.junit.platform"
    ET.SubElement(launcher_dependency, tag("artifactId")).text = "junit-platform-launcher"
    ET.SubElement(launcher_dependency, tag("version")).text = launcher_version

    tree.write(pom_path, encoding="utf-8", xml_declaration=True)
    return True


def _report_files(root: Path, *patterns: str) -> list[Path]:
    r"""
    找到的报告文件路径，在 Windows 上带 \\?\ 长路径前缀。Spring Security 的测试类全名很长，
    放进批次副本（batch-<32 位 id>）后 Gradle 的报告路径达到 263 个字符：Gradle 会自己缩短文件名，
    rglob 也列得出来，但不带前缀就打不开，于是明明跑过的测试读不到结果，被判成"环境未就绪"。
    Report paths found under root, with the \\?\ long-path prefix on Windows. Spring Security's
    long test class names put Gradle's report paths at 263 characters inside a batch copy
    (batch-<32-char id>): Gradle shortens the file name itself and rglob lists it, but without the
    prefix it cannot be opened, so tests that did run produced no readable result and were
    classified as an unready environment.
    """
    return [long_path(path) for pattern in patterns for path in root.rglob(pattern)]


def is_module_directory(directory: Path) -> bool:
    """Maven 模块（有 pom.xml）或 Gradle 子项目的目录 / A Maven module (has a pom.xml) or a
    Gradle subproject directory."""
    return (directory / "pom.xml").is_file() or gradle_support.is_module_directory(directory)


@dataclass(frozen=True)
class BuildScope:
    """这次验证要覆盖的模块与测试类。两个都为空表示不裁剪，走全 reactor。
    The modules and test classes this validation covers. Both empty means no scoping and a
    full-reactor run."""

    modules: tuple[str, ...] = ()
    test_classes: tuple[str, ...] = ()

    def describe(self) -> str:
        if not self.modules:
            return "the whole project"
        return f"{len(self.modules)} module(s): " + ", ".join(sorted(self.modules))


class ProjectHarness:
    """运行论文中的编译与测试检验 / Runs the paper's compile and test checks."""

    def __init__(self, maven_repo_local: str | Path | None = None) -> None:
        """
        maven_repo_local：显式指定这次运行用哪个本地 Maven 仓库。Maven 不会根据项目
        目录自动选择仓库，不传就是默认共享的 ~/.m2/repository；多个项目并行跑（比如
        同时验证 dubbo 和 druid）应该各自传一个独立目录，避免共享仓库被并发写入踩坏。
        maven_repo_local: explicitly pins which local Maven repository this run uses.
        Maven does not pick a repository based on the project directory on its own; when
        omitted this stays the default shared ~/.m2/repository. Running multiple projects
        in parallel (e.g. validating dubbo and druid at the same time) should each pass an
        isolated directory here, to avoid concurrent writes corrupting a shared repository.
        """
        self.maven_repo_local = str(Path(maven_repo_local).resolve()) if maven_repo_local else None
        self._test_jar_artifacts: dict[str, set[str]] = {}
        self._gradle_projects: dict[str, gradle_support.GradleProjects] = {}

    def _maven_repo_args(self) -> list[str]:
        return [f"-Dmaven.repo.local={self.maven_repo_local}"] if self.maven_repo_local else []

    def _scope_args(self, root: Path, scope: BuildScope | None) -> list[str]:
        """
        把构建限定到这次改动真正涉及的模块。

        不加 `-pl`，Maven 会把整个 reactor 的生命周期走一遍——Dubbo 有 124 个模块，哪怕
        `-Dtest` 已经限定了只执行哪个测试类，每个模块仍要各自跑一遍 enforcer/checkstyle/
        spotless 和编译。一次测试代码的改动只碰一两个文件，为它冷编译 124 个模块是纯粹的
        浪费。

        `-pl X -am` 构建 X 和它**依赖**的模块，但不构建**依赖 X 的**模块。通常没问题，因为
        别的模块不会用到 X 的测试产物——除非 X 发布了 test-jar。Dubbo 里确实有 3 个模块这么
        做，所以这里会去查：只要目标模块的 test-jar 被别人依赖，就补上 `-amd`，把下游也带进来。
        Limits the build to the modules this change actually touches.

        Without `-pl`, Maven walks the whole reactor's lifecycle — Dubbo has 124 modules, and
        every one of them still runs its own enforcer/checkstyle/spotless and compile even
        when `-Dtest` already narrows which test class executes. Cold-compiling 124 modules
        for a one- or two-file test change is pure waste.

        `-pl X -am` builds X and the modules it **depends on**, not the modules that **depend
        on** X. That is normally fine, since nothing consumes another module's test output —
        unless X publishes a test-jar. Three Dubbo modules do exactly that, so this checks:
        when the target module's test-jar is consumed elsewhere, `-amd` is added to pull those
        downstream modules back in.
        """
        if scope is None or not scope.modules:
            return []
        modules = sorted({value.replace("\\", "/").strip("/") for value in scope.modules if value})
        if not modules:
            return []
        args = ["-pl", ",".join(modules), "-am"]
        if self._has_test_jar_consumer(root, modules):
            args.append("-amd")
        return args

    def _has_test_jar_consumer(self, root: Path, modules: list[str]) -> bool:
        """这些模块里，有没有哪个的 test-jar 被别的模块依赖。结果按项目缓存，只扫一次。
        Whether any of these modules has its test-jar consumed by another module. Cached per
        project, so the scan happens once."""
        key = str(root)
        consumed = self._test_jar_artifacts.get(key)
        if consumed is None:
            consumed = set()
            for pom in root.rglob("pom.xml"):
                if "target" in pom.parts:
                    continue
                try:
                    text = pom.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if "test-jar" not in text:
                    continue
                for match in re.finditer(
                        r"<artifactId>\s*([\w.\-]+)\s*</artifactId>(?:(?!</dependency>).)*?<type>\s*test-jar\s*</type>",
                        text, re.DOTALL):
                    consumed.add(match.group(1))
            self._test_jar_artifacts[key] = consumed
        if not consumed:
            return False
        return any(Path(module).name in consumed for module in modules)

    @staticmethod
    def _test_filter_args(scope: BuildScope | None) -> list[str]:
        """
        `-am` 会把依赖模块也拉进 reactor，而 Maven 对 reactor 里每个模块套用同一个 `-Dtest`
        过滤。`-DfailIfNoTests=false` 只管"这个模块压根没有测试"，管不住"有测试但没有匹配
        `-Dtest` 的那个类"——后者要靠 surefire 自己的 `failIfNoSpecifiedTests`。两个都得加，
        否则第一个不相关的依赖模块就会把整个 reactor 中止掉。
        `-am` pulls dependency modules into the reactor, and Maven applies the same `-Dtest`
        filter to each of them. `-DfailIfNoTests=false` only covers "this module has no tests
        at all", not "it has tests, just none matching this `-Dtest` class" — that needs
        surefire's own `failIfNoSpecifiedTests`. Both are required, or the first unrelated
        dependency module aborts the whole reactor.
        """
        if scope is None or not scope.test_classes:
            return []
        return [f"-Dtest={','.join(sorted(scope.test_classes))}", "-DfailIfNoTests=false",
                "-Dsurefire.failIfNoSpecifiedTests=false"]

    def expected_test_classes(self) -> list[str]:
        """The full-project harness has no narrower expected class set."""
        return []

    def validate_targets(self, project_root: Path, test_classes: list[str],
                         modules: list[str] | None = None) -> HarnessEvidence:
        """Run the selected test classes after a reactor-wide test stopped early.

        This is deliberately a second receipt, not a replacement for the full-project
        gate.  A Maven reactor can stop in an upstream module before it reaches the MCI's
        module; comparing two equally-truncated report sets would otherwise be a false
        success.
        """
        root = _strip_long_path_prefix(project_root)
        evidence = HarnessEvidence(compile_status=HarnessStatus.PASSED)
        classes = sorted({value.strip() for value in test_classes if value and value.strip()})
        if not classes:
            evidence.test_status = HarnessStatus.UNAVAILABLE
            evidence.diagnostics.append("No target test classes could be resolved / 无法解析目标测试类")
            return evidence

        module_list = sorted({value.replace("\\", "/").strip("/") for value in (modules or []) if value})
        if (root / "pom.xml").is_file() or (root / "mvnw").is_file() or (root / "mvnw.cmd").is_file():
            executable = str(root / "mvnw.cmd") if os.name == "nt" and (root / "mvnw.cmd").is_file() else (
                str(root / "mvnw") if (root / "mvnw").is_file() else ("mvn.cmd" if os.name == "nt" else "mvn")
            )
            command = [executable, *self._maven_repo_args(), *self._english_output_args(),
                       *self._style_check_skip_args()]
            if module_list:
                command.extend(["-pl", ",".join(module_list), "-am"])
            command.extend([
                "test", f"-Dtest={','.join(classes)}", "-DfailIfNoTests=false",
                "-Dsurefire.failIfNoSpecifiedTests=false",
            ])
        elif gradle_support.is_gradle_build(root):
            _, command, _ = self._gradle_commands(root, BuildScope(tuple(module_list), tuple(classes)))
        else:
            evidence.test_status = HarnessStatus.UNAVAILABLE
            evidence.diagnostics.append("No supported Maven or Gradle build was found / 未找到 Maven 或 Gradle 构建")
            return evidence

        self._clear_test_reports(root)
        started_at = time.time() - 1
        evidence.test_status = self._execute(command, root, evidence)
        evidence.test_results = self._collect_test_identities(root, started_at)
        observed = {key.split("#", 1)[0] for key in evidence.test_results}
        missing = [name for name in classes if name not in observed and name.rsplit(".", 1)[-1] not in observed]
        if missing:
            evidence.test_status = HarnessStatus.FAILED
            evidence.diagnostics.append(
                "Target tests produced no fresh receipt: " + ", ".join(missing)
                + " / 目标测试未产生新的执行凭据"
            )
        return evidence

    @staticmethod
    def _english_output_args() -> list[str]:
        """
        强制 javac/Maven 用英文输出诊断。

        中文 Windows 上 javac 发的是 GBK 编码的本地化消息，而我们按 UTF-8 加 errors=replace
        去解——GBK 字节遇上 UTF-8 解码器会被整段换成 U+FFFD，不可逆。实测一次编译失败，模型
        收到的是：

            ����:   ���� createMockServiceDiscovery(MetadataInfo)
            λ��: �� MockServiceDiscovery

        方法名和类名是 ASCII 所以活了下来，而中文——也就是"错在哪"这个信息本身——全没了。
        模型分不清这是"找不到符号"还是"类型不匹配"，两轮修复因此全部白烧。诊断回灌的循环
        一直是对的，它只是从第一天起就在消费垃圾。

        改成英文既根除了编码问题，也给了模型它最擅长解析的格式。
        Forces javac/Maven to emit diagnostics in English. On a Chinese Windows, javac emits
        GBK-encoded localized messages while we decode as UTF-8 with errors=replace, so those
        bytes become an irrecoverable run of U+FFFD. Measured on a real compile failure, the
        model received `����:   ���� createMockServiceDiscovery(MetadataInfo)`: the method and
        class names survived because they are ASCII, while the Chinese — the part saying what
        actually went wrong — did not. Unable to tell "cannot find symbol" from "incompatible
        types", both repair rounds were wasted. The feedback loop was always correct; it had
        been consuming garbage from the start. English removes the encoding problem outright
        and gives the model the format it parses best.
        """
        return ["-Duser.language=en", "-Duser.country=US"]

    @staticmethod
    def _style_check_skip_args() -> list[str]:
        """
        论文对"Syntactic Validity"的定义是"successful compilation"——是编译器（javac）
        层面的概念，不是某个项目自选的代码风格检查插件。Spotless 这类格式检查即使不通过，
        代码本身也是完全合法、能编译的 Java，只是不符合某个项目的排版约定。让它参与
        判定会带来两类和"这段代码到底能不能编译"无关的假阴性：(1) 这是一次性隔离副本，
        我们自己往 pom.xml 里插入 PIT 配置时会重新序列化整个文件，格式和原文件不完全
        一样（哪怕语义等价），会被判定"违规"；(2) 模型生成的候选代码风格上有瑕疵、但
        语法完全合法时，也会被这类检查拦下来，和论文"能不能编译"的定义对不上。所以
        这两个标准开关都跳过，跟 -DskipTests 类似，只影响这次隔离验证，不代表我们认为
        代码风格不重要。
        The paper's own definition of "Syntactic Validity" is "successful compilation" —
        a compiler-level (javac) concept, not an opt-in code-style linter's opinion.
        Failing Spotless-style checks does not mean the code is invalid Java; it's fully
        legal and compilable, just not to some project's formatting taste. Letting it
        gate this classification produces two kinds of false negative unrelated to
        whether the code actually compiles: (1) this is a disposable isolated copy, and
        when we inject PIT config into its pom.xml we re-serialize the whole file, so it
        no longer matches the original byte-for-byte (even though it's semantically
        identical) and would be flagged as a "violation"; (2) a model candidate that is
        syntactically valid Java but stylistically imperfect would be rejected by such a
        check, which doesn't match the paper's own "does it compile" definition. Both
        standard toggles are skipped here, in the same spirit as -DskipTests — this only
        affects this isolated verification run, and isn't a statement that code style
        doesn't matter in general.
        """
        return ["-Dspotless.check.skip=true", "-Dspotless.apply.skip=true"]

    def validate(self, project_root: Path, run_pit: bool = False,
                 progress_callback: Callable[[str, int, str], None] | None = None,
                 scope: BuildScope | None = None) -> HarnessEvidence:
        def progress(phase: str, percent: int, detail: str) -> None:
            if progress_callback:
                progress_callback(phase, percent, detail)

        project_root = _strip_long_path_prefix(project_root)
        evidence = HarnessEvidence()
        progress("DISCOVERING_BUILD", 5, "Detecting Maven or Gradle build")
        build = self._build_commands(project_root, scope)
        if build is None:
            evidence.compile_status = HarnessStatus.UNAVAILABLE
            evidence.test_status = HarnessStatus.UNAVAILABLE
            evidence.pit_status = HarnessStatus.UNAVAILABLE
            evidence.diagnostics.append("No supported Maven or Gradle build was found / 未找到 Maven 或 Gradle 构建")
            return evidence
        if gradle_support.is_gradle_command(build[0]):
            problem = self._gradle_environment_problem(project_root)
            if problem is not None:
                evidence.compile_status = HarnessStatus.UNAVAILABLE
                evidence.test_status = HarnessStatus.UNAVAILABLE
                evidence.pit_status = HarnessStatus.UNAVAILABLE
                evidence.diagnostics.append(problem)
                return evidence

        # 范围要如实写进证据里。裁剪之后"测试通过"的含义变窄了——它说的是这些模块的这些
        # 测试类通过，不是整个项目通过。不记下来，后面看报告的人会把两者当成一回事。
        # The scope goes into the evidence. Once narrowed, "tests passed" means something
        # smaller — these test classes in these modules passed, not the whole project. Without
        # recording it, whoever reads the report later will mistake one for the other.
        described = scope.describe() if scope is not None else "the whole project"
        evidence.scope = described
        compile_command, test_command, pit_command = build
        progress("COMPILING", 12, f"Compiling test sources for {described}")
        phase_started = time.time()
        evidence.compile_status = self._execute(compile_command, project_root, evidence)
        evidence.durations["compile"] = round(time.time() - phase_started, 2)
        if evidence.compile_status == HarnessStatus.PASSED:
            progress("TESTING", 45, f"Running the regression suite for {described}")
            self._clear_test_reports(project_root)
            test_started_at = time.time() - 1
            phase_started = time.time()
            evidence.test_status = self._execute(test_command, project_root, evidence)
            evidence.durations["test"] = round(time.time() - phase_started, 2)
            evidence.test_results = self._collect_test_identities(project_root, test_started_at)
        else:
            evidence.test_status = HarnessStatus.NOT_RUN
        if run_pit and evidence.test_status == HarnessStatus.PASSED and pit_command:
            progress("MUTATION_TESTING", 78, "Running PIT mutation testing")
            # 减去一点余量，避免文件系统 mtime 精度和时钟误差把本次刚生成的报告漏掉。
            # Subtract a small buffer so filesystem mtime resolution or clock skew
            # doesn't cause the report just generated by this run to be missed.
            pit_started_at = time.time() - 1
            phase_started = time.time()
            evidence.pit_status = self._execute(pit_command, project_root, evidence)
            evidence.durations["pit"] = round(time.time() - phase_started, 2)
            mutation = self._collect_mutation_summary(project_root, pit_started_at)
            if mutation is not None:
                evidence.mutation_total = mutation["total"]
                evidence.mutation_score = mutation["mutationScore"]
                evidence.mutation_counts = mutation["counts"]
                evidence.mutants = mutation["mutants"]
        else:
            evidence.pit_status = HarnessStatus.NOT_RUN
        progress("COLLECTING_EVIDENCE", 96, "Collecting fresh test and mutation reports")
        return evidence

    @staticmethod
    def _clear_test_reports(root: Path) -> None:
        """Remove prior XML receipts so a later invocation cannot reuse them as evidence."""
        reports = _report_files(root, "surefire-reports/TEST-*.xml", "test-results/*/TEST-*.xml")
        for report in reports:
            try:
                report.unlink()
            except OSError:
                continue

    @staticmethod
    def _collect_test_identities(root: Path, since: float | None = None) -> dict[str, str]:
        identities: dict[str, str] = {}
        reports = _report_files(root, "surefire-reports/TEST-*.xml", "test-results/*/TEST-*.xml")
        for report in reports:
            if since is not None:
                try:
                    if report.stat().st_mtime < since:
                        continue
                except OSError:
                    continue
            try:
                suite = ET.parse(report).getroot()
            except (OSError, ET.ParseError):
                continue
            for testcase in suite.findall("testcase"):
                classname = testcase.attrib.get("classname", "")
                name = testcase.attrib.get("name", "")
                key = f"{classname}#{name}"
                if testcase.find("failure") is not None:
                    status = "FAILED"
                elif testcase.find("error") is not None:
                    status = "ERROR"
                elif testcase.find("skipped") is not None:
                    status = "SKIPPED"
                else:
                    status = "PASSED"
                identities[key] = status
        return identities

    @staticmethod
    def _collect_mutation_summary(root: Path, since: float) -> dict[str, Any] | None:
        """
        只收集这次调用期间新生成的 mutations.xml（按 mtime 过滤），避免在共享 workspace
        里把前一个 MCI 遗留的报告也算进这次结果。
        Only collects mutations.xml files freshly generated by this call (filtered by
        mtime) so a leftover report from an earlier MCI in a shared workspace is not
        counted toward this result.

        pit-reports/*/mutations.xml（要求 pit-reports 和 mutations.xml 之间恰好隔一层
        子目录）曾经是这里用的 glob，但这个 PIT 版本 / 配置实际输出的路径是
        <module>/target/pit-reports/mutations.xml，中间没有那层子目录——导致这个 glob
        从来没匹配上过任何文件，本方法一直静默返回 None，mutation_total/mutation_score/
        mutation_counts/mutants 全项目所有跑批从未真正被填充过。mutation_regressed() 因此
        一直在拿两个空字典互相比较，"没有变异体退化"这个判定从建成以来就是永远为真的
        vacuous truth，Functional Integrity 这一层实际上只被 pitStatus（PIT 这个 Maven
        目标的退出码）把关，从没有真正按变异体级别验证过。改成先用 rglob 找所有
        mutations.xml，再按路径里是否含 pit-reports 这一段过滤，不再依赖固定的目录深度。
        pit-reports/*/mutations.xml (requiring exactly one subdirectory between
        pit-reports and mutations.xml) used to be the glob here, but this PIT
        version/configuration actually writes to <module>/target/pit-reports/mutations.xml
        with no such intermediate directory -- so this glob never matched a single file,
        this method has always silently returned None, and mutation_total/mutation_score/
        mutation_counts/mutants have never actually been populated across this entire
        project's runs. mutation_regressed() has therefore always been comparing two
        empty dicts against each other -- "no mutation regression" has been a vacuous
        truth since this was built, and the Functional Integrity tier has in practice
        only ever been gated by pitStatus (the PIT Maven goal's exit code), never by a
        genuine mutant-level comparison. Fixed by globbing for mutations.xml anywhere
        under root and filtering by whether "pit-reports" appears in its path, instead
        of assuming a fixed directory depth.
        """
        reports = [
            path for path in _report_files(root, "mutations.xml")
            if "pit-reports" in path.parts and path.stat().st_mtime >= since
        ]
        if not reports:
            return None
        counts: dict[str, int] = {}
        mutants: dict[str, str] = {}
        for report in reports:
            try:
                root_element = ET.parse(report).getroot()
            except (OSError, ET.ParseError):
                continue
            for mutation in root_element.findall("mutation"):
                status = (mutation.attrib.get("status") or mutation.findtext("status") or "UNKNOWN").upper()
                counts[status] = counts.get(status, 0) + 1
                key = "|".join((
                    mutation.findtext("mutatedClass") or "",
                    mutation.findtext("mutatedMethod") or "",
                    mutation.findtext("lineNumber") or "",
                    mutation.findtext("mutator") or "",
                ))
                mutants[key] = status
        total = sum(counts.values())
        killed = counts.get("KILLED", 0)
        return {
            "total": total,
            "counts": counts,
            "mutationScore": (killed / total) if total else None,
            "mutants": mutants,
        }

    def _build_commands(self, root: Path,
                        scope: BuildScope | None = None) -> tuple[list[str], list[str], list[str] | None] | None:
        if (root / "mvnw.cmd").is_file() or (root / "mvnw").is_file() or (root / "pom.xml").is_file():
            executable = str(root / "mvnw.cmd") if os.name == "nt" and (root / "mvnw.cmd").is_file() else (
                str(root / "mvnw") if (root / "mvnw").is_file() else ("mvn.cmd" if os.name == "nt" else "mvn")
            )
            # -Dmaven.repo.local 紧跟在 mvn/mvnw 后面、goal 前面插入，三条命令（编译、
            # 测试、PIT）都必须带，否则没传这个参数的那条命令会静默退回默认共享仓库。
            # -Dmaven.repo.local is inserted right after mvn/mvnw and before the goal; all
            # three commands (compile, test, PIT) must carry it, or whichever one omits it
            # silently falls back to the default shared repository.
            repo_args = self._maven_repo_args()
            style_check_skip_args = self._style_check_skip_args()
            prefix = [executable, *repo_args, *self._english_output_args(), *style_check_skip_args,
                      *self._scope_args(root, scope)]
            test_filter = self._test_filter_args(scope)
            pit_command = [*prefix, "org.pitest:pitest-maven:mutationCoverage", "-DoutputFormats=XML"]
            if scope is not None and scope.test_classes:
                pattern = ",".join(sorted(scope.test_classes))
                # 与 Gradle 初始化脚本同一条规则：被变异的类取目标测试所在的包。不给的话 PIT 回落到
                # groupId.*，-am 带上的每个上游模块都整包变异——dubbo 一个 MCI 变出 39118 个变异体，
                # 99.7% 无覆盖，一侧跑 25 分钟，变异分数也被无关代码稀释到 0.002。
                # The same rule as the Gradle init script: mutate the classes in the target tests'
                # packages. Without it PIT falls back to groupId.* and mutates every upstream module
                # -am pulls in — one dubbo MCI produced 39118 mutants, 99.7% uncovered, 25 minutes a
                # side, with the score diluted to 0.002 by unrelated code.
                packages = sorted({name.rsplit(".", 1)[0] + ".*" for name in scope.test_classes if "." in name})
                pit_command.extend([f"-DtargetTests={pattern}", "-DfailWhenNoMutations=false"])
                if packages:
                    pit_command.append(f"-DtargetClasses={','.join(packages)}")
            return (
                [*prefix, "-DskipTests", "test-compile"],
                [*prefix, "test", *test_filter],
                pit_command,
            )
        if gradle_support.is_gradle_build(root):
            return self._gradle_commands(root, scope)
        return None

    def _gradle_projects_for(self, root: Path) -> gradle_support.GradleProjects:
        """每个项目根只问一次 Gradle。一批 MCI 共享一个副本，所以一批只付一次。
        Gradle is asked once per project root. A batch shares one workspace, so a batch pays once."""
        key = str(root)
        projects = self._gradle_projects.get(key)
        if projects is None:
            projects = gradle_support.discover_projects(root)
            self._gradle_projects[key] = projects
        return projects

    def _gradle_scope(self, root: Path, scope: BuildScope | None) -> tuple[list[str], list[str]]:
        """
        Gradle 版的 `-pl X -am [-amd]`：返回 (目标项目, 需要一起编译的下游项目)。

        `:X:testClasses` 本身就会先构建 X 依赖的项目，相当于 `-am`。`-amd` 的对应物是依赖 X 的
        `tests` 输出的项目——Spring Security 里 25 处 `project(path: ..., configuration: 'tests')`，
        改了 X 的测试代码，这些项目的测试可能因此编译不过，必须一起编译；和 Maven 一样取传递闭包。
        The Gradle form of `-pl X -am [-amd]`: returns (target projects, downstream projects to
        compile with them). `:X:testClasses` already builds what X depends on, which is `-am`.
        The `-amd` counterpart is the projects consuming X's `tests` output — 25 such edges in
        Spring Security — whose tests may stop compiling when X's test code changes, so they are
        compiled too, transitively, as Maven does.
        """
        if scope is None or not scope.modules:
            return [], []
        projects = self._gradle_projects_for(root)
        targets = sorted({project for project in (projects.project_for(module) for module in scope.modules) if project})
        downstream: set[str] = set()
        pending = list(targets)
        while pending:
            for consumer in projects.test_output_consumers.get(pending.pop(), set()):
                if consumer not in downstream and consumer not in targets:
                    downstream.add(consumer)
                    pending.append(consumer)
        return targets, sorted(downstream)

    @staticmethod
    def _gradle_task(project: str | None, task: str) -> str:
        if project is None:
            return task
        return f":{task}" if project == ":" else f"{project}:{task}"

    def _gradle_commands(self, root: Path, scope: BuildScope | None) -> tuple[list[str], list[str], list[str]]:
        prefix = [gradle_support.gradle_executable(root), "-I", str(gradle_support.verify_init_script()),
                  "--console=plain"]
        targets, downstream = self._gradle_scope(root, scope)
        test_classes = sorted(scope.test_classes) if scope is not None else []
        filters = [argument for name in test_classes for argument in ("--tests", name)]
        # --rerun 只作用于紧挨在它前面的那个任务，所以逐个任务附上。它保证测试真的执行：Gradle
        # 会把输入没变的 test 判定为 up-to-date，或从构建缓存（Spring Security 开着
        # org.gradle.caching）直接还原报告，那样的"通过"不是这一次运行给出的凭据。
        # --rerun binds to the task right before it, so it is attached per task. It makes the tests
        # actually run: Gradle otherwise marks an unchanged test task up-to-date or restores its
        # reports from the build cache (Spring Security enables org.gradle.caching), and a pass
        # replayed that way is not a receipt from this run.
        test_tasks: list[str] = []
        pit_tasks: list[str] = []
        compile_tasks: list[str] = []
        source_sets: set[str] = set()
        projects = self._gradle_projects_for(root) if targets else None
        for project in targets or [None]:
            # 目标类所在 source set 对应的 Test 任务；不一定是 test（见 GradleProjects.test_tasks_for）。
            # The Test tasks for the target classes' source sets, not necessarily test
            # (see GradleProjects.test_tasks_for).
            tasks: list[gradle_support.TestTask] = []
            if projects is not None and project is not None:
                for name in test_classes:
                    for task in projects.test_tasks_for(project, name, fallback=False):
                        if task not in tasks:
                            tasks.append(task)
            for task in tasks or [gradle_support.DEFAULT_TEST_TASK]:
                test_tasks += [self._gradle_task(project, task.name), *filters, "--rerun"]
                classes_task = self._gradle_task(project, task.classes_task)
                if classes_task not in compile_tasks:
                    compile_tasks.append(classes_task)
                source_sets.add(task.source_set)
            pit_tasks += [self._gradle_task(project, "pitest"), "--rerun"]
        compile_tasks += [self._gradle_task(project, "testClasses") for project in downstream]
        pit_properties = [f"-PcloneDeMockerPitProjects={','.join(targets) or '*'}"]
        if test_classes:
            pit_properties.append(f"-PcloneDeMockerPitTests={','.join(test_classes)}")
        if source_sets - {"test"}:
            pit_properties.append(f"-PcloneDeMockerPitTestSourceSets={','.join(sorted(source_sets))}")
        return (
            [*prefix, *compile_tasks],
            [*prefix, *test_tasks],
            [*prefix, *pit_properties, *pit_tasks],
        )

    def _gradle_environment_problem(self, root: Path) -> str | None:
        """
        Gradle 构建本身配置不起来，或要求的 JDK toolchain 本机没有。Spring Security 7.1.1 要求
        JDK 25；缺了它，编译要跑到任务执行阶段才报错，还会被当成"候选代码编译失败"。
        The Gradle build cannot configure, or requests a JDK toolchain this host lacks. Spring
        Security 7.1.1 requests JDK 25; without it the failure only surfaces when tasks execute,
        where it would be read as the candidate failing to compile.
        """
        projects = self._gradle_projects_for(root)
        if not projects.ok:
            return ("Gradle could not configure this build / Gradle 无法完成该项目的配置阶段\n"
                    + projects.output)
        missing = projects.missing_toolchains()
        if missing:
            return (f"The build requests JDK toolchain(s) {', '.join(missing)}, which Gradle cannot find on this "
                    f"host; install them or list them in org.gradle.java.installations.paths / "
                    f"构建要求 JDK toolchain {', '.join(missing)}，本机 Gradle 找不到；请安装，或在 "
                    f"org.gradle.java.installations.paths 中登记")
        return None

    # 900 秒对小项目够用，但像 dubbo 这种上百模块的真实 reactor 项目，
    # 冷编译一次经常就要 20-30 分钟甚至更久，之前的 900 秒会在编译中途把进程杀掉，
    # 被误判成"编译失败"（其实只是没跑完）。
    # 900s is enough for small projects, but a real multi-module reactor project like
    # dubbo (100+ modules) often takes 20-30+ minutes for one cold compile; the previous
    # 900s cut the process off mid-build and got misread as a genuine compile failure
    # (it had simply not finished yet).
    TIMEOUT_SECONDS = 3600

    @staticmethod
    def _execute(command: list[str], cwd: Path, evidence: HarnessEvidence) -> HarnessStatus:
        evidence.commands.append(command)
        try:
            environment = gradle_support.english_environment() if gradle_support.is_gradle_command(command) else None
            completed = subprocess.run(command, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment,
                                       timeout=ProjectHarness.TIMEOUT_SECONDS, check=False)
            evidence.diagnostics.append(completed.stdout[-12000:])
            return HarnessStatus.PASSED if completed.returncode == 0 else HarnessStatus.FAILED
        except (OSError, subprocess.TimeoutExpired) as error:
            evidence.diagnostics.append(str(error))
            return HarnessStatus.UNAVAILABLE
