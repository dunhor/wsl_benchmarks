# File I/O Performance Benchmark

A Python script to test file system I/O performance with various file sizes and operations, including real-world package manager workloads.

## Quick Start

### Docker

**With shared volume (tests run on mounted host directory):**
```bash
docker build -t test-image . && docker run --rm -it -v "$(pwd)/out:/out:z" test-image python file_io_benchmark.py wsl-ext4 --working-folder /out
```

**Without shared volume (tests run entirely in container, then copy results):**
```bash
mkdir -p save && docker build -t test-image . && docker run --rm -v $(pwd)/save:/save:z test-image bash -c "mkdir -p out && python file_io_benchmark.py wsl-ext4 --working-folder out && cp out/benchmark_results_wsl-ext4.json /save/"
```

### First-time setup (with internet connection):

```bash
# Install dependencies and create offline caches
python3 setup_caches.py
```

### Run benchmarks (works offline):

```bash
python3 file_io_benchmark.py
```

## What It Tests

- **Sequential Read/Write**: Throughput with different file sizes (10KB to 500MB)
- **Random Read/Write**: IOPS and latency with 4KB blocks
- **Metadata Operations**: File creation and deletion speed
- **Real-World Tests**:
  - npm install (offline) - Installs a large set of popular packages including React, Angular, Vue, Next.js, TypeScript, Webpack, and more
  - pip install (offline) - Installs requests, flask, and dependencies (~12 packages)

## Setup

The setup script downloads packages once to create offline caches:

```bash
python3 setup_caches.py
```

This creates a `benchmark_cache/` directory with:
- `npm_cache/` - npm packages and metadata
- `npm_test_project/` - package.json and package-lock.json
- `pip_wheels/` - Python wheel files

**Note**: You only need to run setup once. After that, benchmarks work completely offline.

## Configuration

Edit `main()` in `file_io_benchmark.py`:

```python
data_size_gb = 2.0  # Total data per test
num_runs = 5        # Number of iterations
```

## Output

- Console output with per-run and aggregated statistics (mean ± std dev)
- `benchmark_results.json` with detailed results from all runs
