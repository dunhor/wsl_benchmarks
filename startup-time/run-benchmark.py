#!/usr/bin/env python3
"""
Cold-start timing benchmark for container technology.

Measures the wall-clock time to launch a container from a fully stopped
VM state ("cold start").

Platform strategies:
  - Windows (wslc):    terminates all sessions → times ``wslc run``.
  - macOS (container): ``container system stop`` → times ``container run``.
  - Linux (docker):    times ``docker run`` (no separate VM to cold-start).

Steps:
  1. Build a minimal Alpine image.
  2. For each iteration: stop the container system, wait for the VM to
     exit, then time a ``run --rm`` command.
  3. Report min / max / average / median across iterations.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench_helpers import (
    add_common_args, build_container_run_cmd, get_container_bin,
    get_platform_name, print_success, run, start_container_system,
    stop_container_system, today_iso, wait_for_vm_exit,
)

IMAGE_TAG = "startup-time-bench:latest"
CONTAINER_NAME = "startup-time-bench-run"
DEFAULT_RUNS = 5

PLATFORM_CONFIG = {
    "Windows": {"bin": "wslc", "name": "windows"},
    "Darwin":  {"bin": "container", "name": "mac"},
    "Linux":   {"bin": "docker", "name": "linux"},
}


def time_cold_start(bin_name, cpu=None, memory=None):
    """Stop the VM, then time a container run.  Return elapsed seconds."""
    run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)

    print("  Stopping container system ...")
    stop_container_system(bin_name)
    wait_for_vm_exit(timeout=60)

    cmd = build_container_run_cmd(
        bin_name, CONTAINER_NAME, IMAGE_TAG,
        ["echo", "ready"],
        cpu=cpu, memory=memory, extra_flags=["--rm"],
    )

    print("Waiting a few seconds for the system to settle ...")
    time.sleep(10)

    start = time.perf_counter()
    start_container_system(bin_name)
    run(cmd)
    elapsed = time.perf_counter() - start
    print(f"  → Cold start: {elapsed:.3f}s\n")
    return elapsed


def main():
    parser = argparse.ArgumentParser(
        description="Cold-start timing benchmark (VM boot + container launch)")
    parser.add_argument(
        "--runs", "-r", type=int, default=DEFAULT_RUNS,
        help=f"Number of cold-start iterations (default: {DEFAULT_RUNS})")
    add_common_args(parser)
    args = parser.parse_args()

    bin_name = get_container_bin(PLATFORM_CONFIG)
    plat = get_platform_name(PLATFORM_CONFIG)
    today = today_iso()
    script_dir = Path(__file__).resolve().parent
    output_file = script_dir / f"{plat}-startup-time-{today}.json"

    print(f"Platform: {plat} | Binary: {bin_name}")
    print(f"Iterations: {args.runs}")
    print()

    try:
        print("=== Step 1: Building container image ===")
        start_container_system(bin_name)
        run([bin_name, "build", "-t", IMAGE_TAG, str(script_dir)])
        print()

        print(f"=== Step 2: Cold-start timing ({args.runs} iterations) ===")
        timings = []
        for i in range(1, args.runs + 1):
            print(f"--- Iteration {i}/{args.runs} ---")
            elapsed = time_cold_start(bin_name, cpu=args.cpu, memory=args.memory)
            timings.append(round(elapsed, 3))

        avg = round(statistics.mean(timings), 3)
        med = round(statistics.median(timings), 3)
        mn = min(timings)
        mx = max(timings)
        stdev = round(statistics.stdev(timings), 3) if len(timings) > 1 else 0.0

        print(f"  Timings: {timings}")
        print(f"  Avg: {avg}s | Median: {med}s | Min: {mn}s | Max: {mx}s | Stdev: {stdev}s\n")

        print("=== Step 3: Cleaning up ===")
        run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)
        run([bin_name, "rmi", IMAGE_TAG], check=False, quiet=True)
        print()

        results = {
            "platform": plat,
            "date": today,
            "container_tool": bin_name,
            "iterations": args.runs,
            "cold_start_seconds": {
                "timings": timings,
                "average": avg,
                "median": med,
                "min": mn,
                "max": mx,
                "stdev": stdev,
                "units": "seconds",
            },
        }

        output_file.write_text(json.dumps(results, indent=2) + "\n")
        print_success(output_file, results)

    finally:
        run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)
        run([bin_name, "rmi", IMAGE_TAG], check=False, quiet=True)


if __name__ == "__main__":
    main()
