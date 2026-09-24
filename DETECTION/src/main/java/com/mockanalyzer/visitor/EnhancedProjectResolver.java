package com.mockanalyzer.visitor;

import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.symbolsolver.resolution.typesolvers.CombinedTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.JarTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.JavaParserTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.ReflectionTypeSolver;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * 非侵入式类型解析器：不修改被分析项目，也不要求项目完整编译。
 * Non-invasive type resolver: never edits the subject project and does not require a full build.
 */
public class EnhancedProjectResolver {
    /**
     * JavaParser 默认按 Java 11 解析，text block、instanceof 模式匹配、record、switch 表达式都会
     * 让整个文件解析失败并被跳过。Spring Security 7.1.1 有 230 个文件因此被跳过，其中 30 个是
     * Mockito 测试类，里面的 MCI 直接漏检。Java 17 语法是 Java 8/11 的超集，旧项目结果不变。
     * JavaParser parses as Java 11 by default, so a text block, an instanceof pattern, a record
     * or a switch expression fails the whole file and it is skipped. In Spring Security 7.1.1
     * that skipped 230 files, 30 of them Mockito test classes whose MCIs were silently missed.
     * Java 17 syntax is a superset of 8/11, so older projects are unaffected.
     *
     * 用 JavaParser 3.28.2 支持的 Java 25（当前 LTS），覆盖 record 模式、switch 模式、未命名变量等。
     * 唯一不兼容的是 Java 8 及以前把 "_" 当标识符，由 {@link #legacyParserConfiguration()} 兜底。
     * Uses Java 25 (the current LTS, supported by JavaParser 3.28.2) to cover record patterns,
     * switch patterns and unnamed variables. The one incompatibility, "_" as an identifier in
     * Java 8 and earlier, is covered by {@link #legacyParserConfiguration()}.
     */
    public static ParserConfiguration parserConfiguration() {
        return new ParserConfiguration().setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_25);
    }

    /** 新语法级别解析失败时的回退 / Fallback when a file fails at the modern language level. */
    public static ParserConfiguration legacyParserConfiguration() {
        return new ParserConfiguration().setLanguageLevel(ParserConfiguration.LanguageLevel.JAVA_8);
    }

    public static CombinedTypeSolver createTypeSolver(Path projectRoot, boolean resolveDependencies)
            throws IOException, InterruptedException {
        CombinedTypeSolver solver = new CombinedTypeSolver();
        solver.add(new ReflectionTypeSolver());

        // 即使项目不能编译，JavaParser 仍可从全部源码根解析项目内部类型。
        // JavaParser can resolve project types from source roots even when the project cannot compile.
        addSourceRoots(projectRoot, solver);
        Set<Path> dependencies = new LinkedHashSet<>();
        if (resolveDependencies) {
            if (Files.exists(projectRoot.resolve("pom.xml"))) {
                dependencies.addAll(resolveMavenClasspath(projectRoot));
            } else if (Files.exists(projectRoot.resolve("build.gradle"))
                    || Files.exists(projectRoot.resolve("build.gradle.kts"))
                    || Files.exists(projectRoot.resolve("settings.gradle"))
                    || Files.exists(projectRoot.resolve("settings.gradle.kts"))) {
                dependencies.addAll(resolveGradleClasspath(projectRoot));
            }
        }
        dependencies.addAll(findExistingJars(projectRoot));
        for (Path dependency : dependencies) {
            try {
                solver.add(new JarTypeSolver(dependency));
            } catch (IOException error) {
                System.err.println("[WARN] Cannot load dependency / 无法加载依赖: " + dependency + " - " + error.getMessage());
            }
        }
        return solver;
    }

    private static void addSourceRoots(Path root, CombinedTypeSolver solver) throws IOException {
        try (var paths = Files.walk(root)) {
            paths.filter(Files::isDirectory)
                    .filter(path -> path.endsWith(Path.of("src", "main", "java"))
                            || path.endsWith(Path.of("src", "test", "java")))
                    .sorted()
                    .forEach(path -> solver.add(new JavaParserTypeSolver(path, parserConfiguration())));
        }
    }

    /**
     * 以前用 -Dmdep.outputFile 写一个共享文件，但 reactor 里每个模块都写同一个文件、后写覆盖先写，
     * 最后只剩最后一个模块的 classpath——Dubbo 124 个模块只拿到 13 个 jar，netty、nacos 都不在里面，
     * "解析依赖"实际上从未生效。现在从输出里逐个模块收集 "Dependencies classpath:"，并用 -fae，
     * 一个模块解析失败不会让其余模块的 classpath 一起丢掉。
     * This used to write one shared -Dmdep.outputFile, but every reactor module writes that same file
     * and the last one wins: Dubbo's 124 modules yielded 13 jars, without netty or nacos, so "resolve
     * dependencies" never took effect. Each module's "Dependencies classpath:" is now collected from
     * the output, and -fae keeps one module's resolution failure from discarding everyone else's.
     */
    private static List<Path> resolveMavenClasspath(Path root) throws IOException, InterruptedException {
        Path wrapper = root.resolve(isWindows() ? "mvnw.cmd" : "mvnw");
        String executable = Files.isRegularFile(wrapper) ? wrapper.toString() : (isWindows() ? "mvn.cmd" : "mvn");
        List<String> command = new ArrayList<>();
        command.add(executable);
        String localRepository = System.getenv("CLONEDEMOCKER_MAVEN_REPO");
        if (localRepository == null || localRepository.isBlank()) {
            String userProfile = System.getenv("USERPROFILE");
            if (userProfile != null && !userProfile.isBlank()) {
                localRepository = Path.of(userProfile, ".m2", "repository").toString();
            }
        }
        if (localRepository != null && !localRepository.isBlank()) {
            command.add("-Dmaven.repo.local=" + localRepository);
        }
        command.addAll(List.of("-B", "-fae", "dependency:build-classpath", "-DincludeScope=test"));
        String[] lines = run(command, root).split("\\R");
        Set<Path> result = new LinkedHashSet<>();
        for (int index = 0; index + 1 < lines.length; index++) {
            if (!lines[index].contains("Dependencies classpath:")) continue;
            String classpath = lines[index + 1].trim();
            if (classpath.isEmpty() || classpath.startsWith("[")) continue;
            Arrays.stream(classpath.split(java.util.regex.Pattern.quote(File.pathSeparator)))
                    .map(Path::of).filter(Files::isRegularFile).forEach(result::add);
        }
        System.out.println("[INFO] Maven classpath entries / Maven 依赖条目: " + result.size());
        return new ArrayList<>(result);
    }

    private static List<Path> resolveGradleClasspath(Path root) throws IOException, InterruptedException {
        Path initScript = Files.createTempFile("clonedemocker-gradle-", ".gradle");
        String script = """
                allprojects { p ->
                    tasks.register('cloneDeMockerClasspath') {
                        doLast {
                            def c = p.configurations.findByName('testRuntimeClasspath')
                            if (c != null && c.canBeResolved) {
                                c.files.each { println 'CLONEDEMOCKER_CP:' + it.absolutePath }
                            }
                        }
                    }
                }
                """;
        Files.writeString(initScript, script, StandardCharsets.UTF_8);
        try {
            String executable;
            if (isWindows() && Files.exists(root.resolve("gradlew.bat"))) executable = root.resolve("gradlew.bat").toString();
            else if (Files.exists(root.resolve("gradlew"))) executable = root.resolve("gradlew").toString();
            else executable = isWindows() ? "gradle.bat" : "gradle";
            String output = run(List.of(executable, "-I", initScript.toString(), "cloneDeMockerClasspath", "--console=plain"), root);
            List<Path> result = new ArrayList<>();
            for (String line : output.split("\\R")) {
                if (line.startsWith("CLONEDEMOCKER_CP:")) {
                    Path path = Path.of(line.substring("CLONEDEMOCKER_CP:".length()));
                    if (Files.isRegularFile(path) && !result.contains(path)) result.add(path);
                }
            }
            System.out.println("[INFO] Gradle classpath entries / Gradle 依赖条目: " + result.size());
            return result;
        } finally {
            Files.deleteIfExists(initScript);
        }
    }

    private static List<Path> findExistingJars(Path root) throws IOException {
        try (var paths = Files.walk(root)) {
            return paths.filter(Files::isRegularFile)
                    .filter(path -> path.getFileName().toString().endsWith(".jar"))
                    .filter(path -> !path.toString().contains(File.separator + ".git" + File.separator))
                    .toList();
        }
    }

    private static String run(List<String> command, Path root) throws IOException, InterruptedException {
        System.out.println("[INFO] Resolve classpath / 解析依赖: " + String.join(" ", command));
        Process process = new ProcessBuilder(command).directory(root.toFile()).redirectErrorStream(true).start();
        String output = new String(process.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            System.err.println("[WARN] Classpath command failed; continuing with source-only type resolution / "
                    + "依赖解析命令失败，已继续使用纯源码类型解析 (" + exitCode + ")\n" + output);
        }
        return output;
    }

    private static boolean isWindows() {
        return System.getProperty("os.name").toLowerCase().contains("win");
    }
}
