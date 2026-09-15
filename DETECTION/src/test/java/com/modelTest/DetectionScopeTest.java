package com.modelTest;

import com.mockanalyzer.model.DetectionScope;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class DetectionScopeTest {
    @TempDir Path root;

    @Test
    void includesDirectoriesFilesAndPackagesWithoutEscapingProject() throws Exception {
        Path included = Files.createDirectories(root.resolve("src/test/java/demo"));
        Path chosen = Files.writeString(included.resolve("ChosenTest.java"), "package demo;");
        Path excluded = Files.writeString(included.resolve("ExcludedTest.java"), "package demo;");
        Path outside = Files.createTempFile("outside", ".java");

        DetectionScope scope = new DetectionScope();
        scope.includePaths = List.of("src/test/java");
        scope.excludePaths = List.of("src/test/java/demo/ExcludedTest.java");
        scope.packagePrefixes = List.of("demo.feature");

        assertTrue(scope.includesFile(root, chosen));
        assertFalse(scope.includesFile(root, excluded));
        assertFalse(scope.includesFile(root, outside));
        assertTrue(scope.includesPackage("demo.feature.service"));
        assertFalse(scope.includesPackage("demo.features"));
    }
}
