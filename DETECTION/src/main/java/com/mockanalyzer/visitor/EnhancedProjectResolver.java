package com.mockanalyzer.visitor;

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
                    || Files.exists(projectRoot.resolve("build.gradle.kts"))) {
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
                    .forEach(path -> solver.add(new JavaParserTypeSolver(path)));
        }
    }

    private static List<Path> resolveMavenClasspath(Path root) throws IOException, InterruptedException {
        Path output = Files.createTempFile("clonedemocker-maven-classpath-", ".txt");
        try {
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
            command.addAll(List.of("dependency:build-classpath", "-DincludeScope=test",
                    "-Dmdep.outputFile=" + output.toAbsolutePath()));
            run(command, root);
            String classpath = Files.exists(output) ? Files.readString(output, StandardCharsets.UTF_8).trim() : "";
            if (classpath.isEmpty()) return List.of();
            return Arrays.stream(classpath.split(java.util.regex.Pattern.quote(File.pathSeparator)))
                    .map(Path::of).filter(Files::isRegularFile).toList();
        } finally {
            Files.deleteIfExists(output);
        }
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
                    if (Files.isRegularFile(path)) result.add(path);
                }
            }
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
