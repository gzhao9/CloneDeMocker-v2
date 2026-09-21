package com.mockanalyzer.exporter;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParseResult;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.ImportDeclaration;
import com.github.javaparser.symbolsolver.JavaSymbolSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.CombinedTypeSolver;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.mockanalyzer.model.MockInfo;
import com.mockanalyzer.model.DetectionScope;
import com.mockanalyzer.visitor.EnhancedProjectResolver;
import com.mockanalyzer.visitor.MockCollectorVisitor;

import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;

/**
 * 分析整个 Java 项目并输出 MockInfo 列表。
 * 用法:
 * java -jar mock-analyzer.jar -project <pathToProjectRoot> <output.json>
 */
public class MockInfoExporter {

    /**
     * 分析项目并输出 MockInfo 列表到 JSON 文件。
     *
     * @param projectRoot 项目根目录
     * @param outputPath  输出 JSON 文件路径
     * @param runCommand  是否执行构建命令
     */

    public static void export(Path projectRoot, String outputPath, boolean runCommand) {

        if (!Files.exists(projectRoot) || !Files.isDirectory(projectRoot)) {
            System.err.println("Error: Invalid project root -> " + projectRoot);
            System.exit(1);
        }

        try {
            List<MockInfo> combinedResults = analyzeProject(projectRoot, runCommand);
            writeMockInfoToJson(combinedResults, outputPath);
            System.out.println("Analysis completed. Result -> " + outputPath);
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    public static List<MockInfo> analyzeProject(Path projectRoot, boolean runCommand)
            throws IOException, InterruptedException {
        return analyzeProject(projectRoot, runCommand, DetectionScope.all());
    }

    /**
     * 论文步骤一：在检测池内提取 Mock Logic。
     * Paper Step 1: extracts Mock Logic inside the Detection Scope.
     */
    public static List<MockInfo> analyzeProject(Path projectRoot, boolean runCommand, DetectionScope scope)
            throws IOException, InterruptedException {
        List<MockInfo> combinedResults = new ArrayList<>();

        // 获取 CombinedTypeSolver
        CombinedTypeSolver combinedSolver = EnhancedProjectResolver.createTypeSolver(projectRoot, runCommand);

        ParserConfiguration parserConfiguration = EnhancedProjectResolver.parserConfiguration();
        parserConfiguration.setSymbolResolver(new JavaSymbolSolver(combinedSolver));
        JavaParser parser = new JavaParser(parserConfiguration);
        JavaParser legacyParser = new JavaParser(EnhancedProjectResolver.legacyParserConfiguration()
                .setSymbolResolver(new JavaSymbolSolver(combinedSolver)));
        int parseFailures = 0;

        // 收集所有 Java 文件
        List<Path> javaFiles = new ArrayList<>();
        try (Stream<Path> paths = Files.walk(projectRoot)) {
            paths.filter(Files::isRegularFile)
                    .filter(p -> p.toString().endsWith(".java"))
                    .filter(p -> scope.includesFile(projectRoot, p))
                    .sorted()
                    .forEach(javaFiles::add);
        }

        int totalFiles = javaFiles.size();
        System.out.println("[PROGRESS] SCAN 0/" + totalFiles);
        for (int fileIndex = 0; fileIndex < totalFiles; fileIndex++) {
            Path javaFile = javaFiles.get(fileIndex);
            try {
                ParseResult<CompilationUnit> parseResult = parser.parse(javaFile);
                if (!parseResult.isSuccessful()) {
                    parseResult = legacyParser.parse(javaFile);
                }

                if (parseResult.isSuccessful() && parseResult.getResult().isPresent()) {
                    CompilationUnit cu = parseResult.getResult().get();

                    // 判断是否包含 mockito 导入
                    boolean hasMockitoImport = cu.findAll(ImportDeclaration.class).stream()
                            .anyMatch(imp -> imp.getNameAsString().startsWith("org.mockito"));

                    String packageName = cu.getPackageDeclaration()
                            .map(declaration -> declaration.getNameAsString())
                            .orElse("");
                    if (hasMockitoImport && scope.includesPackage(packageName)) {
                        MockCollectorVisitor visitor = new MockCollectorVisitor(javaFile.toString());
                        visitor.visit(cu, null);

                        List<MockInfo> mockList = visitor.getFinalMockList();
                        for (MockInfo info : mockList) {
                            info.classContext.filePath = javaFile.toString();
                        }
                        combinedResults.addAll(mockList);
                    }
                } else {
                    parseFailures++;
                    System.err.println("[WARN] Parse failed: " + javaFile + " - "
                            + parseResult.getProblems().stream().findFirst().map(Object::toString).orElse(""));
                }
            } catch (Exception e) {
                System.err.println("[WARN] Skipping file due to exception: " + javaFile + " - " + e.getMessage());
            }
            // 带上当前文件名。只报 "312/4871" 时，一次扫描在界面上就是一条无声移动的进度条；
            // 卡住时既看不出卡在哪个文件，也分不清是真卡住还是某个文件特别大。名字是这里唯一
            // 能提供、而调用方无法自己推断的信息。
            // Carries the current file name. Reporting only "312/4871" leaves a scan as a bar
            // moving in silence: when it stalls, neither which file it stalled on nor whether it
            // stalled at all is visible. The name is the one thing only this loop can supply and
            // the caller cannot infer.
            if ((fileIndex + 1) % 10 == 0 || fileIndex + 1 == totalFiles) {
                System.out.println("[PROGRESS] SCAN " + (fileIndex + 1) + "/" + totalFiles
                        + " " + javaFile.getFileName());
            }
        }

        // 逐文件的警告会被界面日志截断，汇总一行保证解析失败数总能看到。
        // Per-file warnings get truncated in the UI log; one summary line keeps the count visible.
        System.out.println("[INFO] Parse failures / 解析失败文件: " + parseFailures + "/" + totalFiles);

        // 固定源码快照和检测池下，排序可保证选择界面的 ID 稳定。
        // Sorting keeps selection IDs stable for a fixed source snapshot and scope.
        for (int i = 0; i < combinedResults.size(); i++) {
            combinedResults.get(i).rawMockObjectId = i;
        }

        return combinedResults;
    }

    public static void writeMockInfoToJson(List<MockInfo> mockInfos, String outputPath) {
        Gson gson = new GsonBuilder()
                .setPrettyPrinting()
                .disableHtmlEscaping()
                .create();

        try (OutputStreamWriter writer = new OutputStreamWriter(
                new FileOutputStream(outputPath), StandardCharsets.UTF_8)) {
            gson.toJson(mockInfos, writer);
        } catch (IOException e) {
            e.printStackTrace();
        }
    }
}
