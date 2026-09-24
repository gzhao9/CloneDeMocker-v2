"""Check whether this checkout can run the UI, persist data, and run PIT."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FAILURES = 0


def report(level: str, name: str, detail: str, fix: str = "") -> None:
    global FAILURES
    print(f"[{level}] {name}: {detail}")
    if fix:
        print(f"       Fix: {fix}")
    if level == "FAIL":
        FAILURES += 1


def run(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, cwd=REPOSITORY_ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None


def command_check(name: str, command: str, arguments: list[str], fix: str,
                  version_pattern: str | None = None, minimum: tuple[int, ...] | None = None) -> bool:
    executable = shutil.which(command)
    if not executable:
        report("FAIL", name, f"{command} was not found", fix)
        return False
    completed = run([executable, *arguments])
    output = ((completed.stdout + completed.stderr).strip() if completed else "")
    first_line = output.splitlines()[0] if output else executable
    if completed is None or completed.returncode != 0:
        report("FAIL", name, first_line or "version command failed", fix)
        return False
    if version_pattern and minimum:
        match = re.search(version_pattern, output)
        version = tuple(int(part) for part in match.groups()) if match else ()
        if not version or version < minimum:
            report("FAIL", name, f"found {first_line}; need {'.'.join(map(str, minimum))}+", fix)
            return False
    report("PASS", name, first_line)
    return True


def check_python() -> None:
    version = sys.version_info
    if version[:2] == (3, 11):
        report("PASS", "Python", f"{version.major}.{version.minor}.{version.micro}")
    else:
        report("FAIL", "Python", f"running {version.major}.{version.minor}.{version.micro}; need Python 3.11",
               "Run setup.cmd (Windows) or bash setup.sh (macOS/Linux).")
    try:
        import openai  # noqa: F401
        import tree_sitter  # noqa: F401
        import tree_sitter_java  # noqa: F401
    except ImportError as error:
        report("FAIL", "Python dependencies", str(error), "Run setup.cmd or bash setup.sh.")
    else:
        report("PASS", "Python dependencies", "core UI imports are installed")


def check_repository() -> None:
    required = ["pyproject.toml", "uv.lock", ".python-version",
                "DETECTION/CloneDeMocker_Detection_tool.jar"]
    missing = [name for name in required if not (REPOSITORY_ROOT / name).is_file()]
    if missing:
        report("FAIL", "Repository files", f"missing {', '.join(missing)}",
               "Run git pull and git lfs pull from the repository root.")
    else:
        report("PASS", "Repository files", "dependency lock and detector JAR are present")

    pointer_files = []
    completed = run(["git", "lfs", "ls-files", "-n"])
    if completed and completed.returncode == 0:
        for relative in completed.stdout.splitlines():
            path = REPOSITORY_ROOT / relative.strip()
            if path.is_file() and path.read_bytes()[:42].startswith(b"version https://git-lfs.github.com/spec/v1"):
                pointer_files.append(relative.strip())
    if pointer_files:
        report("FAIL", "Git LFS content", f"{len(pointer_files)} file(s) are still pointer files",
               "Run git lfs pull.")
    else:
        report("PASS", "Git LFS content", "tracked LFS files are materialized")

    data_dir = REPOSITORY_ROOT / "data"
    try:
        data_dir.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=data_dir, prefix=".write-check-", delete=True):
            pass
    except OSError as error:
        report("FAIL", "data/ write access", str(error), "Grant write permission to the checkout.")
    else:
        report("PASS", "data/ write access", str(data_dir))


def check_target(project_root: Path | None, require_pit: bool) -> None:
    if project_root is None:
        level = "FAIL" if require_pit else "WARN"
        report(level, "Target project", "no --project-root was supplied",
               "Pass --project-root <Java project> for build and PIT checks.")
        return
    if not project_root.is_dir():
        report("FAIL", "Target project", f"directory does not exist: {project_root}")
        return
    report("PASS", "Target project", str(project_root.resolve()))
    if any(ord(character) > 127 for character in str(project_root.resolve())):
        report("WARN", "Target path", "contains non-ASCII characters; Java tools may fail on Windows",
               "Prefer an ASCII-only project path.")

    is_maven = (project_root / "pom.xml").is_file()
    is_gradle = any((project_root / name).is_file() for name in
                    ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"))
    if is_maven:
        wrapper = (project_root / "mvnw").is_file() or (project_root / "mvnw.cmd").is_file()
        if wrapper or shutil.which("mvn"):
            report("PASS", "Target build tool", "Maven Wrapper" if wrapper else "system Maven")
        else:
            report("FAIL", "Target build tool", "Maven project without mvnw or system mvn",
                   "Install Maven 3.9+ or add the Maven Wrapper.")
    elif is_gradle:
        wrapper = (project_root / "gradlew").is_file() or (project_root / "gradlew.bat").is_file()
        if wrapper or shutil.which("gradle"):
            report("PASS", "Target build tool", "Gradle Wrapper" if wrapper else "system Gradle")
        else:
            report("FAIL", "Target build tool", "Gradle project without gradlew or system gradle",
                   "Restore the Gradle Wrapper or install the required Gradle version.")
    else:
        report("FAIL", "Target build tool", "no Maven or Gradle root build file was found",
               "Pass the actual Java project root.")


def setup_directories(data_project: str) -> list[Path]:
    root = REPOSITORY_ROOT / "data" / data_project / "refactoring"
    return [path for path in root.iterdir() if path.is_dir()] if root.is_dir() else []


def check_saved_data(data_project: str | None, require_pit: bool, verify_pit_output: bool) -> None:
    if not data_project:
        if require_pit or verify_pit_output:
            report("FAIL", "Canonical data project", "--data-project was not supplied",
                   "Use the directory name under data/, for example --data-project cloudstack-4.23.0.0.")
        else:
            report("WARN", "Canonical data project", "not checked; pass --data-project <name>")
        return
    project_dir = REPOSITORY_ROOT / "data" / data_project
    detection = project_dir / "detection.json"
    if detection.is_file():
        report("PASS", "Detection data", str(detection.relative_to(REPOSITORY_ROOT)))
    else:
        report("FAIL", "Detection data", "data/<project>/detection.json is missing",
               "Run detection in the UI; detection is saved automatically.")
    setups = setup_directories(data_project)
    valid_setups = [path for path in setups if (path / "refactoring-results.json").is_file()]
    if valid_setups:
        report("PASS", "Refactoring data", f"{len(valid_setups)} setup(s) contain refactoring-results.json")
    else:
        report("FAIL", "Refactoring data", "no saved refactoring result was found",
               "Run a refactoring batch; completed MCIs are saved to data/ automatically.")
        return
    if require_pit:
        diff_count = sum(len(list((path / "diffs").glob("*.diff"))) for path in valid_setups)
        if diff_count:
            report("PASS", "PIT replay input", f"{diff_count} saved diff(s) are available")
        else:
            report("FAIL", "PIT replay input", "no saved diffs were found",
                   "Export or rerun refactoring before PIT replay.")
    if verify_pit_output:
        json_count = sum(len(list((path / "pit").glob("*.json"))) for path in valid_setups)
        xml_count = sum(len(list((path / "pit").glob("**/mutations.xml"))) for path in valid_setups)
        if json_count and xml_count:
            report("PASS", "PIT output in data/", f"{json_count} evidence JSON file(s), {xml_count} mutations.xml file(s)")
        else:
            report("FAIL", "PIT output in data/", f"found {json_count} evidence JSON and {xml_count} mutations.xml files",
                   f"Run: uv run --locked --python 3.11 python validation/pit_replay.py pit {data_project}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, help="Java project to scan/build")
    parser.add_argument("--data-project", help="directory name under data/")
    parser.add_argument("--require-pit", action="store_true", help="require PIT prerequisites and saved diffs")
    parser.add_argument("--verify-pit-output", action="store_true", help="require PIT JSON and mutations.xml under data/")
    args = parser.parse_args()

    print("CloneDeMocker environment and data check")
    print(f"Repository: {REPOSITORY_ROOT}")
    command_check("Git", "git", ["--version"], "Install Git.")
    command_check("Git LFS", "git", ["lfs", "version"], "Install Git LFS and run git lfs install.")
    command_check("uv", "uv", ["--version"], "Install uv, then run setup.cmd or bash setup.sh.")
    command_check("JDK", "java", ["-version"], "Install JDK 17 and set JAVA_HOME.",
                  r'version "(\d+)\.(\d+)', (17, 0))
    if not shutil.which("javac"):
        report("FAIL", "Java compiler", "javac was not found", "Install a full JDK 17, not a JRE.")
    else:
        report("PASS", "Java compiler", shutil.which("javac") or "javac")
    if shutil.which("mvn"):
        command_check("Maven", "mvn", ["-version"], "Install Maven 3.9+ or use the target's Maven Wrapper.",
                      r'Apache Maven (\d+)\.(\d+)', (3, 9))
    else:
        report("WARN", "System Maven", "mvn was not found; a target Maven/Gradle Wrapper is also valid",
               "Install Maven 3.9+ if the target project has no wrapper.")
    check_python()
    check_repository()
    check_target(args.project_root, args.require_pit or args.verify_pit_output)
    check_saved_data(args.data_project, args.require_pit, args.verify_pit_output)
    print(f"\nResult: {'PASS' if FAILURES == 0 else f'FAIL ({FAILURES} condition(s))'}")
    return 0 if FAILURES == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
