#!/usr/bin/env python3
"""Opt-in lightweight behavioral regression checks against a running Hermes gateway.

This executes real agent turns, uses provider tokens, and may mutate its state.
It is NOT run by CI. For high-stakes testing, use an isolated Hermes deployment.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "cases.json"
REPORTS = ROOT / "reports"


def match_answer(case: dict, answer: str) -> bool:
    normalized = answer.strip()
    if "expected_exact" in case:
        return normalized == case["expected_exact"]
    if "expected_contains" in case:
        return all(fragment in normalized for fragment in case["expected_contains"])
    if "expected_json" in case:
        try:
            return json.loads(normalized) == case["expected_json"]
        except ValueError:
            return False
    raise ValueError(f"Case {case.get('id', '?')} has no expectation")


def invoke(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT,
                          text=True, capture_output=True, timeout=timeout,
                          check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--include-output", action="store_true",
                        help="Store raw agent output in ignored reports directory.")
    args = parser.parse_args()
    if not (0 <= args.min_pass_rate <= 1):
        parser.error("--min-pass-rate must be between 0 and 1")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if not (ROOT / ".env").exists():
        parser.error("Missing .env. Run make init first.")
    try:
        cases = json.loads(args.cases.read_text())
        if not isinstance(cases, list) or not cases:
            parser.error("Expected a nonempty JSON array of cases.")
    except (OSError, ValueError) as exc:
        parser.error(f"Cannot read cases: {exc}")
    status = invoke("ps", "--status", "running", "--quiet", "hermes")
    if status.returncode or not status.stdout.strip():
        parser.error("Gateway must be running: make up")
    print("This executes real agent turns against the running deployment.")
    print("Provider charges and Hermes state changes may occur. Use a test deployment.")
    results = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("prompt"), str):
            parser.error("Each case must be an object containing a prompt string.")
        name = case.get("id", f"case-{len(results) + 1}")
        start = time.monotonic()
        try:
            answer = invoke("exec", "-T", "hermes", "hermes", "chat",
                            "--oneshot", "--quiet", "--query", case["prompt"],
                            timeout=args.timeout)
            ok = answer.returncode == 0 and match_answer(case, answer.stdout)
            output = answer.stdout
            error = answer.stderr[-1000:]
            code = answer.returncode
        except subprocess.TimeoutExpired:
            ok, output, error, code = False, "", "timeout", -1
        record = {
            "id": name,
            "passed": ok,
            "seconds": round(time.monotonic() - start, 2),
            "exit_code": code,
            "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
        }
        if args.include_output:
            record["output"] = output
            record["stderr"] = error
        results.append(record)
        print(f"{'PASS' if ok else 'FAIL'} {name} ({record['seconds']}s)")
    passing = sum(r["passed"] for r in results)
    rate = passing / len(results)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    REPORTS.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_path = REPORTS / f"eval-{stamp}.json"
    git_revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                                  capture_output=True, check=False).stdout.strip()
    report = {
        "timestamp_utc": stamp, "git_revision": git_revision,
        "cases_file": str(args.cases), "pass_rate": rate, "cases": results
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    report_path.chmod(0o600)
    print(f"Pass rate: {passing}/{len(results)} ({rate:.0%}). Report: {report_path}")
    return 0 if rate >= args.min_pass_rate else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (FileNotFoundError, OSError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        sys.exit(2)
