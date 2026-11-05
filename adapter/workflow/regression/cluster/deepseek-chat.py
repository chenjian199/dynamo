#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Natural-language smoke on both DeepSeek configurations, not a quality benchmark."""

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CASES = [
    ("What is 6 multiplied by 7? Answer with only the number.", "42"),
    ("What is the capital of France? Reply with only the city name.", "paris"),
    ("中国的首都是哪里？只回答城市名称。", "北京"),
    (
        "Sort these integers in ascending order: 3, 1, 2. Reply only as 1, 2, 3.",
        "1, 2, 3",
    ),
]


def main():
    report = {
        "passed": False,
        "qualification": "Four simple semantic smoke cases only",
        "models": {},
    }
    try:
        for prefix in ("ascend-deepseek", "ascend-deepseek-mtp"):

            def request(case):
                prompt, expected = case
                req = urllib.request.Request(
                    f"http://{prefix}-frontend:8000/v1/chat/completions",
                    data=json.dumps(
                        {
                            "model": prefix + "-model",
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0,
                            "seed": 42,
                            "max_tokens": 32,
                            "chat_template_kwargs": {"thinking": False},
                        }
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=180) as response:
                    result = json.load(response)
                text = result["choices"][0]["message"].get("content", "") or ""
                return {
                    "prompt": prompt,
                    "expected": expected,
                    "response": result,
                    "semantic_match": expected.replace(" ", "")
                    in text.lower().replace(" ", ""),
                }

            with ThreadPoolExecutor(max_workers=4) as pool:
                rows = list(pool.map(request, CASES * 3))
            report["models"][prefix] = rows
            assert all(r["semantic_match"] for r in rows), f"{prefix} semantic smoke"
        report["passed"] = True
    except Exception as exc:
        report["error"] = repr(exc)
        raise
    finally:
        print("REGRESSION_RESULT=" + json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
