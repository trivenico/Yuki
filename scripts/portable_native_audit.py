"""Static native-loader metadata audit for portable Yuki bundles.

Uses platform inspection tools (readelf, otool, or dumpbin); never loads or
executes the inspected application binaries. This audits loader metadata only,
not ABI compatibility or the complete transitive system-library closure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

class NativeAuditError(RuntimeError):
    pass

SYSTEM_DYLIB_PREFIXES = ("/usr/lib/", "/System/Library/")
ELF_ORIGIN_TOKENS = ("$ORIGIN", "${ORIGIN}")


def _run(command: list[str], runner=subprocess.run) -> str:
    try:
        result = runner(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeAuditError(f"Unable to run native inspection tool: {command[0]}") from exc
    if result.returncode != 0:
        raise NativeAuditError(f"Native inspection failed ({command[0]}): {result.stderr.strip()}")
    return result.stdout


def parse_elf_dynamic(output: str) -> dict:
    needed = re.findall(r"\(NEEDED\).*?Shared library: \[(.*?)\]", output)
    paths = re.findall(r"\((?:RPATH|RUNPATH)\).*?Library (?:rpath|runpath): \[(.*?)\]", output)
    entries = [item for value in paths for item in value.split(":") if item]
    def origin_relative(item: str) -> bool:
        return any(item == token or item.startswith(token + "/") for token in ELF_ORIGIN_TOKENS)
    unsafe = [item for item in entries if not origin_relative(item)]
    unsafe_needed = [item for item in needed if "/" in item and not origin_relative(item)]
    return {"needed": needed, "loader_paths": entries, "unsafe_loader_paths": unsafe,
            "unsafe_needed_paths": unsafe_needed, "ok": not unsafe and not unsafe_needed}


def parse_macho_load_commands(load_output: str, linked_output: str) -> dict:
    rpaths = re.findall(r"\bpath (.*?) \(offset \d+\)", load_output)
    absolute_non_system = []
    linked_lines = [
        line for line in linked_output.splitlines()[1:]
        if "(architecture " not in line or not line.rstrip().endswith("):")
    ]
    for line in linked_lines:
        value = line.strip().split(" (", 1)[0]
        if value.startswith("/") and not value.startswith(SYSTEM_DYLIB_PREFIXES):
            absolute_non_system.append(value)
    def loader_relative(value: str) -> bool:
        return any(value == token or value.startswith(token + "/")
                   for token in ("@loader_path", "@executable_path"))
    unsafe_rpaths = [p for p in rpaths if not loader_relative(p)]
    install_names = []
    for line in linked_lines:
        value = line.strip().split(" (", 1)[0]
        if value:
            install_names.append(value)
    allowed_relative = ("@rpath/", "@loader_path/", "@executable_path/")
    unsafe_links = [p for p in install_names if not p.startswith(SYSTEM_DYLIB_PREFIXES) and not p.startswith(allowed_relative)]
    return {"rpaths": rpaths, "absolute_non_system_links": absolute_non_system,
            "install_names": install_names, "unsafe_rpaths": unsafe_rpaths, "unsafe_links": unsafe_links,
            "ok": not unsafe_rpaths and not unsafe_links}


def parse_windows_dependents(output: str) -> dict:
    dependencies = []
    in_section = False
    for line in output.splitlines():
        if "Image has the following dependencies:" in line:
            in_section = True
            continue
        if in_section:
            value = line.strip()
            if not value:
                continue
            if value.lower().endswith(".dll") and re.fullmatch(r"[A-Za-z0-9_.+-]+\.dll", value, re.I):
                dependencies.append(value)
    return {"dependencies": sorted(set(dependencies), key=str.lower),
            "resolution_verified": False,
            "note": "dumpbin lists imports but does not prove runtime DLL resolution."}


def audit_bundle(root: Path, platform_name: str, runner=subprocess.run) -> dict:
    root_candidate = Path(root)
    if root_candidate.is_symlink() or not root_candidate.is_dir():
        raise NativeAuditError("Bundle root must be an existing non-symlink directory.")
    root = root_candidate.resolve(strict=True)

    def is_native_binary(path: Path) -> bool:
        if path.is_symlink() or not path.is_file():
            return False
        try:
            with path.open("rb") as stream:
                magic = stream.read(4)
        except OSError as exc:
            raise NativeAuditError(f"Unable to read bundle file: {path.relative_to(root)}") from exc
        return (
            magic == bytes((127, 69, 76, 70))
            or magic in {bytes((254, 237, 250, 206)), bytes((206, 250, 237, 254)),
                         bytes((254, 237, 250, 207)), bytes((207, 250, 237, 254)),
                         bytes((202, 254, 186, 190)), bytes((190, 186, 254, 202)),
                         bytes((202, 254, 186, 191)), bytes((191, 186, 254, 202))}
            or magic[:2] == b"MZ"
        )

    binaries = sorted(p for p in root.rglob("*") if is_native_binary(p))
    results = []
    for binary in binaries:
        if platform_name == "linux":
            tool = shutil.which("readelf")
            if not tool:
                raise NativeAuditError("readelf is required for Linux native audits.")
            result = parse_elf_dynamic(_run([tool, "-d", str(binary)], runner))
        elif platform_name == "macos":
            tool = shutil.which("otool")
            if not tool:
                raise NativeAuditError("otool is required for macOS native audits.")
            result = parse_macho_load_commands(
                _run([tool, "-l", str(binary)], runner),
                _run([tool, "-L", str(binary)], runner),
            )
        elif platform_name == "windows":
            tool = shutil.which("dumpbin")
            if not tool:
                raise NativeAuditError("dumpbin is required for Windows native audits.")
            result = parse_windows_dependents(_run([tool, "/DEPENDENTS", str(binary)], runner))
        else:
            raise NativeAuditError(f"Unsupported platform: {platform_name}")
        results.append({"path": binary.relative_to(root).as_posix(), **result})
    failures = [item for item in results if item.get("ok") is False]
    no_native_binaries = not results
    return {"platform": platform_name, "binary_count": len(results), "results": results,
            "no_native_binaries": no_native_binaries,
            "metadata_failures": len(failures) + int(no_native_binaries),
            "resolution_fully_verified": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--platform", choices=("linux", "macos", "windows"),
                        default={"nt": "windows", "posix": "linux"}.get(os.name, "linux"))
    args = parser.parse_args(argv)
    try:
        report = audit_bundle(args.bundle_root, args.platform)
    except (OSError, NativeAuditError, ValueError) as exc:
        print(f"Native loader audit failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["metadata_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
