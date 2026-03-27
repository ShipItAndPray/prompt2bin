"""
MCP Server for prompt2bin.

An AI agent says "I need a CSV to JSON converter" and gets back a working binary.

Tools:
    - generate_binary: Turn a prompt into a compiled binary
    - list_binaries: List previously generated binaries
    - run_binary: Execute a generated binary with arguments
    - delete_binary: Remove a generated binary
"""

import asyncio
import json
import os
import subprocess
import sys

from .core import build, load_manifest, save_manifest, _size_str

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    print("Install MCP SDK: pip install 'promptcompile[mcp]'", file=sys.stderr)
    sys.exit(1)


server = Server("prompt2bin")


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="generate_binary",
            description=(
                "Generate a compiled binary from a natural language description. "
                "Self-contained executable, zero dependencies. Describe what it should do in English."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What the binary should do, in plain English.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Name for the binary (optional, auto-generated if omitted)",
                    },
                    "target_os": {
                        "type": "string",
                        "enum": ["linux", "darwin", "windows"],
                        "description": "Target OS (optional, defaults to current)",
                    },
                    "target_arch": {
                        "type": "string",
                        "enum": ["amd64", "arm64"],
                        "description": "Target architecture (optional, defaults to current)",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Force regeneration, ignoring cache",
                    },
                },
                "required": ["description"],
            },
        ),
        Tool(
            name="list_binaries",
            description="List all previously generated binaries.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="run_binary",
            description="Execute a generated binary with arguments and return output.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Binary name"},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "CLI arguments",
                    },
                    "stdin": {"type": "string", "description": "Input to pipe to stdin"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="delete_binary",
            description="Delete a generated binary.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Binary name"},
                },
                "required": ["name"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name, arguments):
    if name == "generate_binary":
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: build(
                description=arguments["description"],
                name=arguments.get("name"),
                target_os=arguments.get("target_os"),
                target_arch=arguments.get("target_arch"),
                force=arguments.get("force", False),
                verbose=False,
            ),
        )

        if result.success:
            text = (
                f"Binary generated successfully!\n\n"
                f"Name: {result.binary_name}\n"
                f"Path: {result.binary_path}\n"
                f"Size: {_size_str(result.size)}\n"
                f"SHA256: {result.sha256}\n"
                f"Cached: {result.cached}\n"
                f"Attempts: {result.attempts}\n\n"
                f"Help:\n{result.help_text}\n\n"
                f"Run: {result.binary_path} -h"
            )
        else:
            text = f"Build failed: {result.error}\n\nSteps: {json.dumps(result.steps, indent=2)}"

        return [TextContent(type="text", text=text)]

    elif name == "list_binaries":
        manifest = load_manifest()
        if not manifest:
            return [TextContent(type="text", text="No binaries generated yet.")]

        lines = ["Generated binaries:\n"]
        for phash, info in manifest.items():
            bname = info.get("name", "?")
            size = _size_str(info.get("size", 0))
            exists = "OK" if os.path.exists(info.get("path", "")) else "MISSING"
            lines.append(
                f"  {bname} ({size}, {exists})\n"
                f"    {info.get('description', '?')}\n"
                f"    Created: {info.get('created', '?')}\n"
            )
        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "run_binary":
        bin_name = arguments["name"]
        args = arguments.get("args", [])
        stdin_data = arguments.get("stdin")
        timeout = arguments.get("timeout", 30)

        manifest = load_manifest()
        # Find by name
        entry = None
        for info in manifest.values():
            if info.get("name") == bin_name:
                entry = info
                break
        if not entry:
            return [TextContent(type="text", text=f"Binary '{bin_name}' not found. Use list_binaries.")]

        path = entry["path"]
        if not os.path.exists(path):
            return [TextContent(type="text", text=f"Binary file missing at {path}")]

        try:
            result = subprocess.run(
                [path] + args,
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = ""
            if result.stdout:
                output += f"stdout:\n{result.stdout}\n"
            if result.stderr:
                output += f"stderr:\n{result.stderr}\n"
            output += f"exit code: {result.returncode}"
            return [TextContent(type="text", text=output)]
        except subprocess.TimeoutExpired:
            return [TextContent(type="text", text=f"Timed out after {timeout}s")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    elif name == "delete_binary":
        bin_name = arguments["name"]
        manifest = load_manifest()

        to_delete = None
        for phash, info in manifest.items():
            if info.get("name") == bin_name:
                to_delete = phash
                path = info.get("path", "")
                if os.path.exists(path):
                    os.unlink(path)
                break

        if to_delete:
            del manifest[to_delete]
            save_manifest(manifest)
            return [TextContent(type="text", text=f"Deleted '{bin_name}'.")]
        return [TextContent(type="text", text=f"Binary '{bin_name}' not found.")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
