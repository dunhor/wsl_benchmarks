#!/usr/bin/env python3
"""
Disk space benchmark for container technology.

Measures host disk space impact of building, running, and cleaning up
a container that compiles the Linux kernel.
"""

import argparse
import json
import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bench_helpers import (
    add_common_args, build_container_run_cmd, bytes_to_mb, get_container_bin,
    get_platform_name, print_success, run, run_capture,
    start_container_system, stop_container_system, today_iso, wait_for_vm_exit,
)

IMAGE_TAG = "disk-space-bench:latest"
CONTAINER_NAME = "disk-space-bench-run"
KERNEL_VERSION = "6.1.90"
KERNEL_URL = f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{KERNEL_VERSION}.tar.xz"

PLATFORM_CONFIG = {
    "Windows": {"bin": "wslc", "name": "windows"},
    "Darwin": {"bin": "container", "name": "mac"},
    "Linux": {"bin": "docker", "name": "linux"},
}


def _get_directory_size(directory: Path) -> int:
    """Get actual on-disk size of all files in a directory recursively.

    Uses st_blocks to measure real disk usage (handles sparse files and
    APFS clones correctly), matching what ``du`` reports.
    """
    total = 0
    try:
        for root, _dirs, files in os.walk(directory):
            for f in files:
                try:
                    st = os.stat(os.path.join(root, f))
                    total += st.st_blocks * 512  # st_blocks is in 512-byte units
                except OSError:
                    pass
    except OSError:
        pass
    return total


def get_disk_space_used():
    """Return bytes of disk consumed by container storage."""
    system = platform.system()
    if system == "Windows":
        username = os.getlogin()
        vhdx = Path(f"C:/Users/{username}/AppData/Local/wslc/sessions"
                     f"/wslc-cli-{username}/storage.vhdx")
        if not vhdx.exists():
            sys.exit(f"VHDX not found: {vhdx}")
        return vhdx.stat().st_size
    elif system == "Darwin":
        container_dir = Path.home() / "Library" / "Application Support" / "com.apple.container"
        if not container_dir.exists():
            sys.exit(f"Container storage not found: {container_dir}")
        return _get_directory_size(container_dir)
    else:
        # Linux: measure /var/lib/docker
        docker_dir = Path("/var/lib/docker")
        if docker_dir.exists():
            return _get_directory_size(docker_dir)
        result = run_capture(["df", "-k", "/"])
        line = result.strip().splitlines()[-1]
        used_kb = int(line.split()[2])
        return used_kb * 1024


def container_exec(bin_name, cmd_str):
    """Run a bash command inside the running container."""
    run([bin_name, "exec", CONTAINER_NAME, "bash", "-c", cmd_str])


def main():
    parser = argparse.ArgumentParser(
        description="Disk space benchmark (Linux kernel compile in container)")
    add_common_args(parser)
    args = parser.parse_args()

    bin_name = get_container_bin(PLATFORM_CONFIG)
    plat = get_platform_name(PLATFORM_CONFIG)
    today = today_iso()
    script_dir = Path(__file__).resolve().parent
    output_file = script_dir / f"{plat}-disk-space-{today}.json"

    print(f"Platform: {plat} | Binary: {bin_name}")
    print(f"Kernel:   Linux {KERNEL_VERSION}")
    print()

    run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)

    try:
        print("=== Step 0: Resetting container storage ===")
        if platform.system() == "Windows":
            username = os.getlogin()
            vhdx = Path(f"C:/Users/{username}/AppData/Local/wslc/sessions"
                        f"/wslc-cli-{username}/storage.vhdx")
            stop_container_system(bin_name)
            wait_for_vm_exit(timeout=60)
            if vhdx.exists():
                print(f"  Deleting {vhdx} ...")
                vhdx.unlink()
                print(f"  Deleted.")
            else:
                print(f"  VHDX not found, skipping delete.")
            print(f"  Recreating storage with image ls ...")
            run([bin_name, "image", "ls"])
        print()

        print("=== Step 1: Building container image ===")
        start_container_system(bin_name)
        run([bin_name, "build", "-t", IMAGE_TAG, str(script_dir)])

        disk_after_build = get_disk_space_used()
        print(f"Disk used after image build: {bytes_to_mb(disk_after_build)} MB\n")

        print("=== Step 2: Compiling Linux kernel ===")
        cmd = build_container_run_cmd(
            bin_name, CONTAINER_NAME, IMAGE_TAG,
            ["tail", "-f", "/dev/null"],
            cpu=args.cpu, memory=args.memory, extra_flags=["-d"],
        )
        run(cmd)

        build_cmd = (
            f"cd /build"
            f" && wget -q {KERNEL_URL}"
            f" && tar xf linux-{KERNEL_VERSION}.tar.xz"
            f" && cd linux-{KERNEL_VERSION}"
            f" && make defconfig"
            f" && make -j$(nproc)"
        )
        container_exec(bin_name, build_cmd)

        disk_after_compile = get_disk_space_used()
        print(f"Disk used after kernel compile: {bytes_to_mb(disk_after_compile)} MB\n")

        print("=== Step 3: Cleaning kernel build files ===")
        container_exec(bin_name, "rm -rf /build/*")

        disk_after_cleanup = get_disk_space_used()
        print(f"Disk used after kernel cleanup: {bytes_to_mb(disk_after_cleanup)} MB\n")

        print("=== Step 4: Deleting container and image ===")
        run([bin_name, "rm", "-f", CONTAINER_NAME])
        run([bin_name, "rmi", IMAGE_TAG], check=False)

        disk_after_delete = get_disk_space_used()
        print(f"Disk used after container/image delete: {bytes_to_mb(disk_after_delete)} MB\n")

        results = {
            "platform": plat,
            "date": today,
            "container_tool": bin_name,
            "kernel_version": KERNEL_VERSION,
            "disk_space_mb": {
                "after_image_build": bytes_to_mb(disk_after_build),
                "after_kernel_compile": bytes_to_mb(disk_after_compile),
                "after_kernel_cleanup": bytes_to_mb(disk_after_cleanup),
                "after_container_delete": bytes_to_mb(disk_after_delete),
                "units": "MB",
            },
            "disk_space_diff_mb": {
                "kernel_compile_added": round(bytes_to_mb(disk_after_compile) - bytes_to_mb(disk_after_build), 2),
                "kernel_cleanup_reclaimed": round(bytes_to_mb(disk_after_compile) - bytes_to_mb(disk_after_cleanup), 2),
                "container_delete_diff": round(bytes_to_mb(disk_after_delete) - bytes_to_mb(disk_after_build), 2),
                "units": "MB",
            },
        }

        output_file.write_text(json.dumps(results, indent=2) + "\n")
        print_success(output_file, results)

    finally:
        run([bin_name, "rm", "-f", CONTAINER_NAME], check=False, quiet=True)


if __name__ == "__main__":
    main()
