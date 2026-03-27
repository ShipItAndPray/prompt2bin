"""
Core engine: prompt -> Go code -> compiled binary.

Supports two LLM backends:
  1. Anthropic API (preferred, needs ANTHROPIC_API_KEY)
  2. Claude CLI fallback (needs `claude` installed)

Includes binary caching by prompt hash.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path


# --- Config ---

CACHE_DIR = Path(os.environ.get("P2B_CACHE", Path.home() / ".prompt2bin"))
BINARY_DIR = CACHE_DIR / "binaries"
MANIFEST_PATH = CACHE_DIR / "manifest.json"
SOURCE_DIR = CACHE_DIR / "sources"  # keep generated .go files for debugging

BINARY_DIR.mkdir(parents=True, exist_ok=True)
SOURCE_DIR.mkdir(parents=True, exist_ok=True)


# --- LLM Prompts ---

CODEGEN_SYSTEM = """You are an expert Go programmer generating production-quality CLI tools.

ABSOLUTE RULES — violating ANY of these will cause a build failure:
1. Output ONLY Go source code. No explanations. No markdown. No code fences. Just Go.
2. SINGLE FILE: everything in one main.go. Package main. Only stdlib imports.
3. FLAG PACKAGE: use "flag" for all CLI options. Always define a usage function.
4. ERROR HANDLING: print errors to stderr with fmt.Fprintf(os.Stderr, ...). Never panic.
5. I/O PATTERN: read from file args or stdin. Write results to stdout.
6. GO 1.13 COMPATIBLE: no generics, no embed directive, no errors.Is/As, no io/fs.
7. CROSS-PLATFORM: no platform-specific syscalls. Use os and path/filepath.
8. COMPLETE: the code must compile and run correctly. No TODO placeholders.

QUALITY STANDARDS:
- Handle empty input gracefully (don't crash, print helpful message)
- Validate all flag values before using them
- Buffer large I/O with bufio for performance
- Include a descriptive usage message that explains ALL options

The binary you generate is the PRODUCT. It must work perfectly."""

FIX_SYSTEM = """You are fixing broken Go code. Output ONLY the corrected Go source.
No explanations. No markdown. No code fences. Just the complete fixed Go file.
Fix ALL errors, not just the first one. The output must compile cleanly."""

TEST_SYSTEM = """You generate minimal bash test scripts.
Output ONLY bash code. No explanations. No markdown. No code fences.
The script must start with #!/bin/bash
Use set -euo pipefail
Keep tests fast (<5 seconds total).
Clean up temp files in a trap."""


# --- Data Classes ---

@dataclass
class BuildResult:
    success: bool
    binary_path: str = ""
    binary_name: str = ""
    size: int = 0
    sha256: str = ""
    attempts: int = 0
    cached: bool = False
    help_text: str = ""
    steps: list = field(default_factory=list)
    error: str = ""


# --- Manifest ---

def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return {}


def save_manifest(manifest: dict):
    with open(MANIFEST_PATH, "w") as f:
        json.dump(manifest, f, indent=2)


# --- LLM Backends ---

def _call_anthropic_api(prompt: str, system: str, timeout: int = 120) -> str:
    """Call Anthropic API directly. Returns empty string on failure."""
    try:
        import anthropic
    except ImportError:
        return ""

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception:
        return ""


def _call_claude_cli(prompt: str, system: str, timeout: int = 120) -> str:
    """Call Claude CLI as fallback. Returns empty string on failure."""
    full_prompt = f"{system}\n\n---\n\n{prompt}"
    try:
        # Use --tools "" to disable tool use (prevents file writes),
        # --output-format text to get raw text output
        result = subprocess.run(
            ["claude", "-p", full_prompt, "--output-format", "text", "--tools", ""],
            capture_output=True, text=True, timeout=timeout,
        )
        out = result.stdout.strip()
        return out
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def call_llm(prompt: str, system: str, timeout: int = 120) -> str:
    """Call LLM: tries Anthropic API first, falls back to Claude CLI."""
    # Try API first (faster, more reliable)
    out = _call_anthropic_api(prompt, system, timeout)
    if out:
        return out
    # Fallback to CLI
    return _call_claude_cli(prompt, system, timeout)


# --- Code Processing ---

def strip_fences(text: str) -> str:
    """Remove markdown code fences and extract Go source."""
    if not text:
        return ""

    lines = text.split("\n")

    # Remove leading/trailing fences
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    code = "\n".join(lines).strip()

    # Extract from embedded fences
    m = re.search(r"```(?:go)?\s*\n(.*?)```", code, re.DOTALL)
    if m:
        code = m.group(1).strip()

    # Find where Go code actually starts
    idx = code.find("package main")
    if idx > 0:
        code = code[idx:]

    return code


def prompt_hash(description: str) -> str:
    """Stable hash for caching."""
    return hashlib.sha256(description.strip().lower().encode()).hexdigest()[:16]


# --- Compilation ---

def compile_go(source_path: str, output_path: str, goos=None, goarch=None) -> tuple:
    """Compile Go source to binary. Returns (success, stderr)."""
    env = os.environ.copy()
    if goos:
        env["GOOS"] = goos
    if goarch:
        env["GOARCH"] = goarch
    env["CGO_ENABLED"] = "0"

    try:
        result = subprocess.run(
            ["go", "build", "-ldflags", "-s -w", "-o", output_path, source_path],
            capture_output=True, text=True, timeout=60, env=env,
        )
        return result.returncode == 0, result.stderr
    except subprocess.TimeoutExpired:
        return False, "compilation timed out (60s)"


def smoke_test(binary_path: str) -> tuple:
    """Run basic smoke tests. Returns (passed, details)."""
    if not os.path.isfile(binary_path) or not os.access(binary_path, os.X_OK):
        return False, "binary not found or not executable"

    try:
        r = subprocess.run([binary_path, "-h"], capture_output=True, text=True, timeout=5)
        output = r.stdout + r.stderr
        if len(output) < 10:
            return False, "help output too short"
        return True, output.strip()[:500]
    except subprocess.TimeoutExpired:
        return False, "-h timed out"
    except Exception as e:
        return False, f"-h failed: {e}"


def generate_and_run_tests(binary_path: str, description: str) -> tuple:
    """Generate targeted tests and run them. Returns (passed, output)."""
    prompt = f"""Generate a bash test script for this CLI tool.
Binary path: {binary_path}
Tool description: {description}

Write 3-5 tests:
- Test the help flag works
- Test with valid input (create temp files if needed)
- Test with empty/missing input (should not crash)
- Test a realistic use case

Each test: echo description, run command, check exit code or output.
Use a trap to clean up temp files on exit."""

    test_code = strip_fences(call_llm(prompt, TEST_SYSTEM, timeout=90))
    if not test_code.startswith("#!"):
        test_code = "#!/bin/bash\nset -euo pipefail\n" + test_code

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(test_code)
        test_path = f.name

    try:
        os.chmod(test_path, 0o755)
        result = subprocess.run(
            ["bash", test_path],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0, (result.stdout + result.stderr)[:1000]
    except subprocess.TimeoutExpired:
        return False, "tests timed out (30s)"
    except Exception as e:
        return False, str(e)
    finally:
        os.unlink(test_path)


# --- Auto-name ---

def auto_name(description: str) -> str:
    """Generate a short binary name from the description."""
    words = re.sub(r"[^a-z0-9 ]", "", description.lower()).split()
    skip = {"a", "an", "the", "cli", "tool", "that", "which", "for", "with", "and", "to"}
    name = next((w for w in words if w not in skip and len(w) > 2), words[0] if words else "tool")
    return name


# --- Main Build Pipeline ---

def build(
    description: str,
    name: str = None,
    target_os: str = None,
    target_arch: str = None,
    max_attempts: int = 5,
    force: bool = False,
    output_dir: str = None,
    verbose: bool = True,
) -> BuildResult:
    """Full pipeline: prompt -> code -> compile -> test -> binary."""

    result = BuildResult(success=False)

    if not name:
        name = auto_name(description)
    if target_os == "windows" and not name.endswith(".exe"):
        name += ".exe"

    result.binary_name = name

    # Where to put the binary
    if output_dir:
        output_path = os.path.join(os.path.abspath(output_dir), name)
    else:
        output_path = os.path.abspath(name)

    # --- Cache check ---
    phash = prompt_hash(description)
    manifest = load_manifest()

    if not force and phash in manifest:
        cached = manifest[phash]
        cached_path = cached.get("path", "")
        if os.path.exists(cached_path):
            # Copy from cache to output location
            if cached_path != output_path:
                shutil.copy2(cached_path, output_path)
                os.chmod(output_path, 0o755)
            result.success = True
            result.binary_path = output_path
            result.size = os.path.getsize(output_path)
            result.sha256 = cached.get("sha256", "")
            result.cached = True
            result.help_text = cached.get("help", "")
            result.steps.append("cache hit")
            if verbose:
                _print_cached(name, result)
            return result

    if verbose:
        _print_header(description, name, target_os, target_arch)

    # --- Step 1: Generate ---
    if verbose:
        print("  ■ Generating...", end="", flush=True)
    t0 = time.time()
    code = strip_fences(call_llm(f"Build this CLI tool: {description}", CODEGEN_SYSTEM, timeout=180))
    elapsed = time.time() - t0

    if not code or "package main" not in code:
        if verbose:
            print(f" FAILED ({elapsed:.1f}s)")
        result.error = "LLM did not return valid Go code"
        result.steps.append(f"generation failed ({elapsed:.1f}s)")
        return result
    if verbose:
        print(f" done ({elapsed:.1f}s, {len(code)} chars)")
    result.steps.append(f"generated ({elapsed:.1f}s, {len(code)} chars)")

    # Save source for debugging
    source_archive = SOURCE_DIR / f"{phash}.go"
    source_archive.write_text(code)

    # --- Step 2: Compile with auto-fix ---
    build_dir = tempfile.mkdtemp(prefix="p2b_")
    source_path = os.path.join(build_dir, "main.go")
    compiled = False

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        with open(source_path, "w") as f:
            f.write(code)

        if verbose:
            label = "  ■ Compiling" + (f" (fix #{attempt-1})" if attempt > 1 else "") + "..."
            print(label, end="", flush=True)
        t0 = time.time()

        ok, errors = compile_go(source_path, output_path, target_os, target_arch)
        elapsed = time.time() - t0

        if ok:
            if verbose:
                print(f" done ({elapsed:.1f}s)")
            result.steps.append(f"compiled (attempt {attempt}, {elapsed:.1f}s)")
            compiled = True
            break
        else:
            if verbose:
                print(f" FAILED ({elapsed:.1f}s)")
            result.steps.append(f"compile failed (attempt {attempt}): {errors[:200]}")
            if attempt < max_attempts:
                if verbose:
                    print(f"    Fixing ({attempt}/{max_attempts})...", end="", flush=True)
                t0 = time.time()
                fix_prompt = f"Fix this Go code.\n\nCOMPILE ERRORS:\n{errors}\n\nCODE:\n{code}\n\nOutput the COMPLETE corrected file."
                code = strip_fences(call_llm(fix_prompt, FIX_SYSTEM))
                elapsed = time.time() - t0
                if not code or "package main" not in code:
                    if verbose:
                        print(f" fix failed ({elapsed:.1f}s)")
                    continue
                if verbose:
                    print(f" done ({elapsed:.1f}s)")
                # Update saved source
                source_archive.write_text(code)

    shutil.rmtree(build_dir, ignore_errors=True)

    if not compiled:
        result.error = f"Failed to compile after {max_attempts} attempts"
        return result

    # --- Step 3: Smoke test ---
    if verbose:
        print("  ■ Smoke testing...", end="", flush=True)
    passed, help_text = smoke_test(output_path)
    if verbose:
        print(f" {'PASS' if passed else 'FAIL'}")
    result.help_text = help_text if passed else ""
    result.steps.append(f"smoke test {'passed' if passed else 'failed'}")

    # --- Step 4: Integration tests ---
    if verbose:
        print("  ■ Testing...", end="", flush=True)
    t0 = time.time()
    try:
        test_passed, test_output = generate_and_run_tests(output_path, description)
        elapsed = time.time() - t0
        if verbose:
            print(f" {'PASS' if test_passed else 'FAIL'} ({elapsed:.1f}s)")
            if not test_passed and test_output:
                for line in test_output.split("\n")[:5]:
                    if line.strip():
                        print(f"    {line}")
        result.steps.append(f"tests {'passed' if test_passed else 'failed'} ({elapsed:.1f}s)")
    except Exception as e:
        elapsed = time.time() - t0
        if verbose:
            print(f" skipped ({elapsed:.1f}s)")
        result.steps.append(f"tests skipped: {e}")

    # --- Done ---
    size = os.path.getsize(output_path)
    with open(output_path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()[:12]

    result.success = True
    result.binary_path = output_path
    result.size = size
    result.sha256 = sha

    # Cache: store a copy in BINARY_DIR
    cache_path = str(BINARY_DIR / name)
    if output_path != cache_path:
        shutil.copy2(output_path, cache_path)
        os.chmod(cache_path, 0o755)

    manifest[phash] = {
        "name": name,
        "description": description,
        "path": cache_path,
        "size": size,
        "sha256": sha,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "target_os": target_os or "native",
        "target_arch": target_arch or "native",
        "attempts": result.attempts,
        "help": result.help_text[:500],
    }
    save_manifest(manifest)

    if verbose:
        _print_done(name, result)

    return result


# --- Pretty Output ---

def _size_str(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size/1024:.0f}KB"
    return f"{size/1024/1024:.1f}MB"


def _print_header(description, name, target_os, target_arch):
    print(f"""
  prompt2bin
  "The AI will just create the binary directly."

  Prompt : {description}
  Output : ./{name}
  Target : {target_os or 'native'}/{target_arch or 'native'}
""")


def _print_cached(name, result):
    print(f"""
  prompt2bin — cache hit

  Binary : ./{name}
  Size   : {_size_str(result.size)}
  SHA256 : {result.sha256}

  (Use --force to regenerate)
""")


def _print_done(name, result):
    print(f"""
  BUILD COMPLETE

  Binary : ./{name}
  Size   : {_size_str(result.size)}
  SHA256 : {result.sha256}

  Run:  ./{name} -h
""")
