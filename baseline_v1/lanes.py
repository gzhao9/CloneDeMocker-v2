"""Elastic lanes on one host: MCI claims, a lane registry, stop requests and host load.

With `drive.py --claim`, lanes on one host do not split the MCI list in advance. Each lane claims an
MCI right before running it (an O_EXCL file under .git/pair-claims/), so lanes can be added or
removed one at a time without restarting the others. A claim whose owner process is gone is taken
over. A lane asked to stop (`<lane>.stop` under .git/pair-lanes/) exits before its next MCI, so no
run in flight is thrown away. baseline_v1/autoscale.py uses these to size the lane pool.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import time
from pathlib import Path

GIT = Path(__file__).resolve().parents[1] / ".git"
CLAIMS = GIT / "pair-claims"
LANES = GIT / "pair-lanes"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) on Windows terminates the process; ask the kernel instead.
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok) and code.value == 259                    # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _owner(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return 0


# ---- claims -----------------------------------------------------------------------------------

def _claim_path(project: str, mci_id: str) -> Path:
    return CLAIMS / (hashlib.sha1(f"{project}\0{mci_id}".encode("utf-8")).hexdigest() + ".claim")


def claim(project: str, mci_id: str, lane: str) -> bool:
    CLAIMS.mkdir(parents=True, exist_ok=True)
    path = _claim_path(project, mci_id)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {lane} {mci_id}\n".encode("utf-8"))
            os.close(fd)
            return True
        except FileExistsError:
            owner = _owner(path)
            if owner == os.getpid():
                return True
            if owner and pid_alive(owner):
                return False
            path.unlink(missing_ok=True)                          # its lane died mid-MCI
    return False


def release(project: str, mci_id: str) -> None:
    path = _claim_path(project, mci_id)
    if _owner(path) == os.getpid():
        path.unlink(missing_ok=True)


# ---- registry ---------------------------------------------------------------------------------

def register(lane: str, pid: int | None = None) -> None:
    LANES.mkdir(parents=True, exist_ok=True)
    (LANES / f"{lane}.pid").write_text(f"{pid or os.getpid()}\n", encoding="utf-8")


def unregister(lane: str) -> None:
    (LANES / f"{lane}.pid").unlink(missing_ok=True)


def live_lanes() -> list[str]:
    """Lanes whose supervisor is running, by name."""
    if not LANES.is_dir():
        return []
    alive = []
    for path in sorted(LANES.glob("*.pid")):
        if pid_alive(_owner(path)):
            alive.append(path.stem)
        else:
            path.unlink(missing_ok=True)
    return alive


def request_stop(lane: str) -> None:
    LANES.mkdir(parents=True, exist_ok=True)
    (LANES / f"{lane}.stop").write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"), encoding="utf-8")


def stop_requested(lane: str) -> bool:
    return (LANES / f"{lane}.stop").is_file()


def clear_stop(lane: str) -> None:
    (LANES / f"{lane}.stop").unlink(missing_ok=True)


# ---- host load ----------------------------------------------------------------------------------

def available_ram_gb() -> float:
    if os.name == "nt":
        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.ullAvailPhys / 2**30
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 2**20
    return 0.0


def cpu_percent(seconds: float = 10.0) -> float:
    """Busy share of all cores over `seconds`."""
    if os.name == "nt":
        def times():
            idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
            return idle.value, kernel.value + user.value          # kernel time includes idle time
        i0, t0 = times()
        time.sleep(seconds)
        i1, t1 = times()
        total = t1 - t0
        return 100.0 * (1 - (i1 - i0) / total) if total else 0.0

    def stat():
        fields = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
        return fields[3] + fields[4], sum(fields)
    i0, t0 = stat()
    time.sleep(seconds)
    i1, t1 = stat()
    return 100.0 * (1 - (i1 - i0) / (t1 - t0)) if t1 > t0 else 0.0
