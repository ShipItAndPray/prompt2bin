# prompt2bin

Turn English into executables. No code. Just the binary.

```
$ p2b "a CLI that converts CSV to JSON with column filtering"

  prompt2bin
  "The AI will just create the binary directly."

  Prompt : a CLI that converts CSV to JSON with column filtering
  Output : ./csv2json
  Target : native/native

  ■ Generating... done (24.6s, 4981 chars)
  ■ Compiling... done (0.5s)
  ■ Smoke testing... PASS
  ■ Testing... PASS (10.1s)

  BUILD COMPLETE

  Binary : ./csv2json
  Size   : 2.0MB
  SHA256 : bdf53d481398

  Run:  ./csv2json -h
```

```
$ echo "name,age,city\nalice,30,NYC" | ./csv2json -columns name,city
{"city":"NYC","name":"alice"}
```

## What it does

You describe a tool in plain English. prompt2bin generates a self-contained compiled binary. No source code. No dependencies. No runtime. Just a binary that works.

- Generates Go binaries (static, zero dependencies, 2-5MB)
- Auto-fixes compilation errors (up to 5 attempts)
- Smoke tests every binary before delivery
- Cross-compiles for linux/mac/windows
- Caches binaries by prompt hash (instant on repeat)

## Requirements

- **Go** (1.13+) — for compiling generated code
- **One of:**
  - `ANTHROPIC_API_KEY` env var (preferred, uses API directly)
  - `claude` CLI installed (fallback)

## Install

```bash
pip install prompt2bin
```

Or from source:

```bash
git clone https://github.com/ShipItAndPray/prompt2bin.git
cd prompt2bin
pip install .
```

## Usage

### Generate a binary

```bash
# Basic usage
p2b "a tool that finds duplicate files by content hash"

# Name the output
p2b "an HTTP static file server" --name serve

# Cross-compile
p2b "a JSON pretty-printer" --name jpp --os linux --arch amd64

# Force regeneration (ignore cache)
p2b "a word counter with JSON output" --force
```

### Manage cache

```bash
# List cached binaries
p2b --list

# Clear all cached binaries
p2b --clear-cache
```

### MCP Server

prompt2bin also works as an MCP server, so AI agents can generate binaries on the fly:

```json
{
  "mcpServers": {
    "prompt2bin": {
      "command": "python3",
      "args": ["-m", "prompt2bin.mcp_server"]
    }
  }
}
```

Tools: `generate_binary`, `list_binaries`, `run_binary`, `delete_binary`

## Examples

Binaries generated during testing:

| Prompt | Binary | Size |
|--------|--------|------|
| "word counter with --json flag" | `jwc` | 1.9MB |
| "find duplicate files by content hash" | `dedup` | 1.8MB |
| "CSV to JSON with column filtering" | `csv2json` | 2.0MB |
| "regex grep with --count and --invert" | `rxgrep` | 2.0MB |
| "HTTP static file server with CORS" | `serve` | 5.8MB |

All generated in 20-90 seconds. All compile. All work.

## How it works

1. Your prompt goes to Claude (API or CLI)
2. Claude generates a single-file Go program
3. Go compiles it to a static binary (`CGO_ENABLED=0 -ldflags "-s -w"`)
4. If compilation fails, Claude fixes the errors (up to 5 rounds)
5. Binary is smoke-tested (`-h` flag, basic I/O)
6. Binary is cached by prompt hash for instant reuse

## Why Go?

- Static binaries with zero runtime dependencies
- Cross-compilation built in (`GOOS=linux GOARCH=amd64`)
- Fast compilation (<1s for most tools)
- Small binaries (2-5MB stripped)
- Rich stdlib (HTTP, JSON, crypto, compression — no third-party imports needed)

## License

MIT
