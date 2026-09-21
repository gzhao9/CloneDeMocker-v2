"""
记录每一次验证的结果，让输入完全相同的验证不必重跑。

一次跑批里最浪费的是基线：99 个 MCI 里若有 40 个落在同一个模块，那 40 次基线编译加测试
跑的是**字节完全相同的源码**，结果注定一致。裁剪范围之后单次基线便宜了，但 40 次仍然是
40 次。断点重来时更甚——前 60 个 MCI 的验证全部要从头再做一遍。

账本按「源码指纹 + 范围 + 是否开 PIT + 流水线代次」做键。任何一项不同，键就不同，命中不
了；所以复用的永远是同一份输入下的同一个结论，而不是一个近似的猜测。

**复用必须留痕。** 论文里报告「通过验证」时，读者有权知道其中哪些是这次实测、哪些是复用
上一次的记录——环境可能变了（Maven 仓库状态、JDK 版本），而记录下来的「编译通过」抓不到
这种差异。所以每一层都带上时间戳写进结果，报告能如实说清两者的比例，也随时可以强制全量
重验。

Records each verification so one with identical inputs need not run again.

The largest waste in a batch is the baseline: if 40 of 99 MCIs sit in the same module, those
40 baseline compile-and-test runs execute over byte-identical source and are bound to agree.
Scoping made one baseline cheap, but 40 of them are still 40. Resuming after an interruption is
worse still, re-verifying everything the first 60 MCIs already established.

The ledger keys on source fingerprint, scope, whether PIT ran, and the pipeline generation.
Any difference in those yields a different key and no hit, so what is reused is always the same
conclusion drawn from the same inputs rather than an approximation.

**Reuse has to leave a trace.** When the paper reports that something passed verification, a
reader is entitled to know how much of it was measured in this run and how much was replayed
from an earlier record: the environment may have moved (the Maven repository, the JDK), and a
recorded "compilation passed" cannot detect that. Every reused tier is therefore stamped with
its timestamp in the result, so a report can state the split honestly and a full re-verification
is always one flag away.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class VerificationLedger:
    def __init__(self, repository_root: Path) -> None:
        self.directory = repository_root / ".clonedemocker" / "verification-ledger"

    @staticmethod
    def fingerprint(files: dict[Path, str]) -> str:
        """被验证的那批源文件的内容指纹。任何一个字节变了，指纹就变。
        A content fingerprint of the sources under verification: one changed byte, one changed
        fingerprint."""
        digest = hashlib.sha256()
        for path, content in sorted(files.items(), key=lambda item: item[0].as_posix()):
            digest.update(path.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(content.encode("utf-8")).digest())
        return digest.hexdigest()

    @staticmethod
    def key(kind: str, source_fingerprint: str, scope: str, run_pit: bool, generation: str,
            patch_fingerprint: str = "") -> str:
        payload = json.dumps({
            "kind": kind,
            "sources": source_fingerprint,
            "scope": scope,
            "runPit": bool(run_pit),
            "generation": generation,
            "patch": patch_fingerprint,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def read(self, key: str) -> dict[str, Any] | None:
        path = self.directory / f"{key}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if record.get("key") != key or not self._usable(record.get("evidence") or {}):
            return None
        return record

    @staticmethod
    def _usable(evidence: dict[str, Any]) -> bool:
        """
        编译和测试都通过，而且确实产生了未跳过的测试结果。只看状态不够：旧 harness 读不到过长路径
        下的 Gradle 报告时，状态是 PASSED 而结果为空，这份空证据被记下后，修好 harness 也会被原样
        回放，MCI 永远停在"环境未就绪"。读取时同样检查，已经写进去的这类记录随之失效。
        Compile and test passed and at least one non-skipped test result exists. Status alone is
        not enough: when the old harness could not read Gradle reports under an overlong path, the
        status was PASSED with no results, and once recorded that empty evidence was replayed even
        after the harness was fixed, pinning the MCI at "environment not ready". Reads check too,
        so records of that kind already on disk stop being served.
        """
        if str(evidence.get("compileStatus")) != "PASSED" or str(evidence.get("testStatus")) != "PASSED":
            return False
        return any(status != "SKIPPED" for status in (evidence.get("testResults") or {}).values())

    def write(self, key: str, evidence: dict[str, Any], kind: str) -> None:
        """
        只登记确实通过的验证。失败的结果不进账本——失败往往是环境性的（依赖没下全、端口
        被占、磁盘满），下一次很可能就好了，把它记下来只会让一次偶发故障永久生效。
        Only a verification that actually passed is recorded. Failures stay out: a failure is
        often environmental (an unresolved dependency, a busy port, a full disk) and likely to
        clear on the next attempt, so recording it would make one transient fault permanent.
        """
        if not self._usable(evidence):
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{key}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "key": key,
            "kind": kind,
            "recordedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "evidence": evidence,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def prune(self, generation: str) -> int:
        """清掉上一代流水线留下的记录，避免账本无限增长。
        Drops records from an older pipeline generation so the ledger does not grow forever."""
        removed = 0
        if not self.directory.is_dir():
            return 0
        for path in self.directory.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                removed += 1
                continue
            if record.get("generation") not in (None, generation):
                path.unlink(missing_ok=True)
                removed += 1
        return removed
