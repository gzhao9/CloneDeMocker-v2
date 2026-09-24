package com.mockanalyzer.manual;

import java.nio.file.Path;
import java.nio.file.Paths;

import com.mockanalyzer.exporter.MockCloneExporter;

public class ManualVerify {
    public static void main(String[] args) throws Exception {
        String community = "Apache";

        String projectName = "dubbo";

        Path projectRoot = Paths.get("C:\\Java_projects\\"+community+"\\" + projectName);
        String outputPath = "C:\\Users\\10590\\OneDrive - stevens.edu\\PHD\\2025 Spring\\mock clone detection\\mock-clone-analyzer\\Example Test File\\" + projectName + " clone.json";
        boolean runCommand = false; // Set to true if you want to re-run maven/gradle build

        MockCloneExporter.exportClones(projectRoot, outputPath, runCommand);


        // verifyOutputSequences("C:\\Java_projects\\Apache\\" + projectName,
        //         "C:\\Users\\gzhao9\\OneDrive - stevens.edu\\PHD\\2025 Spring\\mock clone detection\\mock-clone-analyzer\\Example Test File\\"
        //                 + projectName + " clone.json",
        //         false);
        // manualVerifyProcess();
        // manualVerifySolver();
    }

}
  