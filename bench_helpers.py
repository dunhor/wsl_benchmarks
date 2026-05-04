#!/usr/bin/env python3
"""
Shared helpers for container-based benchmarks.

Provides platform detection, container binary resolution, common CLI
arguments (``--cpu``, ``--memory``), and small utility functions so that
each benchmark's ``run-benchmark.py`` does not duplicate boilerplate.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from datetime import date

# ---------------------------------------------------------------------------
# Platform / container binary
# ---------------------------------------------------------------------------

DEFAULT_PLATFORM_CONFIG = {
    "Windows": {"bin": "docker", "name": "windows"},
    "Darwin": {"bin": "container", "name": "mac"},
    "Linux": {"bin": "docker", "name": "linux"},
}


def get_container_bin(platform_config=None):
    """Return the container CLI binary name for the current OS.

    *platform_config* overrides the default mapping when a benchmark
    needs a different binary (e.g. ``wslc`` on Windows).
    """
    config = (platform_config or DEFAULT_PLATFORM_CONFIG).get(platform.system())
    if not config:
        sys.exit(f"Unsupported platform: {platform.system()}")
    bin_name = config["bin"]
    if not shutil.which(bin_name):
        sys.exit(f"Container binary '{bin_name}' not found in PATH")
    return bin_name


def get_platform_name(platform_config=None):
    """Return a short lowercase platform label (``windows``, ``mac``, ``linux``)."""
    config = (platform_config or DEFAULT_PLATFORM_CONFIG).get(platform.system())
    if not config:
        return "unknown"
    return config["name"]


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def run(cmd, check=True, quiet=False):
    """Run *cmd*, optionally printing it and streaming output.

    Returns the process exit code.
    """
    if not quiet:
        print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, check=check, capture_output=quiet)
    # Print output and do a null check
    print(f" -> {result.stdout.decode().strip() if result.stdout else ''}")
    return result.returncode


def run_capture(cmd):
    """Run *cmd* and return its stdout as a string."""
    print(f"  $ {' '.join(cmd)}")

    resultString = subprocess.run(
        cmd, check=True, capture_output=True, text=True,
    ).stdout

    print(f"  → {resultString.strip()}")

    return resultString



# ---------------------------------------------------------------------------
# Session / VM management
# ---------------------------------------------------------------------------

VM_PROCESS_NAME_MAC = "com.apple.Virtualization.VirtualMachine"


def get_vm_process_name():
    """Return the name of the backing VM process for the current platform.

    Returns ``None`` on Linux where there is no separate VM process.
    """
    system = platform.system()
    if system == "Windows":
        username = os.getlogin()
        return f"vmmemwslc-cli-{username}"
    elif system == "Darwin":
        return VM_PROCESS_NAME_MAC
    return None


def start_container_system(bin_name):
    """Start the container VM so that subsequent commands can execute.

    macOS:    runs ``container system start``.
    Windows / Linux: no-op (the VM starts implicitly).
    """
    system = platform.system()
    if system == "Darwin":
        run([bin_name, "system", "start"], check=True)


def stop_container_system(bin_name):
    """Stop the container VM / terminate all sessions for a cold start.

    Windows:  enumerates ``wslc session list`` and terminates each session.
    macOS:    runs ``container system stop``.
    Linux:    no-op (Docker has no separate VM).
    """
    system = platform.system()
    if system == "Windows":
        run([bin_name, "session", "terminate"], check=False)
    elif system == "Darwin":
        run([bin_name, "system", "stop"], check=False)


def wait_for_vm_exit(timeout=30):
    """Block until the backing VM process has exited.

    Returns silently on Linux where there is no separate VM process.
    """
    proc_name = get_vm_process_name()
    if proc_name is None:
        return

    system = platform.system()
    print(f"  Waiting for {proc_name} to exit ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if system == "Windows":
                run_capture(["powershell", "-NoProfile", "-Command",
                             f"Get-Process -Name '{proc_name}' "
                             f"-ErrorAction Stop"])
            else:
                run_capture(["pgrep", "-f", proc_name])
            time.sleep(2)
        except subprocess.CalledProcessError:
            print(f"  {proc_name} is gone.")
            return
    print(f"  Warning: {proc_name} still present after {timeout}s")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def bytes_to_mb(b):
    return round(b / (1024 * 1024), 2)


def print_success(output_file, results):
    """Print a standard success banner and the JSON results."""
    print()
    print("=" * 50)
    print("  Benchmark completed successfully!")
    print(f"  Results written to {output_file}")
    print("=" * 50)
    print(json.dumps(results, indent=2))


# ---------------------------------------------------------------------------
# CLI argument helpers
# ---------------------------------------------------------------------------

def add_common_args(parser):
    """Add ``--cpu`` and ``--memory`` arguments to *parser*.

    These map to ``docker run --cpus`` and ``docker run --memory``
    (or the equivalent flags for the container tool in use).
    """
    parser.add_argument(
        "--cpu", default=None,
        help="CPU limit passed to the container runtime "
             "(e.g. '2' for 2 cores). Maps to --cpus.")
    parser.add_argument(
        "--memory", default=None,
        help="Memory limit passed to the container runtime "
             "(e.g. '4g' for 4 GB). Maps to --memory.")
    return parser


def build_container_run_cmd(bin_name, container_name, image_tag, run_args,
                            *, cpu=None, memory=None, extra_flags=None):
    """Build a ``<bin> run …`` command list.

    *run_args* is a list of strings appended after the image tag
    (e.g. the entrypoint override and its arguments).

    *extra_flags* is an optional list of additional flags inserted
    before the image tag (e.g. ``['-d']``, ``['-p', '5201:5201']``).
    """
    cmd = [bin_name, "run", "--name", container_name]
    if cpu:
        cmd += ["--cpus", str(cpu)]
    if memory:
        cmd += ["--memory", str(memory)]
    if extra_flags:
        cmd += extra_flags
    cmd.append(image_tag)
    cmd += run_args
    return cmd


def today_iso():
    """Return today's date as an ISO-8601 string."""
    return date.today().isoformat()
