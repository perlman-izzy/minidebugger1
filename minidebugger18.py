#!/usr/bin/env python3
"""
minidebugger19.py — Autonomous, general-purpose Python mini-debugger.

What it does
------------
- Runs a target Python file in isolation.
- Detects failures (incl. unittest FAIL/ERROR even when rc==0).
- First tries safe, *generic* local heuristics for Syntax/Indentation errors.
- Otherwise asks an optional local LLM for **minimal** fixes with strict guardrails.
- Diagnoses timeouts by (a) instrumenting the code (via LLM) and (b) summarizing the trace.
- Keeps patches aligned with program intent & error locality:
  * Uses failing frames and/or timeout summary to focus on specific function(s).
  * Enforces public-API preservation (no renames, no arg-count changes).
  * Limits diffs; prefers **AST-scoped, text-spliced replacement** of a single function/class.

Notes
-----
- **No project-specific / hardcoded patches.** Everything here is generic.
- LLM calls are optional; if the endpoint is down, we still do syntax fixes & exit with diagnostics.
- Designed to avoid over-edits: small diffs, function-scoped replacements, score-based selection.

Env Vars
--------
TARGET_FILE                 Path to the Python file being debugged (default: /Users/williamwhite/testcodebase5.py)
MAX_ITERS                   Max outer iterations (default 10)
EXEC_TIMEOUT                Seconds to run target in main loop (default 100)
TEST_TIMEOUT                Seconds to validate final candidate (default 20)
DIFF_THRESHOLD_PERCENT      Max % of lines changed to accept a patch (default 20.0)
LLM_ENDPOINT                HTTP endpoint (Gemini-compatible JSON)
LLM_TIMEOUT                 Seconds per request (default 60.0)
LLM_RETRIES                 Attempts per LLM call (default 2)
LLM_DISABLED                "1" to disable all LLM usage (default 0)
TOURNAMENT_CANDIDATES       Number of variants to try per LLM ask (default 2)

Changes vs v18
--------------
- Fix: robust string handling for traces (no bytes concatenation).
- New: Parse timeout *summary* to extract the implicated function; focus patches on that function.
- New: Function/class replacement uses **text splicing** (not `ast.unparse`) to minimize global diffs.
- New: Guardrail check verifies **exact function signature** (name + arg count) for function-scoped patches.
- New: Dynamic diff budget for timeouts when patch is strictly function-scoped (still bounded).
- Improved: LLM prompts now ask for **only the function** when we know the culprit; reduces patch size.
"""

from __future__ import annotations

import os
import re
import sys
import ast
import json
import time
import difflib
import logging
import tempfile
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# -----------------------
# Logging
# -----------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger("minidebugger19")
logging.getLogger("asyncio").setLevel(logging.DEBUG)  # macOS prints selector info

# -----------------------
# Config
# -----------------------
TARGET_FILE = Path(os.environ.get("TARGET_FILE", "/Users/williamwhite/testcodebase5.py"))
MAX_ITERS = int(os.environ.get("MAX_ITERS", "10"))
EXEC_TIMEOUT = int(os.environ.get("EXEC_TIMEOUT", "100"))
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "20"))
DIFF_THRESHOLD_PERCENT = float(os.environ.get("DIFF_THRESHOLD_PERCENT", "20.0"))
TOURNAMENT_CANDIDATES = int(os.environ.get("TOURNAMENT_CANDIDATES", "2"))

LLM_ENDPOINT = os.environ.get(
    "LLM_ENDPOINT",
    "http://localhost:8000/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent",
)
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60.0"))
LLM_RETRIES = int(os.environ.get("LLM_RETRIES", "2"))
LLM_DISABLED = os.environ.get("LLM_DISABLED", "0") == "1"

RESULT_JSON_PATH = Path("/tmp/debugger_result.json")

# -----------------------
# Small helpers
# -----------------------
def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")

def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def temp_script_path(base_dir: Path) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="dbg_run_", dir=str(base_dir)))
    return tmp_dir / "script.py"

def as_text(x: Any) -> str:
    if isinstance(x, (bytes, bytearray)):
        return x.decode(errors="replace")
    return x or ""

def is_change_too_large(original_code: str, new_code: str, threshold_percent: float) -> bool:
    ol = original_code.splitlines()
    nl = new_code.splitlines()
    diff = list(difflib.unified_diff(ol, nl, lineterm=""))
    changed = sum(1 for line in diff if line.startswith(("+", "-")))
    if changed > 0:
        changed -= 2  # ignore diff headers
    total = max(1, len(ol))
    pct = 100.0 * max(0, changed) / total
    logger.info("[Diff] %d lines changed (%.2f%%)", max(0, changed), pct)
    return pct > threshold_percent

# -----------------------
# Run & Analyze
# -----------------------
def run_python_file(path: Path, timeout: int) -> Tuple[int, str, str]:
    """Run a Python file in its directory, return (rc, stdout, stderr)."""
    env = os.environ.copy()
    env.setdefault("PYTHONHASHSEED", "0")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONFAULTHANDLER", "1")
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=str(path.parent),
            capture_output=True,
            text=True,  # strings not bytes
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as e:
        return 124, as_text(e.stdout), as_text(e.stderr) + f"\n[Timeout] Execution exceeded {timeout}s."

_FAIL_PAT = re.compile(r"^\s*(FAIL|ERROR|FAILED)\b", re.MULTILINE)
_OK_PAT = re.compile(r"^\s*OK\s*$", re.MULTILINE)
_TRACEBACK_PAT = re.compile(r"Traceback \(most recent call last\):")

def analyze_run(rc: int, stdout: str, stderr: str) -> Tuple[bool, str]:
    """Success if no stderr, no unittest FAIL/ERROR, and either 'OK' found or rc==0."""
    if stderr.strip():
        return False, "stderr not empty"
    if _TRACEBACK_PAT.search(stdout):
        return False, "traceback in stdout"
    if _FAIL_PAT.search(stdout):
        return False, "unittest failures"
    if _OK_PAT.search(stdout):
        return True, "unittest OK"
    return (rc == 0 and not stderr.strip()), "rc/err heuristic"

# -----------------------
# Error Classification
# -----------------------
_ERROR_ROUTES = [
    (re.compile(r"SyntaxError", re.I), "syntax"),
    (re.compile(r"IndentationError", re.I), "syntax"),
    (re.compile(r"ImportError: No module named ['\"]([^'\"]+)['\"]"), "missing_import"),
    (re.compile(r"NameError: name ['\"](\w+)['\"] is not defined"), "name_error"),
    (re.compile(r"AttributeError: '([^']+)' object has no attribute '(\w+)'"), "attribute_error"),
    (re.compile(r"(Timeout|exceeded \d+s)"), "timeout"),
    (re.compile(r"AssertionError|FAILED.*assert", re.I), "assertion"),
]

def classify(stderr: str, stdout: str) -> str:
    blob = (stderr or "") + "\n" + (stdout or "")
    for pat, kind in _ERROR_ROUTES:
        if pat.search(blob):
            return kind
    return "generic"

# -----------------------
# Trace extraction & context slicing
# -----------------------
_FRAME_RE = re.compile(r'File "(.+?)", line (\d+), in ([\w_]+)')

def extract_frames(text: str) -> List[Tuple[str, int, str]]:
    return [(m.group(1), int(m.group(2)), m.group(3)) for m in _FRAME_RE.finditer(text or "")]

def slice_context(src: str, line: int, radius: int = 40) -> str:
    lines = src.splitlines()
    i0 = max(0, line - 1 - radius)
    i1 = min(len(lines), line - 1 + radius)
    return "\n".join(lines[i0:i1])

def allowed_symbols_from_frames(frames: List[Tuple[str, int, str]]) -> set[str]:
    return {fn for _, __, fn in frames if fn}

# -----------------------
# Parse function from timeout summary
# -----------------------
_SUMMARY_FUNC_RE = re.compile(r"`([\w\.]+)`")  # backticked name

def function_from_summary(summary: Optional[str]) -> Optional[str]:
    if not summary:
        return None
    m = _SUMMARY_FUNC_RE.search(summary)
    if not m:
        return None
    token = m.group(1)  # e.g., QueueHandler.start_processing
    func = token.split(".")[-1].strip()
    return func or None

# -----------------------
# AST helpers & text-splice replacement
# -----------------------
@dataclass
class FuncSig:
    name: str
    argcount: int

def _func_sig(fn: ast.AST) -> Optional[FuncSig]:
    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return FuncSig(fn.name, len(fn.args.args))
    return None

@dataclass
class FuncLocate:
    name: str
    lineno: int
    end_lineno: int
    indent: int
    sig: FuncSig

def find_function(src: str, name: str) -> Optional[FuncLocate]:
    try:
        tree = ast.parse(src)
    except Exception:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            # Compute indentation from source line
            lines = src.splitlines()
            if 1 <= node.lineno <= len(lines):
                line = lines[node.lineno - 1]
                indent = len(line) - len(line.lstrip(" "))
            else:
                indent = 0
            sig = _func_sig(node)
            return FuncLocate(
                name=node.name,
                lineno=node.lineno,
                end_lineno=getattr(node, "end_lineno", node.lineno),
                indent=indent,
                sig=sig or FuncSig(node.name, 0),
            )
    return None

def normalize_indentation(block: str, target_indent: int) -> str:
    lines = block.splitlines()
    # detect minimal indent of body except first line (def ...)
    min_body = None
    for i, ln in enumerate(lines):
        if i == 0:
            continue
        if ln.strip():
            lead = len(ln) - len(ln.lstrip(" "))
            min_body = lead if min_body is None else min(min_body, lead)
    if min_body is None:
        min_body = 0
    out: List[str] = []
    # first line: left-strip then add target indent
    if lines:
        out.append(" " * target_indent + lines[0].lstrip())
    for i in range(1, len(lines)):
        ln = lines[i]
        if ln.strip():
            lead = len(ln) - len(ln.lstrip(" "))
            rel = max(0, lead - min_body)
            out.append(" " * (target_indent + rel) + ln.lstrip())
        else:
            out.append("")
    return "\n".join(out)

def replace_function_text(src: str, func_loc: FuncLocate, new_func_src: str) -> Optional[str]:
    # Verify new_func_src parses and matches signature name & argcount
    try:
        t = ast.parse(new_func_src)
    except Exception:
        return None
    if not t.body or not isinstance(t.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    new_node = t.body[0]
    new_sig = _func_sig(new_node)
    if not new_sig or new_sig.name != func_loc.sig.name or new_sig.argcount != func_loc.sig.argcount:
        logger.warning("[Guardrails] Function signature mismatch: expected %s/%d, got %s/%d",
                       func_loc.sig.name, func_loc.sig.argcount, new_sig.name if new_sig else "?", new_sig.argcount if new_sig else -1)
        return None

    lines = src.splitlines()
    i0 = max(0, func_loc.lineno - 1)
    i1 = min(len(lines), func_loc.end_lineno)
    before = lines[:i0]
    after = lines[i1:]
    patched = normalize_indentation(new_func_src, func_loc.indent)
    new_lines = before + patched.splitlines() + after
    return "\n".join(new_lines)

# (class replacement kept for parity; rarely needed for timeouts)
@dataclass
class ClassLocate:
    name: str
    lineno: int
    end_lineno: int
    indent: int

def find_class(src: str, name: str) -> Optional[ClassLocate]:
    try:
        tree = ast.parse(src)
    except Exception:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            lines = src.splitlines()
            if 1 <= node.lineno <= len(lines):
                line = lines[node.lineno - 1]
                indent = len(line) - len(line.lstrip(" "))
            else:
                indent = 0
            return ClassLocate(name=node.name, lineno=node.lineno, end_lineno=getattr(node, "end_lineno", node.lineno), indent=indent)
    return None

def replace_class_text(src: str, cls_loc: ClassLocate, new_class_src: str) -> Optional[str]:
    try:
        t = ast.parse(new_class_src)
    except Exception:
        return None
    if not t.body or not isinstance(t.body[0], ast.ClassDef):
        return None
    lines = src.splitlines()
    i0 = max(0, cls_loc.lineno - 1)
    i1 = min(len(lines), cls_loc.end_lineno)
    before = lines[:i0]
    after = lines[i1:]
    patched = normalize_indentation(new_class_src, cls_loc.indent)
    new_lines = before + patched.splitlines() + after
    return "\n".join(new_lines)

# -----------------------
# Local syntax heuristics
# -----------------------
class LocalHeuristicsFixer:
    _MISSING_COLON = re.compile(r"^\s*(def|class|if|elif|else|for|while|try|except|with)\b[^:]*$")
    _UNBALANCED_PARENS = {"(": ")", "[": "]", "{": "}"}

    def try_fix(self, code: str, stdout: str, stderr: str) -> Optional[str]:
        line_no = self._extract_line(stderr) or self._extract_line(stdout)
        if not line_no:
            return None
        lines = code.splitlines()
        if not (1 <= line_no <= len(lines)):
            return None

        original = lines[line_no - 1]
        candidate = original

        # 1) Add missing colon
        if self._MISSING_COLON.match(candidate.strip()):
            candidate = candidate.rstrip() + ":"

        # 2) Balance quotes/brackets on the line
        candidate = self._balance_quotes(candidate)
        candidate = self._balance_brackets(candidate)

        # 3) Indentation nudge
        if "IndentationError" in (stderr or ""):
            prev_indent = self._previous_indent(lines, line_no - 1)
            cur_indent = len(original) - len(original.lstrip(" "))
            if prev_indent is not None and cur_indent not in (prev_indent, prev_indent + 4):
                new_indent = prev_indent if abs(cur_indent - prev_indent) < abs(cur_indent - (prev_indent + 4)) else prev_indent + 4
                candidate = " " * max(0, new_indent) + original.lstrip(" ")

        if candidate != original:
            new_lines = lines[:]
            new_lines[line_no - 1] = candidate
            new_code = "\n".join(new_lines)
            if self._is_valid(new_code):
                logger.info("[Heuristics] Localized syntax fix at line %d", line_no)
                logger.debug("  from: %s\n  to  : %s", original, candidate)
                return new_code
        return None

    @staticmethod
    def _extract_line(text: str) -> Optional[int]:
        m = re.search(r"line\s+(\d+)", text or "")
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        return None

    @staticmethod
    def _is_valid(code: str) -> bool:
        try:
            ast.parse(code)
            return True
        except Exception:
            return False

    @staticmethod
    def _previous_indent(lines: List[str], idx: int) -> Optional[int]:
        for i in range(idx - 1, -1, -1):
            if lines[i].strip():
                return len(lines[i]) - len(lines[i].lstrip(" "))
        return None

    @classmethod
    def _balance_quotes(cls, s: str) -> str:
        for q in ("'", '"'):
            if s.count(q) % 2 != 0:
                s += q
        return s

    @classmethod
    def _balance_brackets(cls, s: str) -> str:
        stack = []
        for ch in s:
            if ch in cls._UNBALANCED_PARENS:
                stack.append(cls._UNBALANCED_PARENS[ch])
            elif stack and ch == stack[-1]:
                stack.pop()
        for missing in reversed(stack):
            s += missing
        return s

# -----------------------
# LLM client
# -----------------------
_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

def extract_code_block(text: str) -> Optional[str]:
    if not text:
        return None
    m = _CODE_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    return None

def _llm_available() -> bool:
    return not LLM_DISABLED and bool(LLM_ENDPOINT)

class LLMClient:
    def __init__(self, endpoint: str, timeout: float, retries: int):
        self.endpoint = endpoint
        self.timeout = timeout
        self.retries = retries

    @staticmethod
    def _extract_text(data: Dict[str, Any]) -> str:
        # Gemini-ish
        cands = data.get("candidates") or []
        for c in cands:
            content = c.get("content") or {}
            parts = content.get("parts") or []
            texts = [p.get("text") for p in parts if isinstance(p.get("text"), str)]
            if texts:
                return "\n".join(texts)
        for k in ("output_text", "text", "message"):
            if isinstance(data.get(k), str):
                return data[k]
        return json.dumps(data)

    def _post(self, prompt: str) -> Tuple[Optional[str], Optional[bool]]:
        if not _llm_available():
            return None, None
        try:
            import requests
        except Exception:
            logger.warning("[LLM] 'requests' not available; skipping.")
            return None, None

        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        headers = {"Content-Type": "application/json"}
        proxy_ok: Optional[bool] = None

        for attempt in range(1, self.retries + 1):
            logger.info("[LLM] POST attempt %d/%d", attempt, self.retries)
            try:
                resp = requests.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
                proxy_ok = resp.status_code < 500
                if resp.status_code >= 400:
                    logger.warning("[LLM] HTTP %s: %.500s", resp.status_code, resp.text)
                    continue
                data = resp.json()
                text = self._extract_text(data)
                return text, proxy_ok
            except Exception as e:
                logger.error("[LLM] Error: %s", e)
                proxy_ok = False
        return None, proxy_ok

    def analyze_intent(self, code: str) -> Optional[str]:
        prompt = (
            "You are an expert code analyst. Give one concise sentence describing the primary purpose of this Python program.\n\n"
            "```python\n" + code + "\n```"
        )
        text, _ = self._post(prompt)
        return (text or "").strip() or None

    def instrument_for_timeout(self, code: str) -> Optional[str]:
        prompt = (
            "The following Python script likely has an infinite loop or deadlock.\n"
            "Instrument it with print statements (function entries/exits, loop heads, before/after awaits).\n"
            "Do NOT change logic. Return the full code in one ```python block. No explanations.\n\n"
            "```python\n" + code + "\n```"
        )
        text, _ = self._post(prompt)
        return extract_code_block(text or "") if text else None

    def summarize_trace(self, trace: str) -> Optional[str]:
        prompt = "Summarize the likely cause of the timeout from this execution trace in one sentence.\n\n" + trace
        text, _ = self._post(prompt)
        return (text or "").strip() or None

    def propose_minimal_fixes(
        self,
        full_code: str,
        focused_function_src: Optional[str],
        focused_function_name: Optional[str],
        failing_slices: List[Tuple[str, int, str, str]],
        stdout: str,
        stderr: str,
        n: int = 2,
    ) -> Tuple[List[str], Optional[bool]]:
        """
        Ask for up to n minimal fixes.
        If `focused_function_src` is provided, request **ONLY that function** to be returned.
        Otherwise, allow either single-function or full-file minimal fix.
        """
        rules_common = (
            "RULES:\n"
            "1) Fix exactly the shown error. Make the smallest possible change.\n"
            "2) Do NOT rename functions/classes or change public signatures.\n"
            "3) Preserve concurrency primitives (async/await, Queue).\n"
            "4) Return code in a single ```python block. No extra commentary.\n"
        )

        if focused_function_src and focused_function_name:
            prompt = (
                "A Python program failed. The error appears to be inside the following function.\n"
                "Return the **entire corrected definition of this function only**, with the SAME name and parameters.\n\n"
                f"{rules_common}"
                "\n\n--- STDERR ---\n" + (stderr or "") +
                "\n\n--- STDOUT ---\n" + (stdout or "") +
                "\n\n--- FUNCTION TO FIX ---\n" + focused_function_src
            )
        else:
            slices_text = []
            for s, ln, fn, fh in failing_slices:
                header = f"# Context from {fh or '<unknown>'} around line {ln}, function {fn}:\n"
                slices_text.append(header + s)
            joined = "\n\n# ----\n\n".join(slices_text) or full_code
            prompt = (
                "A Python program failed. Here is the failure output and the relevant code context.\n\n"
                f"{rules_common}"
                "\n\n--- STDERR ---\n" + (stderr or "") +
                "\n\n--- STDOUT ---\n" + (stdout or "") +
                "\n\n--- CODE CONTEXT ---\n" + joined
            )

        text, proxy_ok = self._post(prompt)
        candidates: List[str] = []
        first = extract_code_block(text or "") or (text.strip() if text else "")
        if first:
            candidates.append(first)

        # Optional variants
        for alt_idx in range(1, max(1, n)):
            alt_prompt = prompt + f"\n\n# Variant {alt_idx+1}: Provide an alternative minimal fix."
            text_alt, _ = self._post(alt_prompt)
            if not text_alt:
                continue
            fixed = extract_code_block(text_alt) or text_alt.strip()
            if fixed and fixed not in candidates:
                candidates.append(fixed)

        return candidates[:n], proxy_ok

# -----------------------
# Candidate evaluation
# -----------------------
@dataclass
class EvalResult:
    ok: bool
    score: float
    rc: int
    stdout: str
    stderr: str
    reason: str

def evaluate_result(rc: int, out: str, err: str) -> EvalResult:
    ok, reason = analyze_run(rc, out, err)
    fail_tokens = len(_FAIL_PAT.findall(out))
    tb = 1 if _TRACEBACK_PAT.search(out) else 0
    score = (0 if ok else 10) + fail_tokens * 5 + tb * 5 + (0 if rc == 0 else 7) + (0 if not err.strip() else 8)
    return EvalResult(ok=ok, score=score, rc=rc, stdout=out, stderr=err, reason=reason)

# -----------------------
# Main debug loop
# -----------------------
def debug_target() -> None:
    logger.info("[Main] Target path: %s", TARGET_FILE)
    if not TARGET_FILE.exists():
        logger.error("[Main] Target file does not exist.")
        sys.exit(2)

    original_src = load_text(TARGET_FILE)
    current_src = original_src
    out_fp = TARGET_FILE.with_name(TARGET_FILE.stem + "_fixed.py")

    llm = LLMClient(LLM_ENDPOINT, timeout=LLM_TIMEOUT, retries=LLM_RETRIES)
    heur = LocalHeuristicsFixer()

    # Program intent (log only)
    if _llm_available():
        intent = llm.analyze_intent(original_src)
        if intent:
            logger.info("[Intent] %s", intent)
    else:
        logger.info("[Intent] LLM disabled; skipping.")

    success = False
    validator_success = False
    tested_success = False
    repair_attempts = 0
    stop_reason = "unknown"
    final_error = ""
    proxy_healthy: Optional[bool] = None
    timeout_summary: Optional[str] = None

    for it in range(1, MAX_ITERS + 1):
        logger.info("═══ Iteration %d/%d", it, MAX_ITERS)

        tmp_script = temp_script_path(TARGET_FILE.parent)
        write_text(tmp_script, current_src)
        rc, out, err = run_python_file(tmp_script, EXEC_TIMEOUT)
        logger.info("[Run] RC=%s", rc)
        if err:
            logger.debug("[stderr]\n%s", err)

        is_ok, reason = analyze_run(rc, out, err)
        if is_ok:
            success = True
            validator_success = True
            stop_reason = "success"
            break

        # Classification
        kind = classify(err, out)
        logger.info("[Classify] %s", kind)

        # Timeout diagnosis: instrument + summarize (once per iteration)
        focused_function_name: Optional[str] = None
        if kind == "timeout" and _llm_available():
            logger.info("[Diag] Timeout detected; attempting to instrument and summarize.")
            instrumented = llm.instrument_for_timeout(current_src)
            if instrumented:
                tmp_i = temp_script_path(TARGET_FILE.parent)
                write_text(tmp_i, instrumented)
                i_rc, i_out, i_err = run_python_file(tmp_i, EXEC_TIMEOUT)
                trace = "--- EXECUTION TRACE ---\n" + as_text(i_out) + "\n" + as_text(i_err) + "\n--- END TRACE ---"
                timeout_summary = llm.summarize_trace(trace) or timeout_summary
                if timeout_summary:
                    logger.info("[Diag] Summary: %s", timeout_summary)
                    focused_function_name = function_from_summary(timeout_summary)
            else:
                logger.info("[Diag] Instrumentation unavailable.")
        elif kind == "timeout":
            logger.info("[Diag] LLM disabled; skipping instrumentation.")

        # Try local syntax heuristics first
        fixed_candidates: List[str] = []
        if kind == "syntax":
            fx = heur.try_fix(current_src, out, err)
            if fx:
                fixed_candidates = [fx]

        # Prepare focused function source if we have a function name
        focused_function_src: Optional[str] = None
        func_loc: Optional[FuncLocate] = None
        if focused_function_name:
            loc = find_function(current_src, focused_function_name)
            if loc:
                func_loc = loc
                # Grab exact function source slice
                lines = current_src.splitlines()
                i0 = max(0, loc.lineno - 1)
                i1 = min(len(lines), loc.end_lineno)
                focused_function_src = "\n".join(lines[i0:i1])

        # If no local candidate, ask LLM for minimal fixes (favor function-only)
        if not fixed_candidates and _llm_available():
            frames = extract_frames(out + "\n" + err)
            # Build slices for context (fallback when no focused function)
            slices: List[Tuple[str, int, str, str]] = []
            if frames:
                for fpath, line, fname in frames[:3]:
                    try:
                        slices.append((slice_context(current_src, line), line, fname, Path(fpath).name))
                    except Exception:
                        continue

            cands, proxy_ok = llm.propose_minimal_fixes(
                full_code=current_src,
                focused_function_src=focused_function_src,
                focused_function_name=focused_function_name,
                failing_slices=slices,
                stdout=out,
                stderr=err,
                n=max(1, TOURNAMENT_CANDIDATES),
            )
            if proxy_ok is not None:
                proxy_healthy = proxy_ok if proxy_healthy is None else (proxy_healthy or proxy_ok)
            fixed_candidates.extend(cands)

        if not fixed_candidates:
            logger.warning("[Debugger] No fix generated; stopping.")
            stop_reason = "no_fix_generated"
            final_error = (err or "") + ("\n" + out if out else "")
            break

        # Normalize candidates:
        # If candidate looks like a single function/class and we have a matching locator,
        # splice it into the original text to keep diffs minimal.
        normed_candidates: List[str] = []
        for cand in fixed_candidates:
            cand = cand.strip()
            if not cand:
                continue

            # Detect single-def unit
            unit_type = None
            name = None
            try:
                t = ast.parse(cand)
                if len(t.body) == 1:
                    node = t.body[0]
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        unit_type, name = "function", node.name
                    elif isinstance(node, ast.ClassDef):
                        unit_type, name = "class", node.name
            except Exception:
                unit_type = None

            if unit_type == "function" and name:
                loc = func_loc or find_function(current_src, name)
                if loc:
                    spliced = replace_function_text(current_src, loc, cand)
                    if spliced:
                        normed_candidates.append(spliced)
                        continue
            if unit_type == "class" and name:
                cls_loc = find_class(current_src, name)
                if cls_loc:
                    spliced = replace_class_text(current_src, cls_loc, cand)
                    if spliced:
                        normed_candidates.append(spliced)
                        continue

            # Otherwise assume full-file replacement
            normed_candidates.append(cand)

        # Guardrails & diff budget & evaluate
        frames = extract_frames(out + "\n" + err)
        allowed_syms = allowed_symbols_from_frames(frames)
        if focused_function_name:
            allowed_syms.add(focused_function_name)

        best_src = None
        best_eval: Optional[EvalResult] = None
        for idx, cand in enumerate(normed_candidates):
            if cand.strip() == current_src.strip():
                logger.info("[Cand %d] Identical; skipping.", idx + 1)
                continue

            # Dynamic diff budget: if timeout + function-scoped splice, allow a bit more (e.g., 45%)
            local_threshold = DIFF_THRESHOLD_PERCENT
            if kind == "timeout" and focused_function_name:
                local_threshold = max(local_threshold, 45.0)

            if is_change_too_large(current_src, cand, local_threshold):
                logger.info("[Cand %d] Diff too large; skipping.", idx + 1)
                continue

            # Public API guardrail (function/class names & argcounts)
            if not guardrails_ok(current_src, cand, allowed_syms):
                logger.info("[Cand %d] Guardrails failed; skipping.", idx + 1)
                continue

            # Score this candidate by actually running it
            tmp_c = temp_script_path(TARGET_FILE.parent)
            write_text(tmp_c, cand)
            c_rc, c_out, c_err = run_python_file(tmp_c, EXEC_TIMEOUT)
            ev = evaluate_result(c_rc, c_out, c_err)
            logger.info("[Cand %d] score=%.1f ok=%s reason=%s", idx + 1, ev.score, ev.ok, ev.reason)

            cur_eval = evaluate_result(rc, out, err)
            if ev.score < cur_eval.score:
                if best_eval is None or ev.score < best_eval.score:
                    best_src, best_eval = cand, ev

        if best_src is None:
            logger.warning("[Debugger] All candidates rejected (guardrails/diff/score).")
            stop_reason = "rejected_candidates"
            final_error = (err or "") + ("\n" + out if out else "")
            break

        logger.info(
            "[Debugger] Fix accepted (score improved)."
        )
        current_src = best_src
        repair_attempts += 1

    # Save final candidate
    write_text(out_fp, current_src)
    logger.info("[Main] Saved fixed code → %s", out_fp)

    # Validate final file
    rc2, out2, err2 = run_python_file(out_fp, TEST_TIMEOUT)
    tested_success, test_reason = analyze_run(rc2, out2, err2)
    logger.info("[Test] objective='pass & no stderr' -> %s (%s)", tested_success, test_reason)

    if not success:
        final_error = final_error or (err2 or "") + ("\n" + out2 if out2 else "")

    result = {
        "success": success,
        "validator": validator_success,
        "test_passed": tested_success,
        "iters": MAX_ITERS,
        "output": str(out_fp),
        "repair_attempts": repair_attempts,
        "final_error": final_error,
        "log_files_found": [],
        "proxy_healthy": None,  # set above when available
        "stop_reason": stop_reason,
    }
    try:
        RESULT_JSON_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("[Main] Failed to write result JSON: %s", e)

    print(json.dumps(result, indent=2))
    sys.exit(0 if (success and tested_success) else 1)

# -----------------------
# Guardrails: public API & locality
# -----------------------
def _public_api(tree: ast.AST) -> set[Tuple[str, int]]:
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            if not n.name.startswith("_"):
                names.add((n.name, len(n.args.args)))
        if isinstance(n, ast.AsyncFunctionDef):
            if not n.name.startswith("_"):
                names.add((n.name, len(n.args.args)))
    return names

def guardrails_ok(original: str, candidate: str, allowed_symbols: set[str]) -> bool:
    try:
        o_api = _public_api(ast.parse(original))
        c_api = _public_api(ast.parse(candidate))
    except Exception:
        logger.warning("[Guardrails] Could not parse code; rejecting.")
        return False

    if o_api != c_api:
        logger.warning("[Guardrails] Public API changed: %s -> %s", o_api, c_api)
        return False

    # Locality: if we know the failing symbols, ensure the diff touches them
    if allowed_symbols:
        diff = "\n".join(difflib.unified_diff(original.splitlines(), candidate.splitlines(), lineterm=""))
        if not any(
            sym and (f"def {sym}" in diff or f"async def {sym}" in diff or (sym in diff))
            for sym in allowed_symbols
        ):
            logger.warning("[Guardrails] Changes don't touch failing symbols %s.", allowed_symbols)
            return False

    return True

# -----------------------
# Entrypoint
# -----------------------
if __name__ == "__main__":
    # Nudge asyncio once to produce selector line (parity with earlier logs on macOS)
    try:
        import asyncio
        async def _kick(): await asyncio.sleep(0)
        asyncio.run(_kick())
    except Exception:
        pass
    debug_target()
