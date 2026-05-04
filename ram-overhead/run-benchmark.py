#!/usr/bin/env python3
"""
RAM overhead benchmark for containers.

Measures the host-side RAM cost of running an idle Alpine container.

Platform strategies:
  - Windows: tracks WorkingSet64 of the ``vmmemwslc-cli-{user}`` process.
  - macOS:   tracks RSS of ``com.apple.Virtualization.VirtualMachine``.
  - Linux:   tracks cgroup memory usage for the container.

Steps:
  1. Record baseline VM RAM (or 0 if the VM process isn't running).
  2. Build a minimal Alpine image.
  3. Run the container (sleep) and measure VM RAM.
  4. Clean up and confirm RAM is released.
"""

import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench_helpers import (
    add_common_args, build_container_run_cmd, bytes_to_mb, get_container_bin,
    get_platform_name, get_vm_process_name, print_success, run, run_capture,
    start_container_system, stop_container_system, today_iso, wait_for_vm_exit,
)

IMAGE_TAG = "ram-overhead-bench:latest"
CONTAINER_NAME = "ram-overhead-bench-run"
STABILIZATION_DELAY = 5   # seconds to wait after state changes
SAMPLES_PER_MEASUREMENT = 3

PLATFORM_CONFIG = {
    "Windows": {"bin": "wslc", "name": "windows"},
    "Darwin":  {"bin": "container", "name": "mac"},
    "Linux":   {"bin": "docker", "name": "linux"},
}


# ---------------------------------------------------------------------------
# RAM measurement — platform-dispatched
# ---------------------------------------------------------------------------

def _sample_rss_bytes(samples=SAMPLES_PER_MEASUREMENT):
    """Take *samples* RSS readings of the VM process and return the median.

    Dispatches to the correct platform helper.  Returns 0 when no VM
    process is found.
    """
    system = platform.system()
    if system == "Windows":
        return _sample_windows(samples)
    elif system == "Darwin":
        return _sample_darwin(samples)
    else:
        return _sample_linux(samples)


# -- macOS -----------------------------------------------------------------


def _find_vm_pid_mac():
    """Return PID of the Virtualization.framework VM process, or None."""
    try:
        out = run_capture(["pgrep", "-f", get_vm_process_name()])
        pids = out.strip().splitlines()
        return int(pids[0]) if pids else None
    except (subprocess.CalledProcessError, ValueError):
        return None


def _rss_for_pid_mac(pid):
    """Return RSS in bytes for a given PID on macOS."""
    out = run_capture(["ps", "-o", "rss=", "-p", str(pid)])
    return int(out.strip()) * 1024  # ps reports RSS in KB


def _sample_darwin(samples):
    readings = []
    for _ in range(samples):
        pid = _find_vm_pid_mac()
        if pid:
            readings.append(_rss_for_pid_mac(pid))
        else:
            readings.append(0)
        if len(readings) < samples:
            time.sleep(1)
    median = int(statistics.median(readings))
    print(f"  VM RSS: {bytes_to_mb(median)} MB "
          f"(samples: {[bytes_to_mb(s) for s in readings]})")
    return median


def _get_host_total_ram_mac():
    out = run_capture(["sysctl", "-n", "hw.memsize"])
    return round(int(out.strip()) / (1024 * 1024), 2)


# -- Windows ---------------------------------------------------------------


def _sample_windows(samples):
    proc_name = get_vm_process_name()
    readings = []
    for _ in range(samples):
        try:
            ps_cmd = (
                f"(Get-Process -Name '{proc_name}' "
                f"-ErrorAction Stop).WorkingSet64"
            )
            out = run_capture(["powershell", "-NoProfile", "-Command", ps_cmd])
            readings.append(int(out.strip()))
        except (subprocess.CalledProcessError, ValueError):
            readings.append(0)
        if len(readings) < samples:
            time.sleep(1)
    median = int(statistics.median(readings))
    print(f"  vmmem WorkingSet64: {bytes_to_mb(median)} MB "
          f"(samples: {[bytes_to_mb(s) for s in readings]})")
    return median


def _get_host_total_ram_windows():
    ps_cmd = "(Get-CimInstance Win32_OperatingSystem).TotalVisibleMemorySize"
    out = run_capture(["powershell", "-NoProfile", "-Command", ps_cmd])
    return round(int(out.strip()) / 1024, 2)


# -- Linux -----------------------------------------------------------------

def _sample_linux(samples):
    """On Linux, read container cgroup memory or fall back to /proc/meminfo."""
    readings = []
    for _ in range(samples):
        try:
            out = run_capture(["docker", "stats", "--no-stream",
                               "--format", "{{.MemUsage}}", CONTAINER_NAME])
            # Output like "123.4MiB / 7.77GiB"
            mem_str = out.strip().split("/")[0].strip()
            match = re.match(r"([\d.]+)\s*(GiB|MiB|KiB|B)", mem_str)
            if match:
                val = float(match.group(1))
                unit = match.group(2)
                multiplier = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
                readings.append(int(val * multiplier.get(unit, 1)))
            else:
                readings.append(0)
        except (subprocess.CalledProcessError, ValueError):
            readings.append(0)
        if len(readings) < samples:
            time.sleep(1)
    median = int(statistics.median(readings))
    print(f"  Container mem: {bytes_to_mb(median)} MB "
          f"(samples: {[bytes_to_mb(s) for s in readings]})")
    return median


def _get_host_total_ram_linux():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024, 2)
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_host_total_ram_mb():
    system = platform.system()
    if system == "Windows":
        return _get_host_total_ram_windows()
    elif system == "Darwin":
        return _get_host_total_ram_mac()
    else:
        return _get_host_total_ram_linux()


def get_vm_process_label():
    return get_vm_process_name() or "docker-stats"


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="RAM overhead benchmark (idle container VM memory)")
    add_common_args(parser)
    args = parser.parse_args()

    bin_name = get_container_bin(PLATFORM_CONFIG)
    plat = get_platform_name(PLATFORM_CONFIG)
    today = today_iso()
    script_dir = Path(__file__).resolve().parent
    output_file = script_dir / f"{plat}-ram-overhead-{today}.json"
    vm_label = get_vm_process_label()

    host_total_mb = get_host_total_ram_mb()
    print(f"Platform: {plat} | Binary: {bin_name}")
    print(f"Host RAM: {host_total_mb} MB")
    print(f"VM process: {vm_label}")
    print()

    try:
        print("=== Step 1: Measuring baseline VM RAM ===")
        run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)
        stop_container_system(bin_name)
        wait_for_vm_exit()
        baseline_bytes = _sample_rss_bytes()
        baseline_mb = bytes_to_mb(baseline_bytes)
        print(f"  → Baseline VM RAM: {baseline_mb} MB\n")

        print("=== Step 2: Building container image ===")
        start_container_system(bin_name)
        run([bin_name, "build", "-t", IMAGE_TAG, str(script_dir)])
        stop_container_system(bin_name)
        wait_for_vm_exit()
        print()

        print("=== Step 3: Running idle container ===")
        start_container_system(bin_name)
        cmd = build_container_run_cmd(
            bin_name, CONTAINER_NAME, IMAGE_TAG,
            ["sleep", "120"],
            cpu=args.cpu, memory=args.memory, extra_flags=["-d"],
        )
        run(cmd)
        print(f"  Waiting {STABILIZATION_DELAY}s for VM to stabilize ...")
        time.sleep(STABILIZATION_DELAY)

        container_bytes = _sample_rss_bytes()
        container_mb = bytes_to_mb(container_bytes)
        overhead_mb = bytes_to_mb(container_bytes - baseline_bytes)
        print(f"  → VM RAM with container: {container_mb} MB")
        print(f"  → Overhead vs baseline: {overhead_mb} MB\n")

        print("=== Step 4: Cleaning up ===")
        run([bin_name, "rm", "-f", CONTAINER_NAME])
        run([bin_name, "rmi", IMAGE_TAG], check=False, quiet=True)
        stop_container_system(bin_name)
        print(f"  Waiting {STABILIZATION_DELAY}s for cleanup ...")
        time.sleep(STABILIZATION_DELAY)

        post_cleanup_bytes = _sample_rss_bytes()
        post_cleanup_mb = bytes_to_mb(post_cleanup_bytes)
        print(f"  → Post-cleanup VM RAM: {post_cleanup_mb} MB\n")

        results = {
            "platform": plat,
            "date": today,
            "container_tool": bin_name,
            "host_total_ram": {
                "value": host_total_mb,
                "units": "MB",
            },
            "vm_process": vm_label,
            "ram_overhead": {
                "baseline": baseline_mb,
                "with_idle_container": container_mb,
                "overhead": overhead_mb,
                "post_cleanup": post_cleanup_mb,
                "units": "MB",
            },
            "stabilization_delay": {
                "value": STABILIZATION_DELAY,
                "units": "seconds",
            },
            "samples_per_measurement": SAMPLES_PER_MEASUREMENT,
        }

        output_file.write_text(json.dumps(results, indent=2) + "\n")
        print_success(output_file, results)

    finally:
        run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)


if __name__ == "__main__":
    main()
