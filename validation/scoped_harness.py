from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio.harness import BuildScope, ProjectHarness  # noqa: E402


class ScopedProjectHarness(ProjectHarness):
    """只对指定测试类跑 test/PIT，并把 compile/test/PIT 都限定到相关模块（-pl -am）。
    Runs test/PIT against only the given test classes, and scopes compile/test/PIT to the
    relevant modules (-pl -am).

    不加 -pl 时 Maven 会把整个 reactor（dubbo 有 112 个模块）的生命周期走一遍——哪怕
    -Dtest 已经限定了只执行哪个测试，每个模块的 enforcer/checkstyle/spotless 等插件
    还是会跑一遍，累加起来是真正的耗时大头，比 PIT 本身还慢。MCI 重构只改测试文件，
    其他模块不会依赖某个模块的测试产物，所以把 compile 也纳入 -pl -am 裁剪是安全的。
    Without -pl, Maven walks the whole reactor's lifecycle (dubbo has 112 modules) even
    though -Dtest already limits which test executes — every module still runs its own
    enforcer/checkstyle/spotless etc., and that adds up to the real bottleneck, bigger than
    PIT itself. MCI refactoring only touches test files, and no other module depends on
    another module's test output, so scoping compile into -pl -am too is safe.

    PIT 粒度是这些测试类组成的 suite 跑一次，不是逐个测试方法单独跑。
    PIT runs once for the suite formed by these test classes, not once per test method.
    """

    def __init__(self, test_classes: list[str], modules: list[str] | None = None,
                 maven_repo_local: str | Path | None = None) -> None:
        super().__init__(maven_repo_local)
        if not test_classes:
            raise ValueError("test_classes must not be empty / 测试类列表不能为空")
        self.test_classes = sorted(set(test_classes))
        self.modules = sorted(set(modules)) if modules else []

    def expected_test_classes(self) -> list[str]:
        """Classes that this scoped invocation must prove it actually executed."""
        return self.test_classes

    def _build_commands(self, root: Path, scope: BuildScope | None = None) -> tuple[list[str], list[str], list[str] | None] | None:
        # 范围由构造参数固定，调用方传入的 scope 不参与 / The scope is fixed by the constructor;
        # a scope passed by the caller does not apply.
        base = super()._build_commands(root)
        if base is None:
            return None
        compile_command, _, _ = base
        pattern = ",".join(self.test_classes)
        packages = sorted({name.rsplit(".", 1)[0] for name in self.test_classes if "." in name})
        target_classes = ",".join(f"{pkg}.*" for pkg in packages) or "*"
        scope_args = ["-pl", ",".join(self.modules), "-am"] if self.modules else []

        if (root / "pom.xml").is_file():
            executable = compile_command[0]
            # 这里是重新拼命令，不是复用 base 返回的列表，所以 -Dmaven.repo.local 必须
            # 在这三条里各自重新加一遍，否则会被裁剪掉、静默退回默认共享仓库。
            # These commands are rebuilt from scratch rather than reusing base's list, so
            # -Dmaven.repo.local must be re-added to each of the three here, or it gets
            # dropped and silently falls back to the default shared repository.
            repo_args = self._maven_repo_args()
            # 见 studio.harness.ProjectHarness._style_check_skip_args 的说明：论文的
            # "Syntactic Validity" 指编译器意义上能不能编译，不是某个项目自选的格式检查
            # 插件；而且我们往 pom.xml 注入 PIT 配置会重新序列化整份文件，格式检查会把
            # 这个（语义无关的）差异当成违规。
            # See studio.harness.ProjectHarness._style_check_skip_args: the paper's "Syntactic
            # Validity" means compiler-level compilability, not an opt-in style linter's
            # opinion — and injecting PIT config into pom.xml re-serializes the whole file,
            # which a style check would flag as a (semantically irrelevant) violation.
            style_check_skip_args = self._style_check_skip_args()
            compile_command = [executable, *repo_args, *style_check_skip_args, *scope_args, "-DskipTests", "test-compile"]
            # -pl <module> -am 会把这个模块依赖的其他模块也拉进 reactor，Maven 对 reactor 里
            # 每个模块都套用同一个 -Dtest 过滤。-DfailIfNoTests=false 管的是"这个模块压根没有
            # 测试目录"，管不住"这个模块有测试目录，但没有匹配 -Dtest 指定的那个类"——后者
            # 触发的是 surefire 自己的 failIfNoSpecifiedTests，不加这个 Maven 会在第一个不匹配
            # 的依赖模块（例如 dubbo-common）直接中止整个 reactor，真正的目标模块根本跑不到，
            # 而且 harness 会把这次中止误判成"两次测试结果都是空字典，判定一致"的假成功。
            # -pl <module> -am pulls that module's dependencies into the reactor too, and Maven
            # applies the same -Dtest filter to every module in it. -DfailIfNoTests=false only
            # covers "this module has no test sources at all" — it does nothing for "this module
            # has tests, just none matching the -Dtest class" — that's surefire's own
            # failIfNoSpecifiedTests. Without it, Maven aborts the whole reactor at the first
            # non-matching dependency module (e.g. dubbo-common) before ever reaching the actual
            # target module, and the harness would misread that abort as "both runs produced the
            # same empty test-results dict, so they're equivalent" — a false success.
            test_command = [
                executable, *repo_args, *style_check_skip_args, *scope_args, "test", f"-Dtest={pattern}",
                "-DfailIfNoTests=false", "-Dsurefire.failIfNoSpecifiedTests=false",
            ]
            # 同一个 -am reactor 问题在 PIT 上更狠：PIT 默认 failWhenNoMutations=true，
            # 目标类过滤器在 reactor 里第一个不相关的依赖模块（比如 dubbo-common）上
            # 一个变异体都生成不出来，PIT 直接把这判定成 BUILD FAILURE，中止整个
            # reactor——真正的目标模块的 PIT 从来没跑过。这个 bug 在这次全量 109 个
            # MCI 的跑批里是系统性的：95 个 SUCCESS 里的 pitStatus 全部是 FAILED、
            # mutationTotal 全部是 0，没有一个真的验证过 PIT 分数没退化，之前的
            # classify_transition 也没检查 pitStatus，只检查了从空变异体字典推出来的
            # mutation_regressed（两边都空，比较空字典，vacuously 判定没退化）。
            # The same -am reactor problem hits PIT even harder: PIT defaults to
            # failWhenNoMutations=true, and the target-class filter produces zero mutants
            # on the first unrelated dependency module in the reactor (e.g. dubbo-common),
            # which PIT treats as a hard BUILD FAILURE that aborts the whole reactor — the
            # real target module's PIT run never happens. This was systemic across the full
            # 109-MCI batch: all 95 SUCCESS results had pitStatus=FAILED and
            # mutationTotal=0, so mutation-score preservation was never actually verified;
            # classify_transition also never checked pitStatus, only the mutant-dict-derived
            # mutation_regressed (empty vs empty compares as "no regression" vacuously).
            pit_command = [
                executable, *repo_args, *style_check_skip_args, *scope_args, "org.pitest:pitest-maven:mutationCoverage",
                f"-DtargetTests={pattern}", f"-DtargetClasses={target_classes}",
                "-DoutputFormats=XML", "-DfailWhenNoMutations=false",
            ]
            return compile_command, test_command, pit_command

        # Gradle 走与产品 harness 相同的裁剪、测试过滤和 PIT 注入。
        # Gradle takes the same scoping, test filtering and PIT injection as the product harness.
        return self._gradle_commands(root, BuildScope(tuple(self.modules), tuple(self.test_classes)))
