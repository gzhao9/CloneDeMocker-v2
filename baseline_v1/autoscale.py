"""Size this host's pool of `drive.py --claim` lanes from load and measured throughput.

    python baseline_v1/autoscale.py --initial 5 --max 12 -- --reverse --round1-failures skip \
        --root cloudstack=D:\\Java_projects\\Apache\\cloudstack cloudstack

Everything after `--` is passed to each lane. Every minute it samples CPU and free RAM, then:

  * Shrink now if the host is short: free RAM under --ram-floor GB, or CPU above --cpu-high % for
    five minutes. The newest lane is asked to stop and leaves after its current MCI.
  * After a change, wait --settle minutes (a new lane copies and compiles a workspace first), then
    measure throughput. If an added lane did not raise it by at least a third of one lane's share,
    that lane is stopped and the pool is capped one lower for --cap-hours.
  * Grow by one lane when CPU averaged under --cpu-low % and free RAM stayed above --ram-grow GB
    over the last 15 minutes, below the cap, and enough MCIs are left to keep another lane busy.

It also publishes the host's rows every --publish minutes. Each lane syncs only every 10 of its own
MCIs, which with several lanes left up to ~2 h of rows unpublished and invisible to other hosts.

Throughput is V2 and V1 results per hour in validation/results/pair-run.log. Decisions go to
validation/results/pair-autoscale.log. One instance per checkout (.git/pair-autoscale.pid).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from baseline_v1 import lanes  # noqa: E402

RESULTS = REPO / "validation" / "results"
RUN_LOG = RESULTS / "pair-run.log"
LOG = RESULTS / "pair-autoscale.log"
PIDFILE = REPO / ".git" / "pair-autoscale.pid"
RESULT_LINE = re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d) (?:\[[^\]]+\] )?  V[12] ")


def log(message: str) -> None:
    line = f"{datetime.now():%m-%d %H:%M:%S} {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def results_per_hour(since: datetime, until: datetime) -> float:
    """V2+V1 results logged in [since, until), per hour."""
    count = 0
    year = until.year
    with RUN_LOG.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            m = RESULT_LINE.match(line)
            if m:
                at = datetime.strptime(f"{year}-{m.group(1)}", "%Y-%m-%d %H:%M:%S")
                if since <= at < until:
                    count += 1
    hours = (until - since).total_seconds() / 3600
    return count / hours if hours > 0 else 0.0


def remaining_mcis(lane_args: list[str]) -> int | None:
    """MCIs this host's lanes could still take (same routing as drive.py), or None if unknown."""
    try:
        from baseline_v1 import drive
        projects = [a for a in lane_args if not a.startswith("-") and "=" not in a]
        drive.ROUTE["round1"] = "skip" if "skip" in lane_args else ("only" if "only" in lane_args else None)
        total = 0
        for project in projects[-1:]:
            should_run = drive.make_should_run(project)
            should_run("")                                  # fills the remote cache
            known = drive._remote_cache.get("round1") or {}
            total += sum(1 for m in known if should_run(m))
        return total
    except Exception as error:  # noqa: BLE001
        log(f"remaining count failed ({type(error).__name__}: {error}); not growing on it")
        return None


def start_lane(name: str, lane_args: list[str]) -> None:
    console = (RESULTS / f"pair-lane-{name}.console.log").open("a", encoding="utf-8")
    flags = 0x08000000 if os.name == "nt" else 0                    # CREATE_NO_WINDOW alone (AGENTS.md 5)
    subprocess.Popen([sys.executable, str(REPO / "baseline_v1" / "drive.py"), "--lane", name, "--claim", *lane_args],
                     cwd=REPO, stdout=console, stderr=subprocess.STDOUT, creationflags=flags)


def lane_number(name: str) -> int:
    m = re.fullmatch(r"L(\d+)", name)
    return int(m.group(1)) if m else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial", type=int, default=0, help="lanes to start now if fewer are running")
    parser.add_argument("--min", type=int, default=1)
    parser.add_argument("--max", type=int, default=12)
    parser.add_argument("--cpu-low", type=float, default=70.0)
    parser.add_argument("--cpu-high", type=float, default=92.0)
    parser.add_argument("--ram-grow", type=float, default=6.0)
    parser.add_argument("--ram-floor", type=float, default=3.0)
    parser.add_argument("--settle", type=float, default=50.0, help="minutes after a change before judging it")
    parser.add_argument("--cap-hours", type=float, default=3.0)
    parser.add_argument("--publish", type=float, default=15.0, help="minutes between publishes of this host's rows")
    args, lane_args = parser.parse_known_args()
    if lane_args[:1] == ["--"]:
        lane_args = lane_args[1:]

    if PIDFILE.is_file():
        try:
            other = int(PIDFILE.read_text().split()[0])
        except ValueError:
            other = 0
        if other != os.getpid() and lanes.pid_alive(other):
            sys.exit(f"autoscale already running (pid {other})")
    PIDFILE.write_text(f"{os.getpid()}\n")

    cpu: deque[float] = deque(maxlen=15)
    ram: deque[float] = deque(maxlen=15)
    last_change = datetime.now() - timedelta(hours=1)
    pending: dict | None = None                # an added lane waiting to be judged
    cap, cap_until = args.max, datetime.now()
    left: int | None = None
    left_at = datetime.min
    published_at = datetime.now()
    projects = [a for a in lane_args if not a.startswith("-") and "=" not in a][-1:]

    def running() -> list[str]:
        return [n for n in lanes.live_lanes() if not lanes.stop_requested(n)]

    def add(reason: str) -> None:
        nonlocal last_change
        taken = set(lanes.live_lanes())
        name = next(f"L{i}" for i in range(1, 100) if f"L{i}" not in taken)
        start_lane(name, lane_args)
        last_change = datetime.now()
        log(f"+ {name} ({reason})")

    def shrink(reason: str) -> None:
        nonlocal last_change
        live = running()
        if len(live) <= args.min:
            return
        name = max(live, key=lane_number)
        lanes.request_stop(name)
        last_change = datetime.now()
        log(f"- {name} asked to stop after its current MCI ({reason})")

    for _ in range(max(0, args.initial - len(running()))):
        add("initial pool")
        time.sleep(15)
    if args.initial:
        last_change = datetime.now()
    log(f"autoscale up: lanes {running()}, args {' '.join(lane_args)}")

    while True:
        cpu.append(lanes.cpu_percent(10))
        ram.append(lanes.available_ram_gb())
        time.sleep(50)
        now = datetime.now()
        if now - published_at >= timedelta(minutes=args.publish):
            published_at = now
            try:
                from baseline_v1 import drive
                for project in projects:
                    drive.sync("autoscale", project, "periodic publish")
            except Exception as error:  # noqa: BLE001 - lanes still sync on their own
                log(f"periodic publish failed ({type(error).__name__}: {error})")
        live = running()
        n = len(live)
        if now >= cap_until:
            cap = args.max

        # 1. Short of memory or CPU: shrink now, whatever the last change was.
        recent_cpu = list(cpu)[-5:]
        if n > args.min and (ram[-1] < args.ram_floor or
                             (len(recent_cpu) == 5 and min(recent_cpu) > args.cpu_high)):
            shrink(f"host short: RAM {ram[-1]:.1f} GB free, CPU {sum(recent_cpu) / len(recent_cpu):.0f}%")
            cap, cap_until = n - 1, now + timedelta(hours=args.cap_hours)
            pending = None
            continue

        if now - last_change < timedelta(minutes=args.settle):
            continue
        window = timedelta(minutes=min(45.0, args.settle - 5))
        rate = results_per_hour(now - window, now)

        # 2. Judge the lane added last time.
        if pending is not None:
            per_lane = pending["rate"] / max(pending["n"], 1)
            gain = rate - pending["rate"]
            if gain < per_lane / 3:
                log(f"lane {n} added {gain:+.1f}/h on {pending['rate']:.1f}/h (one lane ~{per_lane:.1f}/h): no gain")
                shrink("no throughput gain")
                cap, cap_until = pending["n"], now + timedelta(hours=args.cap_hours)
            else:
                log(f"lane {n} added {gain:+.1f}/h on {pending['rate']:.1f}/h: kept")
            pending = None
            continue

        # 3. Room to grow?
        if now - left_at > timedelta(minutes=15):
            left, left_at = remaining_mcis(lane_args), now
        cpu_avg, ram_min = sum(cpu) / len(cpu), min(ram)
        if (len(cpu) == cpu.maxlen and cpu_avg < args.cpu_low and ram_min > args.ram_grow
                and n < min(cap, args.max) and left is not None and left > 3 * (n + 1)):
            pending = {"rate": rate, "n": n}
            add(f"CPU {cpu_avg:.0f}%, RAM min {ram_min:.1f} GB, {rate:.1f} results/h on {n} lanes, {left} MCIs left")
        elif len(cpu) == cpu.maxlen and cpu_avg > args.cpu_high - 5 and n > args.min:
            shrink(f"CPU {cpu_avg:.0f}% over 15 min")
        if n == 0 and left == 0:
            log("no lanes and no MCIs left; autoscale exits")
            PIDFILE.unlink(missing_ok=True)
            return


if __name__ == "__main__":
    main()
