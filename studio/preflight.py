from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from studio import gradle_support


# 往上找 reactor 根时的最大层数，避免在极深目录或符号链接环里空转。
# Cap on how far up we look for a reactor root, so a very deep tree or a
# symlink loop can't spin forever.
MAX_ANCESTOR_DEPTH = 24

# `mvn -N validate` 只读聚合根自己那一份 pom，不遍历 reactor，正常几秒就返回；
# 给 180 秒是留给首次解析插件要联网下载的情况。
# `mvn -N validate` reads only the aggregator's own pom without walking the
# reactor, so it normally returns in seconds; 180s is headroom for a first run
# that has to download plugin descriptors.
VALIDATE_TIMEOUT_SECONDS = 180


def _local_name(element: ET.Element) -> str:
    """去掉 XML 命名空间前缀。有的 pom 带 POM/4.0.0 命名空间，有的不带，
    按 local name 匹配可以同时吃下两种。
    Strips the XML namespace. Some poms carry the POM/4.0.0 namespace and some
    don't; matching on local name handles both."""
    tag = element.tag
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent:
        if _local_name(child) == name:
            return child
    return None


def _parse_pom(pom_path: Path) -> ET.Element | None:
    try:
        return ET.parse(pom_path).getroot()
    except (OSError, ET.ParseError):
        return None


def _declared_modules(pom_path: Path) -> list[str]:
    root = _parse_pom(pom_path)
    if root is None:
        return []
    modules = _find_child(root, "modules")
    if modules is None:
        return []
    return [
        (child.text or "").strip()
        for child in modules
        if _local_name(child) == "module" and (child.text or "").strip()
    ]


def _declared_parent(pom_path: Path) -> dict[str, str] | None:
    root = _parse_pom(pom_path)
    if root is None:
        return None
    parent = _find_child(root, "parent")
    if parent is None:
        return None
    result: dict[str, str] = {}
    for name in ("groupId", "artifactId", "version", "relativePath"):
        child = _find_child(parent, name)
        if child is not None:
            result[name] = (child.text or "").strip()
    return result


def _declaring_parent(directory: Path) -> Path | None:
    """
    找出把 directory 列为自己 <module> 的那个直接上级目录。

    只看直接上级，不做全盘搜索：Maven 允许 <module>../foo</module> 这种写法，
    但真实项目里 module 几乎总是直接子目录，只查一层既没有误报也足够快。
    Finds the immediate parent directory that lists `directory` as one of its
    <module> entries. Only the immediate parent is checked: Maven does allow
    <module>../foo</module>, but in real projects modules are nearly always
    direct subdirectories, so a single-level check is both fast and free of
    false positives.
    """
    parent = directory.parent
    if parent == directory:
        return None
    pom = parent / "pom.xml"
    if not pom.is_file():
        return None
    for module in _declared_modules(pom):
        candidate = (parent / module).resolve()
        # <module> 可以直接指向一个 pom 文件，不一定是目录。
        # A <module> may point straight at a pom file rather than a directory.
        if candidate.suffix == ".xml":
            candidate = candidate.parent
        if candidate == directory:
            return parent
    return None


def reactor_root(project_root: Path) -> Path | None:
    """
    一路往上找到最顶层的 reactor 根；project_root 自己就是根时返回 None。

    逐层往上是必要的：dubbo 里 dubbo-remoting-netty4 的上级是 dubbo-remoting，
    再上一级才是真正的 reactor 根 dubbo-3.3.6。
    Walks up to the topmost reactor root, or None when project_root already is
    one. Walking level by level matters: in dubbo, dubbo-remoting-netty4's
    parent is dubbo-remoting, and only one level above that is the real reactor
    root, dubbo-3.3.6.
    """
    found: Path | None = None
    current = project_root.resolve()
    for _ in range(MAX_ANCESTOR_DEPTH):
        parent = _declaring_parent(current)
        if parent is None:
            break
        found = parent
        current = parent
    return found


def unresolvable_parent_version(project_root: Path) -> str | None:
    """
    这个目录的 pom 声明的 <parent> 版本里是否含有未展开的属性占位符。

    dubbo 这类项目用 CI-friendly versioning，子模块写的是
    <version>${revision}</version>。这个占位符只能靠 parent.relativePath 找到
    父 pom 才能展开；一旦这个目录被当成独立项目根（或者被拷贝到别处），相对路径
    就失效，Maven 会退回去从仓库找 `${revision}` 这个字面版本，必然失败。
    Whether this directory's pom declares a <parent> whose version still holds
    an unexpanded property placeholder. Projects like dubbo use CI-friendly
    versioning, so submodules declare <version>${revision}</version>. That
    placeholder can only be expanded by locating the parent pom via
    parent.relativePath; as soon as this directory is treated as a standalone
    project root (or copied elsewhere) the relative path breaks and Maven falls
    back to resolving the literal `${revision}` from a repository, which always
    fails.
    """
    pom = project_root / "pom.xml"
    if not pom.is_file():
        return None
    parent = _declared_parent(pom)
    if parent is None:
        return None
    version = parent.get("version", "")
    return version if "${" in version else None


def gradle_root(project_root: Path) -> Path | None:
    """
    Gradle 版的 reactor_root：目录自己没有 settings 文件、而某个上级有，这个上级才是构建根。
    Gradle 会自己往上找 settings 文件，但我们只复制所选目录做隔离验证，副本里就没有它和
    buildSrc 了。
    The Gradle form of reactor_root: when the directory has no settings file but an ancestor
    does, that ancestor is the build root. Gradle searches upward for settings on its own, but
    verification copies only the chosen directory, and the copy would then lack it and buildSrc.
    """
    settings = ("settings.gradle", "settings.gradle.kts")
    current = project_root.resolve()
    if any((current / name).is_file() for name in settings):
        return None
    for _ in range(MAX_ANCESTOR_DEPTH):
        if current.parent == current:
            return None
        current = current.parent
        if any((current / name).is_file() for name in settings):
            return current
    return None


def gradle_probe(project_root: Path) -> dict[str, Any]:
    """
    第 1 级探针的 Gradle 版：只跑配置阶段，顺带检查构建要求的 JDK toolchain 本机是否具备。
    Spring Security 7.1.1 要求 JDK 25，缺了它要到编译任务执行时才失败。
    Tier-1 probe for Gradle: runs the configuration phase only, and checks that the JDK
    toolchains the build requests exist on this host. Spring Security 7.1.1 requests JDK 25,
    and without it the failure only appears once compile tasks execute.
    """
    projects = gradle_support.discover_projects(project_root, timeout=VALIDATE_TIMEOUT_SECONDS * 5)
    command = [gradle_support.gradle_executable(project_root), "help"]
    status = "UNAVAILABLE" if not projects.launched else ("OK" if projects.ok else "FAILED")
    return {"status": status, "command": command, "output": projects.output,
            "missingToolchains": projects.missing_toolchains(), "projectCount": len(projects.directories)}


def build_system(project_root: Path) -> str:
    if (project_root / "pom.xml").is_file():
        return "maven"
    if any((project_root / name).is_file()
           for name in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")):
        return "gradle"
    return "none"


def _maven_executable(root: Path) -> str:
    """跟 harness.ProjectHarness._build_commands 保持一致的可执行文件选择。
    Mirrors the executable selection in harness.ProjectHarness._build_commands."""
    if os.name == "nt" and (root / "mvnw.cmd").is_file():
        return str(root / "mvnw.cmd")
    if (root / "mvnw").is_file():
        return str(root / "mvnw")
    return "mvn.cmd" if os.name == "nt" else "mvn"


def maven_validate(project_root: Path, maven_repo_local: str | None = None) -> dict[str, Any]:
    """
    第 1 级探针：`mvn -N validate`。

    -N（非递归）只校验这一个 pom 自己，不碰 reactor 里其他模块，所以哪怕是
    dubbo 这种 112 模块的项目也是秒级返回。它正好覆盖我们真正踩过的那一类失败
    ——parent POM 解析不了、pom 语法错、属性展开不了——而不需要等一次几十分钟
    的冷编译才发现。
    Tier-1 probe: `mvn -N validate`. The -N (non-recursive) flag validates just
    this one pom without touching the rest of the reactor, so it returns in
    seconds even for a 112-module project like dubbo. It covers exactly the
    class of failure actually hit in practice — unresolvable parent POM, broken
    pom syntax, unexpandable properties — without waiting out a cold compile of
    tens of minutes to find out.
    """
    command = [_maven_executable(project_root)]
    if maven_repo_local:
        command.append(f"-Dmaven.repo.local={maven_repo_local}")
    command += ["-N", "validate"]
    try:
        completed = subprocess.run(
            command, cwd=project_root, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=VALIDATE_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        # 找不到 mvn，或者超时——这是"本机环境不具备条件"，不是"这个项目有问题"。
        # No mvn on PATH, or a timeout — that's "this host isn't equipped", not
        # "this project is broken".
        return {"status": "UNAVAILABLE", "command": command, "output": str(error)}
    return {
        "status": "OK" if completed.returncode == 0 else "FAILED",
        "command": command,
        "output": completed.stdout[-6000:],
    }


def inspect_project(project_root: str | Path, run_build_probe: bool = True,
                    maven_repo_local: str | None = None) -> dict[str, Any]:
    """
    选定项目目录时立刻跑的预检，在用户投入任何时间和 token 之前给出判断。

    分两级：第 0 级纯静态（解析 pom，毫秒级），第 1 级跑 `mvn -N validate`
    （秒级）。检测、选 MCI、调模型都不需要项目能构建，只有 Harness 验证需要——
    所以把环境判定放在这里，比等到流水线最后一步再失败要早得多，代价也接近零。
    Preflight run the moment a project directory is chosen, so the verdict
    lands before the user invests any time or tokens. Two tiers: tier 0 is
    purely static (pom parsing, milliseconds), tier 1 runs `mvn -N validate`
    (seconds). Detection, MCI selection and the model call need nothing built —
    only harness validation does — so deciding the environment here is far
    earlier, and far cheaper, than failing at the last step of the pipeline.
    """
    root = Path(project_root).expanduser().resolve()
    findings: list[dict[str, Any]] = []
    if not root.is_dir():
        return {
            "projectRoot": str(root),
            "buildSystem": "none",
            "severity": "BLOCKER",
            "findings": [{
                "code": "NOT_A_DIRECTORY",
                "severity": "BLOCKER",
                "message": f"Project directory does not exist / 项目目录不存在: {root}",
            }],
            "buildProbe": None,
            "canRunLocally": False,
            "sagRecommended": False,
        }

    system = build_system(root)
    suggested = reactor_root(root) if system == "maven" else gradle_root(root)
    placeholder = unresolvable_parent_version(root)

    if system == "none":
        findings.append({
            "code": "NO_BUILD_SYSTEM",
            "severity": "BLOCKER",
            "message": "No Maven or Gradle build found in this directory / "
                       "此目录下没有 Maven 或 Gradle 构建文件",
        })
    if suggested is not None and placeholder:
        findings.append({
            "code": "SUBMODULE_WITH_PLACEHOLDER_PARENT",
            "severity": "BLOCKER",
            "suggestedRoot": str(suggested),
            "message": (
                f"This is a reactor submodule whose parent version is still a placeholder "
                f"({placeholder}), which only resolves through the parent pom next to it. "
                f"Used as a standalone root it cannot build. Use the reactor root instead: {suggested} / "
                f"这是一个 reactor 子模块，它的 parent 版本仍是占位符（{placeholder}），"
                f"只能通过相邻的父 pom 展开。单独作为项目根无法构建，请改用 reactor 根：{suggested}"
            ),
        })
    elif suggested is not None:
        findings.append({
            "code": "REACTOR_SUBMODULE",
            "severity": "WARNING",
            "suggestedRoot": str(suggested),
            "message": (
                f"This directory is a submodule of a larger reactor; analysis and verification will "
                f"cover only part of the project. Reactor root: {suggested} / "
                f"此目录是更大的 reactor 中的一个子模块，检测与验证只会覆盖项目的一部分。"
                f"reactor 根：{suggested}"
            ),
        })

    blocked = any(item["severity"] == "BLOCKER" for item in findings)
    probe: dict[str, Any] | None = None
    if run_build_probe and system == "maven" and not blocked:
        probe = maven_validate(root, maven_repo_local)
        if probe["status"] == "FAILED":
            findings.append({
                "code": "MAVEN_VALIDATE_FAILED",
                "severity": "BLOCKER",
                "message": "`mvn -N validate` failed, so this project cannot be built on this host as configured / "
                           "`mvn -N validate` 失败，该项目在本机当前配置下无法构建",
            })
        elif probe["status"] == "UNAVAILABLE":
            findings.append({
                "code": "MAVEN_UNAVAILABLE",
                "severity": "BLOCKER",
                "message": "Maven could not be run on this host / 本机无法运行 Maven",
            })
    elif run_build_probe and system == "gradle" and not blocked:
        probe = gradle_probe(root)
        if probe["status"] == "FAILED":
            findings.append({
                "code": "GRADLE_CONFIGURE_FAILED",
                "severity": "BLOCKER",
                "message": "Gradle could not configure this build, so it cannot be built on this host as configured / "
                           "Gradle 无法完成配置阶段，该项目在本机当前配置下无法构建",
            })
        elif probe["status"] == "UNAVAILABLE":
            findings.append({
                "code": "GRADLE_UNAVAILABLE",
                "severity": "BLOCKER",
                "message": "Gradle could not be run on this host / 本机无法运行 Gradle",
            })
        if probe["missingToolchains"]:
            versions = ", ".join(probe["missingToolchains"])
            findings.append({
                "code": "GRADLE_TOOLCHAIN_MISSING",
                "severity": "BLOCKER",
                "message": (f"The build requests JDK toolchain(s) {versions}, which Gradle cannot find on this host. "
                            f"Install them or list them in org.gradle.java.installations.paths / "
                            f"构建要求 JDK toolchain {versions}，本机 Gradle 找不到。请安装，或在 "
                            f"org.gradle.java.installations.paths 中登记"),
            })
    elif run_build_probe and system in {"maven", "gradle"} and blocked:
        probe = {"status": "SKIPPED", "command": [],
                 "output": "Skipped because a blocking problem was already found / "
                           "已发现阻断性问题，跳过构建探针"}

    severity = "OK"
    if any(item["severity"] == "BLOCKER" for item in findings):
        severity = "BLOCKER"
    elif findings:
        severity = "WARNING"

    can_run_locally = severity != "BLOCKER"
    # 只有"环境配不起来"才值得建议上 SAG。选错目录这类问题改个路径就好了，
    # 拉一个容器反而是舍近求远。
    # SAG is only worth suggesting when the environment itself can't be made to
    # work. Picking the wrong directory is fixed by picking another one; spinning
    # up a container for that would be the long way round.
    environment_codes = {"MAVEN_VALIDATE_FAILED", "MAVEN_UNAVAILABLE", "NO_BUILD_SYSTEM",
                         "GRADLE_CONFIGURE_FAILED", "GRADLE_UNAVAILABLE", "GRADLE_TOOLCHAIN_MISSING"}
    sag_recommended = any(
        item["severity"] == "BLOCKER" and item["code"] in environment_codes for item in findings
    )

    return {
        "projectRoot": str(root),
        "buildSystem": system,
        "severity": severity,
        "findings": findings,
        "buildProbe": probe,
        "canRunLocally": can_run_locally,
        "sagRecommended": sag_recommended,
    }
