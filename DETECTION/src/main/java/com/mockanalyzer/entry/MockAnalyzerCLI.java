package com.mockanalyzer.entry;

import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.Files;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

import com.google.gson.Gson;
import com.google.gson.reflect.TypeToken;
import com.mockanalyzer.model.DetectionScope;
import com.mockanalyzer.model.MockInfo;
import com.mockanalyzer.sequencesParser.ResolutionDiagnostics;

import com.mockanalyzer.exporter.MockCloneExporter;
import com.mockanalyzer.exporter.MockInfoExporter;

public class MockAnalyzerCLI {

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            printHelp();
            return;
        }

        String mode = args[0];

        switch (mode) {
            case "info":
            case "scan":
                handleInfo(args);
                break;
            case "sequence":
                handleSequence(args);
                break;
            case "clone":
                handleClone(args);
                break;
            case "detect":
                handleDetect(args);
                break;
            default:
                System.err.println("Unknown command: " + mode);
                printHelp();
        }

        long degraded = ResolutionDiagnostics.stackOverflowCount();
        if (degraded > 0) {
            System.err.println("[WARN] Symbol resolution degraded to syntactic matching at "
                    + degraded + " site(s) because JavaParser overflowed the stack on recursive"
                    + " generic types. Detection continued; report this count alongside results.");
        }
    }

    private static void handleInfo(String[] args) {
        if (args.length < 3) {
            System.err.println("Usage: info <projectRoot> <mockinfo.json> [--run]");
            return;
        }

        Path projectRoot = Paths.get(args[1]);
        String outputPath = args[2];
        boolean runCommand = true;

        if (Arrays.asList(args).contains("--skip")) {
            runCommand = false;
        }

        if (!Files.exists(projectRoot)) {
            System.err.println("[ERROR] Project path does not exist: " + projectRoot);
            return;
        }

        try {
            MockInfoExporter.writeMockInfoToJson(
                    MockInfoExporter.analyzeProject(projectRoot, runCommand, readScope(args)), outputPath);
        } catch (Exception e) {
            throw new RuntimeException("Project scan failed", e);
        }
    }

    private static void handleSequence(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("Usage: sequence <projectRoot> <sequences.json> [--skip]");
            return;
        }

        Path projectRoot = Paths.get(args[1]);
        String outputPath = args[2];
        boolean runCommand = !Arrays.asList(args).contains("--skip");

        if (!Files.exists(projectRoot)) {
            System.err.println("[ERROR] Project path does not exist: " + projectRoot);
            return;
        }

        MockInfoExporter.export(projectRoot, outputPath, runCommand);
    }

    private static void handleClone(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("Usage: clone <projectRoot> <clone.json> [--skip]");
            return;
        }

        Path projectRoot = Paths.get(args[1]);
        String outputPath = args[2];
        boolean runCommand = !Arrays.asList(args).contains("--skip");

        if (!Files.exists(projectRoot)) {
            System.err.println("[ERROR] Project path does not exist: " + projectRoot);
            return;
        }

        MockCloneExporter.exportClones(projectRoot, outputPath, runCommand, readScope(args), readMockIds(args));
    }

    private static void handleDetect(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("Usage: detect <mockinfo.json> <clone.json> [--mock-ids ids.json]");
            return;
        }
        Path input = Paths.get(args[1]);
        if (!Files.exists(input)) {
            System.err.println("[ERROR] Mock info file does not exist: " + input);
            return;
        }
        try (var reader = Files.newBufferedReader(input)) {
            List<MockInfo> mockInfos = new Gson().fromJson(reader, new TypeToken<List<MockInfo>>() { }.getType());
            MockCloneExporter.exportClones(mockInfos, args[2], readMockIds(args));
        }
    }

    private static DetectionScope readScope(String[] args) throws Exception {
        String path = optionValue(args, "--scope");
        return path == null ? DetectionScope.all() : DetectionScope.read(Paths.get(path));
    }

    private static Set<Integer> readMockIds(String[] args) throws Exception {
        String path = optionValue(args, "--mock-ids");
        if (path == null) {
            return null;
        }
        try (var reader = Files.newBufferedReader(Paths.get(path))) {
            List<Integer> ids = new Gson().fromJson(reader, new TypeToken<List<Integer>>() { }.getType());
            return ids == null ? Collections.emptySet() : new HashSet<>(ids);
        }
    }

    private static String optionValue(String[] args, String option) {
        for (int i = 0; i < args.length - 1; i++) {
            if (option.equals(args[i])) {
                return args[i + 1];
            }
        }
        return null;
    }

    private static void printHelp() {
        System.out.println("Usage:");
        System.out.println("  java -jar mock-analyzer.jar info <projectRoot> <mockinfo.json> [--skip]");
        System.out.println("  java -jar mock-analyzer.jar scan <projectRoot> <mockinfo.json> [--scope scope.json] [--skip]");
        System.out.println("  java -jar mock-analyzer.jar sequence <projectRoot> <sequences.json> [--skip]");
        System.out.println("  java -jar mock-analyzer.jar clone <projectRoot> <clone.json> [--scope scope.json] [--mock-ids ids.json] [--skip]");
        System.out.println("  java -jar mock-analyzer.jar detect <mockinfo.json> <clone.json> [--mock-ids ids.json]");
    }

}
