#!/usr/bin/env python3
"""
File I/O performance benchmark for container technology.

Builds a Docker image containing the file_io_benchmark suite (sequential
read/write, random I/O, metadata ops, npm/pip/git installs) and runs it
inside a container.  Results are written to a JSON file in this directory.

All ``file_io_benchmark.py`` options are forwarded to the container.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench_helpers import (
    add_common_args, build_container_run_cmd, get_container_bin,
    get_platform_name, print_success, run, start_container_system,
    today_iso,
)

IMAGE_TAG = "file-perf-bench:latest"
CONTAINER_NAME = "file-perf-bench-run"
CONTAINER_OUTPUT_DIR = "/out"

PLATFORM_CONFIG = {
    "Windows": {"bin": "wslc", "name": "windows"},
    "Darwin":  {"bin": "container", "name": "mac"},
    "Linux":   {"bin": "docker", "name": "linux"},
}


def main():
    parser = argparse.ArgumentParser(
        description="File I/O performance benchmark (runs inside a container)",
        epilog="Examples:\n"
               "  %(prog)s                              # Run all tests with defaults\n"
               "  %(prog)s my-run --tests pip npm        # Run only pip & npm tests\n"
               "  %(prog)s my-run --runs 3 --cpu 2       # 3 iterations, 2 CPU cores\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # file_io_benchmark.py arguments (forwarded into the container)
    parser.add_argument("test_name", nargs="?", default="default",
                        help='Name for this test run (default: "default")')
    parser.add_argument("--tests", "-t", nargs="+",
                        choices=["seq_write", "seq_read", "rand_write",
                                 "rand_read", "metadata", "npm", "pip", "git"],
                        help="Select specific tests to run (default: all)")
    parser.add_argument("--runs", "-r", type=int, default=2,
                        help="Number of benchmark iterations (default: 2)")
    add_common_args(parser)
    args = parser.parse_args()

    bin_name = get_container_bin(PLATFORM_CONFIG)
    plat = get_platform_name(PLATFORM_CONFIG)
    today = today_iso()
    script_dir = Path(__file__).resolve().parent
    output_file = script_dir / f"{plat}-file-perf-{today}.json"

    print(f"Platform: {plat} | Binary: {bin_name}")
    print(f"Test name: {args.test_name}")
    if args.tests:
        print(f"Selected tests: {', '.join(args.tests)}")
    else:
        print("Running all tests")
    print(f"Iterations: {args.runs}")
    print()

    start_container_system(bin_name)
    run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)

    try:
        print("=== Step 1: Building container image ===")
        run([bin_name, "build", "-t", IMAGE_TAG, str(script_dir)])
        print()

        print("=== Step 2: Running file I/O benchmark ===")
        # Mount a host directory into the container for results output
        host_output_dir = script_dir / "out"
        host_output_dir.mkdir(exist_ok=True)
        # Use :z on Linux to relabel the volume for SELinux (Fedora/RHEL)
        vol = f"{host_output_dir}:{CONTAINER_OUTPUT_DIR}:z" if plat == "linux" else f"{host_output_dir}:{CONTAINER_OUTPUT_DIR}"

        inner_cmd = [
            "python", "file_io_benchmark.py", args.test_name,
            "--working-folder", CONTAINER_OUTPUT_DIR,
            "--runs", str(args.runs),
        ]
        if args.tests:
            inner_cmd += ["--tests"] + args.tests

        cmd = build_container_run_cmd(
            bin_name, CONTAINER_NAME, IMAGE_TAG, inner_cmd,
            cpu=args.cpu, memory=args.memory,
            extra_flags=["-e", "PYTHONUNBUFFERED=1",
                         "-v", vol],
        )
        print(f"  $ {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False, text=True)

        if result.returncode != 0:
            sys.exit(f"Benchmark failed with exit code {result.returncode}")

        print("\n=== Step 3: Collecting results ===")
        # The benchmark writes to the mounted volume
        container_json = host_output_dir / f"benchmark_results_{args.test_name}.json"
        if not container_json.exists():
            sys.exit(f"Results file not found: {container_json}")

        # Copy to the canonical output location and clean up temp dir
        import shutil
        shutil.copy2(container_json, output_file)
        shutil.rmtree(host_output_dir, ignore_errors=True)

        print()
        print("=== Step 4: Cleaning up ===")
        run([bin_name, "rm", "-f", CONTAINER_NAME])
        run([bin_name, "rmi", IMAGE_TAG], check=False, quiet=True)
        print()

        results = json.loads(output_file.read_text())
        print_success(output_file, results)

    finally:
        run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)
        run([bin_name, "rmi", IMAGE_TAG], check=False, quiet=True)


if __name__ == "__main__":
    main()
