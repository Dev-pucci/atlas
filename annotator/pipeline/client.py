"""OpenRouter chat client with vision support, retries, JSON parsing and cost tracking."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from openai import OpenAI, APIStatusError, APIConnectionError

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class CostTracker:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    by_pass: dict = field(default_factory=dict)

    def add(self, pass_name: str, usage, cost: float | None):
        self.calls += 1
        it = getattr(usage, "prompt_tokens", 0) or 0
        ot = getattr(usage, "completion_tokens", 0) or 0
        self.input_tokens += it
        self.output_tokens += ot
        if cost:
            self.cost_usd += cost
        entry = self.by_pass.setdefault(pass_name, {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
        entry["calls"] += 1
        entry["in"] += it
        entry["out"] += ot
        entry["usd"] += cost or 0.0

    def summary(self) -> str:
        lines = [
            f"API calls: {self.calls} | tokens in/out: {self.input_tokens:,}/{self.output_tokens:,}"
            f" | cost: ${self.cost_usd:.4f}"
        ]
        for name, e in self.by_pass.items():
            lines.append(f"  {name}: {e['calls']} calls, {e['in']:,}/{e['out']:,} tok, ${e['usd']:.4f}")
        return "\n".join(lines)


class Router:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        key_env = self.config["api"]["key_env"]
        api_key = os.environ.get(key_env)
        if not api_key:
            raise SystemExit(
                f"Missing API key: set the {key_env} environment variable.\n"
                f'  PowerShell: $env:{key_env} = "sk-or-..."\n'
                f"  (get a key at https://openrouter.ai/keys)"
            )
        self.client = OpenAI(
            base_url=self.config["api"]["base_url"],
            api_key=api_key,
            timeout=300.0,
        )
        self.cost = CostTracker()
        limits = self.config.get("limits", {})
        self.retries = int(limits.get("request_retries", 3))
        self.max_output = int(limits.get("max_output_tokens", 10000))
        self.reasoning_max = int(limits.get("reasoning_max_tokens", 0))

    def model_for(self, pass_name: str) -> str:
        return self.config["models"][pass_name]

    def chat(self, pass_name: str, messages: list, model: str | None = None) -> str:
        """Send a chat request; returns assistant text. Retries on transient failures."""
        model = model or self.model_for(pass_name)
        messages = _with_cache_control(messages, model)
        extra: dict = {"usage": {"include": True}}
        if self.reasoning_max and model.startswith("google/gemini"):
            extra["reasoning"] = {"max_tokens": self.reasoning_max}
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=self.max_output,
                    extra_body=extra,
                )
                choice = resp.choices[0]
                text = choice.message.content or ""
                if not text.strip():
                    raise RuntimeError(f"Empty response from {model} (finish={choice.finish_reason})")
                if choice.finish_reason == "length":
                    raise RuntimeError(f"Truncated response from {model} (hit max_tokens)")
                cost = None
                if resp.usage is not None:
                    cost = getattr(resp.usage, "cost", None)
                    self.cost.add(pass_name, resp.usage, cost)
                return text
            except (APIStatusError, APIConnectionError, RuntimeError) as e:
                status = getattr(e, "status_code", None)
                if status == 402:
                    raise SystemExit(
                        "OpenRouter says your credit balance is too low for this request.\n"
                        "Add credits at https://openrouter.ai/settings/credits and re-run.\n"
                        f"(cost so far this run: ${self.cost.cost_usd:.4f})"
                    ) from e
                retryable = status in (408, 409, 429, 500, 502, 503, 529) or isinstance(
                    e, (APIConnectionError, RuntimeError)
                )
                last_err = e
                if not retryable or attempt == self.retries:
                    raise
                wait = 2 ** attempt * 2
                print(f"  [retry {attempt + 1}/{self.retries}] {model}: {e} — waiting {wait}s")
                time.sleep(wait)
        raise last_err  # unreachable

    def chat_json(self, pass_name: str, messages: list, model: str | None = None) -> dict | list:
        """Chat expecting a JSON object/array in the reply; parses robustly, retries once."""
        text = self.chat(pass_name, messages, model=model)
        try:
            return parse_json_reply(text)
        except ValueError as e:
            print(f"  [retry] {pass_name}: unparseable JSON reply — asking again")
            text = self.chat(pass_name, messages, model=model)
            return parse_json_reply(text)


def _with_cache_control(messages: list, model: str) -> list:
    """Anthropic models via OpenRouter support prompt caching: mark the (stable) system
    prompt as cacheable so repeated calls in a run pay ~10% for it instead of 100%."""
    if not model.startswith("anthropic/"):
        return messages
    out = []
    for m in messages:
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            m = {
                "role": "system",
                "content": [
                    {"type": "text", "text": m["content"], "cache_control": {"type": "ephemeral"}}
                ],
            }
        out.append(m)
    return out


def parse_json_reply(text: str):
    """Extract JSON from a model reply that may include prose or code fences."""
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fall back: widest {...} or [...] span
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"Could not parse JSON from model reply:\n{text[:800]}")


def image_part(jpeg_bytes: bytes) -> dict:
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}
