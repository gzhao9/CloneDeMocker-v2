from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import os
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Any


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
        }


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

    def _maven_repo_args(self) -> list[str]:
        return [f"-Dmaven.repo.local={self.maven_repo_local}"] if self.maven_repo_local else []

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

    def validate(self, project_root: Path, run_pit: bool = False) -> HarnessEvidence:
        project_root = _strip_long_path_prefix(project_root)
        evidence = HarnessEvidence()
        build = self._build_commands(project_root)
        if build is None:
            evidence.compile_status = HarnessStatus.UNAVAILABLE
            evidence.test_status = HarnessStatus.UNAVAILABLE
            evidence.pit_status = HarnessStatus.UNAVAILABLE
            evidence.diagnostics.append("No supported Maven or Gradle build was found / 未找到 Maven 或 Gradle 构建")
            return evidence

        compile_command, test_command, pit_command = build
        evidence.compile_status = self._execute(compile_command, project_root, evidence)
        if evidence.compile_status == HarnessStatus.PASSED:
            evidence.test_status = self._execute(test_command, project_root, evidence)
            evidence.test_results = self._collect_test_identities(project_root)
        else:
            evidence.test_status = HarnessStatus.NOT_RUN
        if run_pit and evidence.test_status == HarnessStatus.PASSED and pit_command:
            # 减去一点余量，避免文件系统 mtime 精度和时钟误差把本次刚生成的报告漏掉。
            # Subtract a small buffer so filesystem mtime resolution or clock skew
            # doesn't cause the report just generated by this run to be missed.
            pit_started_at = time.time() - 1
            evidence.pit_status = self._execute(pit_command, project_root, evidence)
            mutation = self._collect_mutation_summary(project_root, pit_started_at)
            if mutation is not None:
                evidence.mutation_total = mutation["total"]
                evidence.mutation_score = mutation["mutationScore"]
                evidence.mutation_counts = mutation["counts"]
                evidence.mutants = mutation["mutants"]
        else:
            evidence.pit_status = HarnessStatus.NOT_RUN
        return evidence

    @staticmethod
    def _collect_test_identities(root: Path) -> dict[str, str]:
        identities: dict[str, str] = {}
        reports = list(root.rglob("surefire-reports/TEST-*.xml")) + list(root.rglob("test-results/test/TEST-*.xml"))
        for report in reports:
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
        """
        reports = [
            path for path in root.rglob("pit-reports/*/mutations.xml")
            if path.stat().st_mtime >= since
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

    def _build_commands(self, root: Path) -> tuple[list[str], list[str], list[str] | None] | None:
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
            return (
                [executable, *repo_args, *style_check_skip_args, "-DskipTests", "test-compile"],
                [executable, *repo_args, *style_check_skip_args, "test"],
                [executable, *repo_args, *style_check_skip_args,
                 "org.pitest:pitest-maven:mutationCoverage", "-DoutputFormats=XML"],
            )
        if any((root / name).is_file() for name in ("gradlew", "gradlew.bat", "build.gradle", "build.gradle.kts")):
            executable = str(root / "gradlew.bat") if os.name == "nt" and (root / "gradlew.bat").is_file() else (
                str(root / "gradlew") if (root / "gradlew").is_file() else ("gradle.bat" if os.name == "nt" else "gradle")
            )
            return ([executable, "testClasses"], [executable, "test"], [executable, "pitest"])
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
            completed = subprocess.run(command, cwd=cwd, text=True, encoding="utf-8", errors="replace",
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       timeout=ProjectHarness.TIMEOUT_SECONDS, check=False)
            evidence.diagnostics.append(completed.stdout[-12000:])
            return HarnessStatus.PASSED if completed.returncode == 0 else HarnessStatus.FAILED
        except (OSError, subprocess.TimeoutExpired) as error:
            evidence.diagnostics.append(str(error))
            return HarnessStatus.UNAVAILABLE
