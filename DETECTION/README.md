# CloneDeMocker\_Detection\_tool

`CloneDeMocker_Detection_tool` is a standalone command-line utility for analyzing Mockito-based mock usage in Java test code and detecting mock clones.

---

## ⚙️ Environment Setup

> **Platform Support:** Windows (currently only supported)
> **Java Version:** Java 17 or higher
> **Build Tool Support:** Maven (≥ 3.6) or Gradle (≥ 6.0)

Before using the tool, make sure your target Java project is configured **either with Maven or Gradle** and the build system can resolve dependencies properly.

* ✅ **For Maven-based projects**: Ensure the following command completes successfully:

  ```bash
  mvn clean package -DskipTests
  ```

* ✅ **For Gradle-based projects**: Ensure the following command completes successfully:

  ```bash
  gradlew.bat assemble -x test
  ```

> ❗️Note: The Java project **does not need to compile completely**, but dependency resolution must succeed so that source files and symbols can be analyzed.

---

###  Detect Mock Clones
If you **only want to use the tool**, download the prebuilt binary:

```
CloneDeMocker_Detection_tool.jar
```

Then: Analyze the target project and identify mock clones across test cases. The output includes grouped clone instances, reusable stub patterns, and estimated LOC savings.

```bash
java -jar CloneDeMocker_Detection_tool.jar clone <projectRoot> <clone.json> [--skip]
```

* `<projectRoot>` – Path to the root directory of the Java project
* `<clone.json>` – Output file path for detected mock clone instances
* `--skip` *(optional)* – Skip the build step (faster if already built)


> 💡 **Note:** The tool also provides two helper commands for extracting mock information, useful for debugging or custom analysis:

```bash
java -jar CloneDeMocker_Detection_tool.jar info <projectRoot> <mockinfo.json> [--skip]
# → Extracts raw mock metadata

java -jar CloneDeMocker_Detection_tool.jar sequence <projectRoot> <sequences.json> [--skip]
# → Extracts abstracted mock sequences per test case
```

---
## ⚙️ Project Setup

`CloneDeMocker_Detection_tool` is implemented with **JavaParser** for static analysis of Java test code and is built with **Maven**.

To run the tool from source, please make sure your environment is correctly configured:

1. **Java 17 or higher** must be installed and available in your system path.
2. **Maven (≥ 3.6)** must be installed and properly configured.
3. Clone or download the project source code.
4. Build the project using Maven:

   ```bash
   mvn clean package
   ```

After a successful build, the executable JAR will be available in the `target/` directory.

