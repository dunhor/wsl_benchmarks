#!/usr/bin/env python3
"""
CPU stress benchmark for container technology.

Runs the Phoronix Test Suite smallpt ray-tracing benchmark inside a
container and records the average completion time.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench_helpers import (
    add_common_args, build_container_run_cmd, get_container_bin,
    get_platform_name, print_success, run, start_container_system,
    today_iso,
)

IMAGE_TAG = "cpu-stress-bench:latest"
CONTAINER_NAME = "cpu-stress-bench-run"
TEST_RUNS = 1

PLATFORM_CONFIG = {
    "Windows": {"bin": "wslc", "name": "windows"},
    "Darwin":  {"bin": "container", "name": "mac"},
    "Linux":   {"bin": "docker", "name": "linux"},
}


def parse_average(output):
    """Parse the 'Average:' line from phoronix output."""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Average:"):
            match = re.match(r"Average:\s+([\d.]+)\s+(.*)", line)
            if match:
                return float(match.group(1)), match.group(2).strip()
    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="CPU stress benchmark (Phoronix pts/smallpt)")
    add_common_args(parser)
    args = parser.parse_args()

    bin_name = get_container_bin(PLATFORM_CONFIG)
    plat = get_platform_name(PLATFORM_CONFIG)
    today = today_iso()
    script_dir = Path(__file__).resolve().parent
    output_file = script_dir / f"{plat}-cpu-stress-{today}.json"

    print(f"Platform: {plat} | Binary: {bin_name}")
    print(f"Benchmark: pts/smallpt (ray tracing)")
    print(f"Test runs: {TEST_RUNS}")
    print()

    start_container_system(bin_name)
    run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)

    try:
        print("=== Step 1: Building container image ===")
        run([bin_name, "build", "-t", IMAGE_TAG, str(script_dir)])
        print()

        print("=== Step 2: Running CPU stress benchmark (pts/smallpt) ===")
        print("  This may take several minutes ...\n")
        bench_cmd = (
            "printf 'y\\nn\\nn\\nn\\nn\\nn\\ny\\n' "
            "| ./phoronix-test-suite batch-setup > /dev/null 2>&1 && "
            "PTS_SILENT_MODE=1 "
            "TEST_RESULTS_NAME=smallpt "
            "TEST_RESULTS_IDENTIFIER=ci "
            f"TEST_RUNS={TEST_RUNS} "
            "./phoronix-test-suite batch-benchmark pts/smallpt"
        )
        cmd = build_container_run_cmd(
            bin_name, CONTAINER_NAME, IMAGE_TAG,
            ["/bin/bash", "-c", bench_cmd],
            cpu=args.cpu, memory=args.memory,
        )
        print(f"  $ {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"  stdout: {result.stdout[-500:]}")
            print(f"  stderr: {result.stderr[-500:]}")
            sys.exit(f"Benchmark failed with exit code {result.returncode}")

        output = result.stdout
        print(f"  Raw output:\n{output}")

        average_value, average_unit = parse_average(output)
        if average_value is None:
            sys.exit(f"Could not parse Average from output:\n{output}")

        print(f"  → Average: {average_value} {average_unit}\n")

        print("=== Step 3: Cleaning up ===")
        run([bin_name, "rm", "-f", CONTAINER_NAME])
        run([bin_name, "rmi", IMAGE_TAG], check=False, quiet=True)
        print()

        results = {
            "platform": plat,
            "date": today,
            "container_tool": bin_name,
            "benchmark": "pts/smallpt",
            "test_runs": TEST_RUNS,
            "cpu_benchmark": {
                "average_value": average_value,
                "units": average_unit,
            },
        }

        output_file.write_text(json.dumps(results, indent=2) + "\n")
        print_success(output_file, results)

    finally:
        run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)
        run([bin_name, "rmi", IMAGE_TAG], check=False, quiet=True)


if __name__ == "__main__":
    main()
