#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 HEYI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate one advanced capability against a dedicated running M03 worker."""

import argparse
import base64
import io
import json
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image
from prometheus_client.parser import text_string_to_metric_families

FRONTEND = "http://127.0.0.1:8010"
WORKER = "http://127.0.0.1:8090"


def request(url, payload=None, method=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)


def complete(model, prompt, **kwargs):
    result = request(
        FRONTEND + "/v1/completions",
        dict(model=model, prompt=prompt, max_tokens=16, temperature=0, **kwargs),
    )
    assert (
        result["choices"][0]["text"].strip()
        and result["usage"]["completion_tokens"] > 0
    ), result
    return result


def wait_model(name, present):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        models = request(FRONTEND + "/v1/models")
        if (name in [row["id"] for row in models["data"]]) == present:
            return
        time.sleep(0.2)
    raise AssertionError(f"Model discovery did not converge: {name}, present={present}")


def lora(args, report):
    adapter = "m03-lora"
    source = f"file:///var/cache/dynamo-{args.platform}/models/m03-lora"
    report["load"] = request(
        WORKER + "/v1/loras", dict(lora_name=adapter, source={"uri": source})
    )
    wait_model(adapter, True)
    rng = random.Random(918271)
    block = 128 if args.platform == "ascend" else 64
    prompt = [rng.randint(1000, 20000) for _ in range(2 * block)] + [100]
    # Identical token prefixes must stay isolated by adapter identity.
    report["base_cold"] = complete("m03-model", prompt)
    report["adapter_cold"] = complete(adapter, prompt)
    assert (
        report["adapter_cold"]["usage"]["prompt_tokens_details"]["cached_tokens"] == 0
    ), report
    report["adapter_warm"] = complete(adapter, prompt)
    assert (
        report["adapter_warm"]["usage"]["prompt_tokens_details"]["cached_tokens"]
        == 2 * block
    ), report
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda model: complete(model, "The capital of France is"),
                ["m03-model", adapter] * 4,
            )
        )
    report["concurrent_requests"] = len(results)
    try:
        request(
            WORKER + "/v1/loras",
            dict(
                lora_name="m03-invalid", source={"uri": "file:///missing-m03-adapter"}
            ),
        )
    except urllib.error.HTTPError as error:
        assert error.code >= 400
        report["invalid_load"] = dict(status=error.code, body=error.read().decode())
    else:
        raise AssertionError("Missing adapter was accepted")
    wait_model("m03-invalid", False)
    report["list"] = request(WORKER + "/v1/loras")
    report["unload"] = request(WORKER + "/v1/loras/" + adapter, method="DELETE")
    wait_model(adapter, False)
    report["after_unload"] = complete("m03-model", "The capital of France is")
    # Reload verifies that removal did not leave stale engine/discovery state.
    report["reload"] = request(
        WORKER + "/v1/loras", dict(lora_name=adapter, source={"uri": source})
    )
    wait_model(adapter, True)
    report["after_reload"] = complete(adapter, "The capital of France is")
    request(WORKER + "/v1/loras/" + adapter, method="DELETE")


def vision(args, report):
    responses = []
    payloads = []
    for color in ("red", "blue"):
        buffer = io.BytesIO()
        Image.new("RGB", (448, 448), color=color).save(buffer, format="PNG")
        data = base64.b64encode(buffer.getvalue()).decode()
        payload = dict(
            model="m03-model",
            messages=[
                dict(
                    role="user",
                    content=[
                        dict(
                            type="text",
                            text="What color is the image? Answer with one color word.",
                        ),
                        dict(
                            type="image_url",
                            image_url={"url": "data:image/png;base64," + data},
                        ),
                    ],
                )
            ],
            max_tokens=24,
            temperature=0,
        )
        payloads.append(payload)
        result = request(FRONTEND + "/v1/chat/completions", payload)
        content = result["choices"][0]["message"]["content"]
        assert color in content.lower(), (color, result)
        assert result["usage"]["completion_tokens"] > 0, result
        responses.append(dict(color=color, response=result))
    report["images"] = responses
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda payload: request(FRONTEND + "/v1/chat/completions", payload),
                payloads * 2,
            )
        )
    for color, result in zip(("red", "blue") * 2, results):
        assert color in result["choices"][0]["message"]["content"].lower(), result
    report["concurrent_requests"] = len(results)
    report["warm_image"] = results[0]
    assert results[0]["usage"]["prompt_tokens_details"]["cached_tokens"] > 0, results[0]
    payload = dict(payloads[0], stream=True)
    req = urllib.request.Request(
        FRONTEND + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    chunks = []
    done = False
    with urllib.request.urlopen(req, timeout=120) as response:
        for line in response:
            if not line.startswith(b"data: "):
                continue
            data = line[6:].strip()
            if data == b"[DONE]":
                done = True
                break
            chunk = json.loads(data)
            assert "error" not in chunk, chunk
            chunks.append(chunk)
    assert done and chunks, chunks
    text = "".join(
        choice.get("delta", {}).get("content") or ""
        for chunk in chunks
        for choice in chunk.get("choices", [])
    )
    assert "red" in text.lower(), text
    assert any(
        choice.get("finish_reason") in ("stop", "length")
        for chunk in chunks
        for choice in chunk.get("choices", [])
    ), chunks
    report["stream"] = dict(done=done, chunks=len(chunks), text=text)

    def cancellations():
        with urllib.request.urlopen(WORKER + "/metrics", timeout=10) as response:
            metrics = response.read().decode()
        return sum(
            sample.value
            for family in text_string_to_metric_families(metrics)
            for sample in family.samples
            if sample.name == "dynamo_component_cancellation_total"
        )

    before = cancellations()
    cancel_payload = dict(payloads[0], stream=True, max_tokens=2048, ignore_eos=True)
    cancel_request = urllib.request.Request(
        FRONTEND + "/v1/chat/completions",
        data=json.dumps(cancel_payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(cancel_request, timeout=120) as response:
        for line in response:
            if not line.startswith(b"data: "):
                continue
            data = line[6:].strip()
            assert data != b"[DONE]", "Image stream finished before cancellation"
            chunk = json.loads(data)
            assert "error" not in chunk, chunk
            if any(
                choice.get("delta", {}).get("content")
                for choice in chunk.get("choices", [])
            ):
                break
        else:
            raise AssertionError("Image cancellation stream produced no text")
    deadline = time.monotonic() + 20
    while cancellations() <= before:
        assert time.monotonic() < deadline, "Worker did not observe image cancellation"
        time.sleep(0.2)
    report["cancellation"] = dict(before=before, after=cancellations())
    # Malformed media must fail without taking down a worker.
    invalid = dict(
        payloads[0],
        messages=[
            dict(
                role="user",
                content=[
                    dict(
                        type="image_url",
                        image_url={"url": "data:image/png;base64,bm90LWFuLWltYWdl"},
                    )
                ],
            )
        ],
    )
    try:
        request(FRONTEND + "/v1/chat/completions", invalid)
    except urllib.error.HTTPError as error:
        report["invalid_image"] = dict(status=error.code, body=error.read().decode())
    else:
        raise AssertionError("Malformed image was accepted")
    report["after_invalid_image"] = request(
        FRONTEND + "/v1/chat/completions", payloads[1]
    )
    assert (
        "blue"
        in report["after_invalid_image"]["choices"][0]["message"]["content"].lower()
    )


def spec(args, report):
    prompt = (
        "Continue the repeating sequence without explanation: "
        + "one two three four " * 60
    )
    payload = dict(
        model="m03-model", prompt=prompt, max_tokens=128, temperature=0, ignore_eos=True
    )
    report["response"] = request(FRONTEND + "/v1/completions", payload)
    assert report["response"]["usage"]["completion_tokens"] == 128, report
    time.sleep(1)
    with urllib.request.urlopen(WORKER + "/metrics", timeout=10) as response:
        metrics = response.read().decode()
    rows = {
        sample.name: sample.value
        for family in text_string_to_metric_families(metrics)
        for sample in family.samples
        if "spec_decode" in sample.name
    }
    report["spec_metrics"] = rows
    assert any("num_drafts" in name and value > 0 for name, value in rows.items()), rows
    assert any(
        "accepted_tokens_total" in name and value > 0 for name, value in rows.items()
    ), rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("lora", "vision", "spec"))
    parser.add_argument(
        "--platform", choices=("metax", "ascend", "cuda"), required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = dict(passed=False, kind=args.kind, platform=args.platform)
    try:
        {"lora": lora, "vision": vision, "spec": spec}[args.kind](args, report)
        report["passed"] = True
    except Exception as error:
        report["error"] = repr(error)
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
