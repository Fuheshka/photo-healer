#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Automated build pipeline for Photo Healer standalone Windows binaries.

Builds standalone executables using PyInstaller:
  - photo-healer.exe     : Ultra-compact CLI (< 15 MB)
  - photo-healer-gui.exe : Desktop GUI with PySide6 & dark theme

Features:
  1. Version synchronization from pyproject.toml
  2. Artifact cleaning (build/, dist/)
  3. Spec-driven compilation for CLI and GUI
  4. Post-build integrity verification (--version, --help, process sanity)
  5. SHA-256 checksum generation (checksums.txt)
  6. Release zip archive packaging with README & License
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

# Python 3.11+ standard library for reading pyproject.toml
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore


# Configure Windows console streams for UTF-8 output
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


DEFAULT_LICENSE_TEXT = """MIT License

Copyright (c) 2026 Fuheshka

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def log(msg: str, symbol: str = "ℹ️") -> None:
    """Print formatted console log message with fallback for restrictive codepages."""
    try:
        print(f"[{time.strftime('%H:%M:%S')}] {symbol} {msg}")
    except UnicodeEncodeError:
        safe_sym = {"ℹ️": "[INFO]", "✅": "[OK]", "⚠️": "[WARN]", "❌": "[ERR]"}.get(symbol, "[*]")
        print(f"[{time.strftime('%H:%M:%S')}] {safe_sym} {msg}")


def log_success(msg: str) -> None:
    log(msg, symbol="✅")


def log_warn(msg: str) -> None:
    log(msg, symbol="⚠️")


def log_error(msg: str) -> None:
    log(msg, symbol="❌")


def format_bytes(num_bytes: int) -> str:
    """Format bytes to human-readable size string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} TB"


def get_project_root() -> Path:
    """Resolve repository root directory from script location."""
    return Path(__file__).resolve().parent.parent


def extract_version(project_root: Path) -> str:
    """Extract project version from pyproject.toml."""
    pyproject_file = project_root / "pyproject.toml"
    if not pyproject_file.is_file():
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject_file}")

    if tomllib is not None:
        with open(pyproject_file, "rb") as f:
            data = tomllib.load(f)
        version = data.get("project", {}).get("version")
        if version:
            return str(version)

    # Fallback to simple regex/line parser if tomllib is missing
    with open(pyproject_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("version") and "=" in line:
                parts = line.split("=", 1)
                return parts[1].strip(" \"'")

    raise ValueError("Could not determine project version from pyproject.toml")


def clean_artifacts(project_root: Path, dist_dir: Path) -> None:
    """Clean intermediate build directories and previous artifacts safely on Windows."""
    build_dir = project_root / "build"
    log("Cleaning temporary build artifacts...")

    if build_dir.exists():
        for item in build_dir.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
            except Exception:
                pass
        log_success(f"Cleaned build directory: {build_dir}")
    else:
        build_dir.mkdir(parents=True, exist_ok=True)

    dist_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Clean individual executables and archives in dist, preserving custom user files if any
        for item in dist_dir.glob("photo-healer*"):
            if item.is_file():
                item.unlink(missing_ok=True)
        for item in dist_dir.glob("*.zip"):
            item.unlink(missing_ok=True)
        for item in dist_dir.glob("checksums*.txt"):
            item.unlink(missing_ok=True)
        log_success(f"Cleaned previous outputs in dist directory: {dist_dir}")
    except Exception as e:
        log_warn(f"Failed to clean outputs in {dist_dir}: {e}")


def run_pyinstaller_build(spec_file: Path, clean: bool = True) -> None:
    """Run PyInstaller build for the specified .spec file."""
    if not spec_file.is_file():
        raise FileNotFoundError(f"Specification file not found: {spec_file}")

    cmd = [sys.executable, "-m", "PyInstaller", str(spec_file), "--noconfirm"]
    if clean:
        cmd.append("--clean")

    log(f"Compiling with PyInstaller: {spec_file.name}...")
    start_time = time.perf_counter()

    result = subprocess.run(
        cmd,
        cwd=spec_file.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    elapsed = time.perf_counter() - start_time

    if result.returncode != 0:
        log_error(f"PyInstaller build failed for {spec_file.name} (exit code {result.returncode})")
        if result.stderr:
            sys.stderr.write(result.stderr[-2000:] + "\n")
        raise RuntimeError(f"PyInstaller failed to build {spec_file.name}")

    log_success(f"Successfully compiled {spec_file.name} in {elapsed:.1f}s")


def verify_cli_binary(exe_path: Path, expected_version: str) -> bool:
    """Verify CLI binary integrity, version string and command execution."""
    log(f"Verifying CLI binary: {exe_path.name}...")
    if not exe_path.is_file():
        log_error(f"CLI binary not found: {exe_path}")
        return False

    size_bytes = exe_path.stat().st_size
    log(f"CLI executable size: {format_bytes(size_bytes)} ({size_bytes:,} bytes)")
    if size_bytes > 15 * 1024 * 1024:
        log_warn(f"CLI binary size ({format_bytes(size_bytes)}) exceeds 15 MB threshold!")
    else:
        log_success(f"CLI binary size satisfies Ponytail budget (< 15 MB)")

    # 1. Test --version
    cmd_version = [str(exe_path), "--version"]
    res_version = subprocess.run(
        cmd_version,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if res_version.returncode != 0 or expected_version not in res_version.stdout:
        log_error(
            f"CLI --version check failed. Output: {res_version.stdout.strip()} (expected {expected_version})"
        )
        return False
    log_success(f"CLI --version verified: {res_version.stdout.strip()}")

    # 2. Test triage --help
    cmd_help = [str(exe_path), "triage", "--help"]
    res_help = subprocess.run(
        cmd_help,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if res_help.returncode != 0 or "triage" not in res_help.stdout:
        log_error("CLI 'triage --help' check failed.")
        return False
    log_success("CLI 'triage --help' verified successfully")

    return True


def verify_gui_binary(exe_path: Path) -> bool:
    """Verify GUI binary integrity, size and runtime launch sanity."""
    log(f"Verifying GUI binary: {exe_path.name}...")
    if not exe_path.is_file():
        log_error(f"GUI binary not found: {exe_path}")
        return False

    size_bytes = exe_path.stat().st_size
    log(f"GUI executable size: {format_bytes(size_bytes)} ({size_bytes:,} bytes)")

    # Test process startup sanity: launch process and ensure it doesn't immediately crash (< 0.5s)
    # due to missing Qt plugins or DLL initialization failures.
    log("Testing GUI runtime startup sanity...")
    try:
        proc = subprocess.Popen(
            [str(exe_path), "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Allow process to load libraries and initialize for 2.5 seconds
        time.sleep(2.5)
        poll_status = proc.poll()
        if poll_status is not None and poll_status not in (0,):
            # Process crashed immediately on startup
            _, stderr = proc.communicate()
            log_error(f"GUI binary crashed on startup (code {poll_status}): {stderr.strip()}")
            return False

        # Terminate cleanly if still running
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

        log_success("GUI binary initialized runtime dependencies cleanly")
        return True
    except Exception as e:
        log_error(f"Error while validating GUI binary: {e}")
        return False


def calculate_sha256(file_path: Path) -> str:
    """Calculate SHA-256 hexadecimal digest of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def generate_checksums_file(files: list[Path], output_path: Path) -> Path:
    """Generate standard sha256 checksums file."""
    log(f"Generating SHA-256 checksums file: {output_path.name}...")
    lines = []
    for file_path in files:
        if file_path.is_file():
            checksum = calculate_sha256(file_path)
            lines.append(f"{checksum}  {file_path.name}")
            log(f"  {checksum}  {file_path.name}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log_success(f"Saved checksums to {output_path}")
    return output_path


def create_release_archive(
    project_root: Path,
    dist_dir: Path,
    version: str,
    built_binaries: list[Path],
    checksums_file: Path | None,
) -> Path:
    """Package standalone executables, documentation, and license into a release zip."""
    archive_name = f"photo-healer-v{version}-windows-x64.zip"
    archive_path = dist_dir / archive_name
    log(f"Creating release archive: {archive_name}...")

    # License file resolution: use LICENSE if exists, else write temporary license
    license_file = project_root / "LICENSE"
    temp_license_created = False
    if not license_file.is_file():
        license_file = dist_dir / "LICENSE"
        license_file.write_text(DEFAULT_LICENSE_TEXT, encoding="utf-8")
        temp_license_created = True

    files_to_pack: list[tuple[Path, str]] = []
    for bin_path in built_binaries:
        if bin_path.is_file():
            files_to_pack.append((bin_path, bin_path.name))

    for doc_name in ["README.md", "README.ru.md"]:
        doc_path = project_root / doc_name
        if doc_path.is_file():
            files_to_pack.append((doc_path, doc_name))

    if license_file.is_file():
        files_to_pack.append((license_file, "LICENSE"))

    for icon_subpath in ["assets/icon.ico", "assets/icon.png"]:
        icon_path = project_root / icon_subpath
        if icon_path.is_file():
            files_to_pack.append((icon_path, icon_subpath))

    if checksums_file and checksums_file.is_file():
        files_to_pack.append((checksums_file, checksums_file.name))

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src_path, arc_name in files_to_pack:
            zf.write(src_path, arcname=arc_name)

    if temp_license_created and license_file.is_file():
        license_file.unlink(missing_ok=True)

    archive_size = archive_path.stat().st_size
    log_success(f"Release archive created: {archive_name} ({format_bytes(archive_size)})")

    # Update checksums file to also include the release zip
    zip_checksum = calculate_sha256(archive_path)
    if checksums_file and checksums_file.is_file():
        content = checksums_file.read_text(encoding="utf-8").strip()
        updated_content = f"{content}\n{zip_checksum}  {archive_name}\n"
        checksums_file.write_text(updated_content, encoding="utf-8")
        log(f"Updated checksums with release archive: {zip_checksum}  {archive_name}")

    return archive_path


def main() -> int:
    """CLI entry point for the build script."""
    parser = argparse.ArgumentParser(
        description="Automated build script for Photo Healer standalone Windows binaries."
    )
    parser.add_argument("--cli-only", action="store_true", help="Build only the CLI executable")
    parser.add_argument("--gui-only", action="store_true", help="Build only the GUI executable")
    parser.add_argument("--skip-clean", action="store_true", help="Skip cleaning dist/ and build/")
    parser.add_argument("--skip-tests", action="store_true", help="Skip integrity verification tests")
    parser.add_argument("--skip-zip", action="store_true", help="Skip creating release zip archive")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Custom output directory for artifacts (default: dist/)",
    )

    args = parser.parse_args()

    project_root = get_project_root()
    dist_dir = args.output_dir or (project_root / "dist")
    cli_spec = project_root / "pyinstaller.spec"
    gui_spec = project_root / "pyinstaller_gui.spec"

    try:
        version = extract_version(project_root)
        log(f"Starting Photo Healer build pipeline for v{version} (Windows x64)...")

        # 1. Clean
        if not args.skip_clean:
            clean_artifacts(project_root, dist_dir)
        else:
            dist_dir.mkdir(parents=True, exist_ok=True)

        built_binaries: list[Path] = []
        cli_exe = dist_dir / "photo-healer.exe"
        gui_exe = dist_dir / "photo-healer-gui.exe"

        # 2. Build CLI
        if not args.gui_only:
            run_pyinstaller_build(cli_spec, clean=not args.skip_clean)
            if cli_exe.is_file():
                built_binaries.append(cli_exe)
            else:
                log_error(f"Expected CLI executable {cli_exe} was not produced.")
                return 1

        # 3. Build GUI
        if not args.cli_only:
            run_pyinstaller_build(gui_spec, clean=not args.skip_clean)
            if gui_exe.is_file():
                built_binaries.append(gui_exe)
            else:
                log_error(f"Expected GUI executable {gui_exe} was not produced.")
                return 1

        # 4. Verify integrity
        if not args.skip_tests:
            log("Running automated integrity verification...")
            if cli_exe in built_binaries:
                if not verify_cli_binary(cli_exe, version):
                    log_error("CLI binary verification failed!")
                    return 1
            if gui_exe in built_binaries:
                if not verify_gui_binary(gui_exe):
                    log_error("GUI binary verification failed!")
                    return 1

        # 5. Checksums
        checksums_file = dist_dir / "checksums.txt"
        generate_checksums_file(built_binaries, checksums_file)

        # 6. Release zip archive
        if not args.skip_zip:
            create_release_archive(
                project_root=project_root,
                dist_dir=dist_dir,
                version=version,
                built_binaries=built_binaries,
                checksums_file=checksums_file,
            )

        log_success("Build pipeline finished successfully!")
        return 0

    except Exception as e:
        log_error(f"Build pipeline encountered a fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
