"""
Gradle 构建与 Maven 路径对齐所需的全部 Gradle 专属知识。
Everything Gradle-specific that the harness needs to give Gradle builds the same verification
the Maven path has: scoped compile/test/PIT, fresh test receipts, English diagnostics, and
downstream consumers of a module's test output.

Maven 靠命令行参数就能表达的东西（-pl -am、-Dtest、PIT 插件及其 JUnit 5 依赖），Gradle 大多
只能在构建脚本里表达。这里统一用 init script 注入，只作用于这次调用，绝不修改被测项目的
构建文件——对应 Maven 路径只改隔离副本 pom.xml 的做法，但更干净：连副本都不用改。
Most of what Maven expresses as command-line flags (-pl -am, -Dtest, the PIT plugin and its
JUnit 5 dependency) Gradle can only express in build scripts. It is injected here through an init
script that applies to the one invocation and never edits the subject's build files — the same
role as patching the isolated copy's pom.xml on the Maven side, but without touching even the copy.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from studio.long_paths import long_path

GRADLE_PITEST_PLUGIN_VERSION = "1.19.0"
PITEST_VERSION = "1.30.0"
PITEST_JUNIT5_PLUGIN_VERSION = "1.2.3"

BUILD_FILE_SUFFIXES = (".gradle", ".gradle.kts")
SETTINGS_FILE_NAMES = {"settings.gradle", "settings.gradle.kts"}
DISCOVERY_TIMEOUT_SECONDS = 900

# 实测：中文 Windows 上 Gradle 转发的 javac 报错是 GBK 乱码，和 Maven 路径当初的问题一样。
# Gradle 的编译器和测试进程是 daemon/worker 派生的 JVM，命令行 -D 只到 Gradle 客户端，
# 只有 JAVA_TOOL_OPTIONS 能一路传到真正跑 javac 的那个 JVM。
# Measured: on a Chinese Windows, javac errors forwarded by Gradle arrive as GBK mojibake, the
# same failure the Maven path once had. Gradle's compiler and test JVMs are daemon/worker
# processes, so a command-line -D reaches only the Gradle client; JAVA_TOOL_OPTIONS is what
# reaches the JVM actually running javac.
ENGLISH_JAVA_TOOL_OPTIONS = "-Duser.language=en -Duser.country=US"

_DISCOVERY_SCRIPT = """
gradle.projectsEvaluated { g ->
    def checked = [:]
    g.rootProject.allprojects { p ->
        println "CLONEDEMOCKER_PROJECT|" + p.path + "|" + p.projectDir.absolutePath
        p.configurations.each { c ->
            c.dependencies.withType(ProjectDependency).each { d ->
                if (d.targetConfiguration == 'tests') {
                    // getPath() 是 Gradle 8.11 才有的；更老的版本只有 dependencyProject。
                    // getPath() arrived in Gradle 8.11; older versions only have dependencyProject.
                    def target = d.metaClass.respondsTo(d, 'getPath') ? d.path : d.dependencyProject.path
                    println "CLONEDEMOCKER_TESTS_CONSUMER|" + p.path + "|" + target
                }
            }
        }
        // 哪个 Test 任务跑哪个 source set。测试不一定都在 src/test：Spring Security 的 saml2 把
        // OpenSAML 5 的测试放在 src/opensaml5Test，由单独的 opensaml5Test 任务执行。
        // Which Test task runs which source set. Tests are not always in src/test: Spring
        // Security's saml2 keeps its OpenSAML 5 tests in src/opensaml5Test, run by its own task.
        def sourceSets = p.extensions.findByType(SourceSetContainer)
        if (sourceSets != null) {
            p.tasks.withType(Test).each { t ->
                def classesDirs = t.testClassesDirs.files
                sourceSets.each { ss ->
                    if (ss.output.classesDirs.files.any { classesDirs.contains(it) }) {
                        ss.java.srcDirs.each { dir ->
                            println "CLONEDEMOCKER_TEST_TASK|" + p.path + "|" + t.name + "|" + ss.name + "|" +
                                    ss.classesTaskName + "|" + dir.absolutePath
                        }
                    }
                }
            }
        }
        def java = p.extensions.findByType(JavaPluginExtension)
        def version = java?.toolchain?.languageVersion?.getOrNull()
        if (version != null && !checked.containsKey(version.toString())) {
            def available
            try {
                p.extensions.getByType(JavaToolchainService).launcherFor(java.toolchain).get()
                available = 'OK'
            } catch (Exception ignored) {
                available = 'MISSING'
            }
            checked[version.toString()] = available
            println "CLONEDEMOCKER_TOOLCHAIN|" + version + "|" + available
        }
    }
}
"""

_VERIFY_SCRIPT = f"""
initscript {{
    repositories {{ gradlePluginPortal() }}
    dependencies {{ classpath 'info.solidsoft.gradle.pitest:gradle-pitest-plugin:{GRADLE_PITEST_PLUGIN_VERSION}' }}
}}
def cloneDeMockerList = {{ String name -> (gradle.startParameter.projectProperties[name] ?: '').split(',').findAll {{ it }} }}
def pitProjects = cloneDeMockerList('cloneDeMockerPitProjects') as Set
def pitTests = cloneDeMockerList('cloneDeMockerPitTests')
def pitTestSourceSets = cloneDeMockerList('cloneDeMockerPitTestSourceSets')
allprojects {{ p ->
    // 等价于 Maven 的 -Dsurefire.failIfNoSpecifiedTests=false：同一组 --tests 过滤会套到每个
    // 范围内模块上，某个模块里没有匹配的类不应该让整个构建失败。目标类是否真的执行过，
    // 由 harness 用新鲜的测试报告单独核对。
    // The Gradle counterpart of -Dsurefire.failIfNoSpecifiedTests=false: the same --tests filter
    // is applied to every module in scope, and a module holding none of the classes must not fail
    // the build. Whether the target classes really ran is checked separately from fresh reports.
    p.tasks.withType(Test).configureEach {{ filter.failOnNoMatchingTests = false }}
    if (pitProjects.contains('*') || pitProjects.contains(p.path)) {{
        p.plugins.withId('java') {{
            if (p.plugins.hasPlugin('info.solidsoft.pitest')) return
            p.apply plugin: info.solidsoft.gradle.pitest.PitestPlugin
            p.pitest {{
                pitestVersion = '{PITEST_VERSION}'
                junit5PluginVersion = '{PITEST_JUNIT5_PLUGIN_VERSION}'
                if (pitTests) targetTests = pitTests
                // 插件默认只看 test 这个 source set；目标测试在别的 source set 时要显式给出。
                // The plugin only looks at the test source set by default; others must be named.
                if (pitTestSourceSets) testSourceSets = pitTestSourceSets.collect {{ p.sourceSets.getByName(it) }}
                outputFormats = ['XML']
                timestampedReports = false
                failWhenNoMutations = false
                // 与 Maven 输出位置 target/pit-reports 同名，变异报告收集逻辑不必区分构建工具。
                // Named like Maven's target/pit-reports so mutation collection need not care
                // which build tool produced the report.
                reportDir = p.layout.buildDirectory.dir('pit-reports')
            }}
        }}
    }}
}}
gradle.projectsEvaluated {{
    allprojects {{ p ->
        p.tasks.withType(Test).configureEach {{
            reports.junitXml.required = true
        }}
    }}
}}
"""


def is_gradle_build(root: Path) -> bool:
    return any((root / name).is_file() for name in (
        "gradlew", "gradlew.bat", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"))


def is_module_directory(directory: Path) -> bool:
    """
    静态判断一个目录是否像 Gradle/Maven 子项目。不能只认 build.gradle：Spring Security 的子项目
    构建文件叫 spring-security-core.gradle；Spring Integration 子项目没有独立 build 文件，
    全由根配置统一注册（只有 src/ 目录）。这只是给 BuildScope 提供候选目录，真正的 Gradle
    项目路径由 discover_projects 按实际构建解析。
    Statically, whether a directory looks like a Gradle subproject. build.gradle alone is not
    enough: Spring Security names its build files spring-security-core.gradle and registers them
    from settings.gradle by file name; Spring Integration has no subproject build files at all
    (only a src/ directory). This only proposes a directory for BuildScope; the real project
    path comes from discover_projects, which asks the build itself.
    """
    try:
        if (directory / "src").is_dir() or (directory / "pom.xml").is_file():
            return True
        return any(child.is_file() and child.name.endswith(BUILD_FILE_SUFFIXES)
                   and child.name not in SETTINGS_FILE_NAMES for child in directory.iterdir())
    except OSError:
        return False


def gradle_executable(root: Path) -> str:
    if os.name == "nt" and (root / "gradlew.bat").is_file():
        return str(root / "gradlew.bat")
    if (root / "gradlew").is_file():
        return str(root / "gradlew")
    return "gradle.bat" if os.name == "nt" else "gradle"


def is_gradle_command(command: list[str]) -> bool:
    return bool(command) and Path(command[0].replace("\\", "/")).name.lower() in {"gradlew", "gradlew.bat", "gradle", "gradle.bat"}


def english_environment() -> dict[str, str]:
    environment = dict(os.environ)
    existing = environment.get("JAVA_TOOL_OPTIONS", "")
    if "-Duser.language=" not in existing:
        environment["JAVA_TOOL_OPTIONS"] = f"{existing} {ENGLISH_JAVA_TOOL_OPTIONS}".strip()
    return environment


def _script_path(name: str, content: str) -> Path:
    """init script 写到系统临时目录：它不属于被测项目，也不该出现在任何副本里。
    The init script lives in the system temp directory: it is not part of the subject project
    and should not appear in any copy of it."""
    path = Path(tempfile.gettempdir()) / "clonedemocker" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8", newline="\n")
    return path


def verify_init_script() -> Path:
    return _script_path(f"verify-pitest-{GRADLE_PITEST_PLUGIN_VERSION}.gradle", _VERIFY_SCRIPT)


@dataclass(frozen=True)
class TestTask:
    name: str
    source_set: str
    classes_task: str
    source_dir: str


DEFAULT_TEST_TASK = TestTask("test", "test", "testClasses", "")


@dataclass
class GradleProjects:
    """Gradle 自己报告的项目结构 / The project structure as Gradle itself reports it."""

    root: Path
    # 相对根目录的 posix 路径 -> Gradle 项目路径，例如 "core" -> ":spring-security-core"
    # posix path relative to the root -> Gradle project path, e.g. "core" -> ":spring-security-core"
    directories: dict[str, str] = field(default_factory=dict)
    # 被依赖的项目 -> 依赖它 tests 输出的项目（Maven test-jar 消费者的对应物）
    # project -> projects consuming its test output (the counterpart of Maven test-jar consumers)
    test_output_consumers: dict[str, set[str]] = field(default_factory=dict)
    # 项目 -> 该项目的 Test 任务：(任务名, source set 名, 编译该 source set 的任务名, 源码目录)
    # project -> its Test tasks: (task name, source set name, classes task name, source directory)
    test_tasks: dict[str, list["TestTask"]] = field(default_factory=dict)
    # 要求的 toolchain 语言版本 -> 本机是否可用
    # requested toolchain language version -> whether this host can provide it
    toolchains: dict[str, bool] = field(default_factory=dict)
    ok: bool = True
    # Gradle 根本没能启动（找不到可执行文件或超时），区别于启动了但配置失败。
    # Gradle never ran (no executable, or a timeout), as distinct from running and failing.
    launched: bool = True
    output: str = ""

    def project_for(self, module: str) -> str | None:
        """模块目录对应的 Gradle 项目；目录本身不是项目时取最近的祖先项目。
        The Gradle project for a module directory; the nearest ancestor project when the directory
        itself is not one."""
        parts = [part for part in module.replace("\\", "/").strip("/").split("/") if part and part != "."]
        while parts:
            project = self.directories.get("/".join(parts))
            if project is not None:
                return project
            parts.pop()
        return self.directories.get("")

    def test_tasks_for(self, project: str, test_class: str, fallback: bool = True) -> list["TestTask"]:
        """
        会执行这个测试类的 Test 任务：源码文件落在它所跑的 source set 目录里。找不到时（Gradle 没报告
        这个项目的任务，或类不在任何已知目录）退回约定的 test 任务。
        The Test tasks that execute this class: those whose source set directory holds its source
        file. Falls back to the conventional test task when Gradle reported none for the project
        or the class is in no known directory.
        """
        relative = test_class.replace(".", "/") + ".java"
        found = [task for task in self.test_tasks.get(project, []) if long_path(Path(task.source_dir) / relative).is_file()]
        return found or ([DEFAULT_TEST_TASK] if fallback else [])

    def missing_toolchains(self) -> list[str]:
        return sorted(version for version, available in self.toolchains.items() if not available)


def discover_projects(root: Path, timeout: int = DISCOVERY_TIMEOUT_SECONDS) -> GradleProjects:
    """
    让 Gradle 报告项目目录、tests 输出的消费关系和 toolchain 可用性。只做配置阶段、不编译，
    daemon 热的时候秒级返回。
    Asks Gradle for project directories, test-output consumers and toolchain availability. It
    runs the configuration phase only, nothing is compiled, and returns in seconds once the daemon
    is warm.
    """
    root = root.resolve()
    result = GradleProjects(root=root)
    script = _script_path("discover-projects.gradle", _DISCOVERY_SCRIPT)
    command = [gradle_executable(root), "-q", "-I", str(script), "help", "--console=plain"]
    try:
        completed = subprocess.run(command, cwd=root, text=True, encoding="utf-8", errors="replace",
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env=english_environment(), timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        result.ok = False
        result.launched = False
        result.output = str(error)
        return result
    result.ok = completed.returncode == 0
    result.output = completed.stdout[-6000:]
    for line in completed.stdout.splitlines():
        fields = line.strip().split("|")
        if fields[0] == "CLONEDEMOCKER_PROJECT" and len(fields) == 3:
            try:
                relative = Path(fields[2]).resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            result.directories["" if relative == "." else relative] = fields[1]
        elif fields[0] == "CLONEDEMOCKER_TESTS_CONSUMER" and len(fields) == 3:
            result.test_output_consumers.setdefault(fields[2], set()).add(fields[1])
        elif fields[0] == "CLONEDEMOCKER_TEST_TASK" and len(fields) == 6:
            task = TestTask(fields[2], fields[3], fields[4], fields[5])
            tasks = result.test_tasks.setdefault(fields[1], [])
            if task not in tasks:
                tasks.append(task)
        elif fields[0] == "CLONEDEMOCKER_TOOLCHAIN" and len(fields) == 3:
            result.toolchains[fields[1]] = fields[2] == "OK"
    return result
