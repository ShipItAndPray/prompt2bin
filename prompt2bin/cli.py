"""
CLI entry point for prompt2bin.

Usage:
    p2b "a CLI that converts CSV to JSON with column filtering"
    p2b "find duplicate files by content hash" --name dedup
    p2b "HTTP static file server" --os linux --arch amd64
    p2b --list
    p2b --clear-cache
"""

import argparse
import json
import os
import sys

from .core import build, load_manifest, MANIFEST_PATH, BINARY_DIR, SOURCE_DIR, _size_str


def cmd_build(args):
    description = " ".join(args.description)
    result = build(
        description=description,
        name=args.name,
        target_os=args.target_os,
        target_arch=args.target_arch,
        max_attempts=args.attempts,
        force=args.force,
        verbose=True,
    )
    sys.exit(0 if result.success else 1)


def cmd_list(args):
    manifest = load_manifest()
    if not manifest:
        print("No binaries generated yet. Try: p2b \"a tool that ...\"")
        return

    print(f"\n  prompt2bin cache ({len(manifest)} binaries)\n")
    for phash, info in manifest.items():
        name = info.get("name", "?")
        size = _size_str(info.get("size", 0))
        exists = os.path.exists(info.get("path", ""))
        status = "OK" if exists else "MISSING"
        print(f"  {name} ({size}, {status})")
        print(f"    {info.get('description', '?')}")
        print(f"    Created: {info.get('created', '?')}")
        print()


def cmd_clear(args):
    manifest = load_manifest()
    count = len(manifest)

    # Remove binaries
    for info in manifest.values():
        path = info.get("path", "")
        if os.path.exists(path):
            os.unlink(path)

    # Clear sources
    for f in SOURCE_DIR.iterdir():
        f.unlink()

    # Clear manifest
    with open(MANIFEST_PATH, "w") as f:
        json.dump({}, f)

    print(f"  Cleared {count} cached binaries.")


def main():
    parser = argparse.ArgumentParser(
        prog="p2b",
        description="prompt2bin -- Turn English into executables",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  p2b "a CLI that converts CSV to JSON"
  p2b "a tool that finds duplicate files" --name dedup
  p2b "an HTTP file server" --name serve --os linux
  p2b --list
  p2b --clear-cache""",
    )

    parser.add_argument("description", nargs="*", help="What the tool should do (plain English)")
    parser.add_argument("--name", "-n", help="Name for the output binary")
    parser.add_argument("--os", dest="target_os", choices=["linux", "darwin", "windows"],
                        help="Target OS for cross-compilation")
    parser.add_argument("--arch", dest="target_arch", choices=["amd64", "arm64", "386"],
                        help="Target architecture")
    parser.add_argument("--attempts", type=int, default=5,
                        help="Max compilation fix attempts (default: 5)")
    parser.add_argument("--force", "-f", action="store_true",
                        help="Force regeneration (ignore cache)")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List cached binaries")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Clear all cached binaries")

    args = parser.parse_args()

    if args.list:
        cmd_list(args)
    elif args.clear_cache:
        cmd_clear(args)
    elif args.description:
        cmd_build(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
