package com.mockanalyzer.model;

import com.google.gson.Gson;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * 检测池：限定哪些文件和包可以产生 Mock Clone Instance。
 * Detection Scope: limits which files and packages may produce Mock Clone Instances.
 */
public class DetectionScope {
    public List<String> includePaths = new ArrayList<>();
    public List<String> excludePaths = new ArrayList<>();
    public List<String> packagePrefixes = new ArrayList<>();

    public static DetectionScope all() {
        return new DetectionScope();
    }

    public static DetectionScope read(Path path) throws IOException {
        try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            DetectionScope scope = new Gson().fromJson(reader, DetectionScope.class);
            return scope == null ? all() : scope;
        }
    }

    public boolean includesFile(Path projectRoot, Path file) {
        Path root = projectRoot.toAbsolutePath().normalize();
        Path target = file.toAbsolutePath().normalize();
        if (!target.startsWith(root)) {
            return false;
        }
        String relative = normalize(root.relativize(target).toString());
        if (matchesAny(relative, excludePaths)) {
            return false;
        }
        return includePaths == null || includePaths.isEmpty() || matchesAny(relative, includePaths);
    }

    public boolean includesPackage(String packageName) {
        if (packagePrefixes == null || packagePrefixes.isEmpty()) {
            return true;
        }
        String actual = packageName == null ? "" : packageName;
        return packagePrefixes.stream()
                .filter(prefix -> prefix != null && !prefix.isBlank())
                .anyMatch(prefix -> actual.equals(prefix) || actual.startsWith(prefix + "."));
    }

    private static boolean matchesAny(String relativePath, List<String> configuredPaths) {
        if (configuredPaths == null) {
            return false;
        }
        for (String configured : configuredPaths) {
            if (configured == null || configured.isBlank()) {
                continue;
            }
            String candidate = normalize(configured);
            while (candidate.startsWith("./")) {
                candidate = candidate.substring(2);
            }
            while (candidate.endsWith("/")) {
                candidate = candidate.substring(0, candidate.length() - 1);
            }
            // The UI represents the project-root checkbox as ".".  Treat it as the
            // whole project rather than trying to match source paths such as
            // "module/src/test/..." against "./".
            if (candidate.equals(".")) {
                return true;
            }
            if (relativePath.equals(candidate) || relativePath.startsWith(candidate + "/")) {
                return true;
            }
        }
        return false;
    }

    private static String normalize(String path) {
        return path.replace('\\', '/');
    }
}
