"""Shared retry loop: call the model, validate JSON, feed the error back once or twice."""
from __future__ import annotations

import json
import time
from typing import Any, Callable

import jsonschema

from cardcutter.llm.base import BackendError, LLMResult, Prompt, parse_json

# call(prompt, history) -> (text, usage dict, extra dict)
CallFn = Callable[[Prompt, list[dict[str, str]]], tuple[str, dict[str, int], dict[str, Any]]]


def generate_with_retries(
    call: CallFn,
    prompt: Prompt,
    schema: dict[str, Any],
    *,
    backend: str,
    model: str,
    max_attempts: int = 2,
) -> LLMResult:
    history: list[dict[str, str]] = []
    totals = {"input": 0, "output": 0, "thinking": 0}
    t0 = time.time()
    last_err = ""
    extra: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        text, usage, extra = call(prompt, history)
        for k in totals:
            totals[k] += usage.get(k, 0)
        try:
            data = parse_json(text, schema)
        except (json.JSONDecodeError, jsonschema.ValidationError) as e:
            last_err = e.message if isinstance(e, jsonschema.ValidationError) else str(e)
            history = [
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content": f"That reply was not valid JSON for the schema ({last_err[:200]}). "
                                            "Reply again with only the JSON object."},
            ]
            continue
        return LLMResult(
            data=data, text=text, backend=backend, model=model,
            input_tokens=totals["input"], output_tokens=totals["output"], thinking_tokens=totals["thinking"],
            seconds=time.time() - t0, attempts=attempt, extra=extra,
        )
    raise BackendError(f"{backend}/{model}: no valid JSON after {max_attempts} attempts: {last_err[:300]}")
