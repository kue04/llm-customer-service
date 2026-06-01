from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib import error, request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = PROJECT_ROOT / "data" / "chat_prompt_smoke_cases.jsonl"
REQUIRED_CASE_FIELDS = {
    "id",
    "query",
    "expected_top1_intent",
    "expected_reply_keywords",
    "forbidden_reply_keywords",
}


def load_smoke_cases(path: Path | str = DEFAULT_CASES_PATH) -> list[dict]:
    cases = []
    seen_ids = set()
    with Path(path).open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue

            case = json.loads(line)
            missing = REQUIRED_CASE_FIELDS - set(case)
            if missing:
                raise ValueError(f"Line {line_number} missing fields: {sorted(missing)}")
            if case["id"] in seen_ids:
                raise ValueError(f"Duplicate smoke case id: {case['id']}")
            if not isinstance(case["expected_reply_keywords"], list):
                raise ValueError(f"Line {line_number} expected_reply_keywords must be a list")
            if not isinstance(case["forbidden_reply_keywords"], list):
                raise ValueError(f"Line {line_number} forbidden_reply_keywords must be a list")

            seen_ids.add(case["id"])
            cases.append(case)
    return cases


def post_chat_prompt(base_url: str, query: str, timeout: float) -> dict:
    url = f"{base_url.rstrip('/')}/chat/prompt"
    payload = json.dumps({"message": query}, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def top1_intent(response: dict) -> str:
    retrieved_items = response.get("retrieved_items") or []
    if not retrieved_items:
        return ""
    return str(retrieved_items[0].get("intent", ""))


def evaluate_response(case: dict, response: dict) -> dict:
    reply = str(response.get("reply", ""))
    actual_top1_intent = top1_intent(response)
    missing_reply_keywords = [
        keyword for keyword in case["expected_reply_keywords"] if keyword not in reply
    ]
    forbidden_reply_hits = [
        keyword for keyword in case["forbidden_reply_keywords"] if keyword in reply
    ]

    failures = []
    if actual_top1_intent != case["expected_top1_intent"]:
        failures.append(
            f"top1 intent expected {case['expected_top1_intent']}, got {actual_top1_intent}"
        )
    if missing_reply_keywords:
        failures.append(f"missing reply keywords: {missing_reply_keywords}")
    if forbidden_reply_hits:
        failures.append(f"forbidden reply hits: {forbidden_reply_hits}")

    return {
        "id": case["id"],
        "query": case["query"],
        "passed": not failures,
        "failures": failures,
        "expected_top1_intent": case["expected_top1_intent"],
        "actual_top1_intent": actual_top1_intent,
        "missing_reply_keywords": missing_reply_keywords,
        "forbidden_reply_hits": forbidden_reply_hits,
        "reply": reply,
    }


def run_smoke_cases(
    cases: list[dict],
    base_url: str,
    timeout: float,
    max_cases: int | None = None,
) -> list[dict]:
    selected_cases = cases[:max_cases] if max_cases else cases
    results = []
    for case in selected_cases:
        try:
            response = post_chat_prompt(base_url, case["query"], timeout)
            results.append(evaluate_response(case, response))
        except (OSError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            results.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "passed": False,
                    "failures": [f"request failed: {exc}"],
                    "expected_top1_intent": case["expected_top1_intent"],
                    "actual_top1_intent": "",
                    "missing_reply_keywords": case["expected_reply_keywords"],
                    "forbidden_reply_hits": [],
                    "reply": "",
                }
            )
    return results


def print_results(results: list[dict], print_passed: bool = False) -> None:
    passed_count = sum(1 for result in results if result["passed"])
    print(f"chat_prompt_smoke: passed={passed_count} total={len(results)}")

    for result in results:
        if result["passed"] and not print_passed:
            continue

        status = "PASS" if result["passed"] else "FAIL"
        print("=" * 80)
        print(f"{status} {result['id']}: {result['query']}")
        print(
            f"intent: expected={result['expected_top1_intent']} "
            f"actual={result['actual_top1_intent']}"
        )
        if result["failures"]:
            for failure in result["failures"]:
                print(f"- {failure}")
        if result["reply"]:
            print(f"reply: {result['reply']}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test the live /chat/prompt API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--print-passed", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = load_smoke_cases(args.cases)
    results = run_smoke_cases(
        cases=cases,
        base_url=args.base_url,
        timeout=args.timeout,
        max_cases=args.max_cases or None,
    )
    print_results(results, print_passed=args.print_passed)
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
