#!/usr/bin/env python3
"""engine.py — single entry point for all Claude calls in foodbot.

Engine choice (same convention as JobBot):
  • ANTHROPIC_API_KEY set  → anthropic SDK, direct API billing
  • otherwise              → `claude` CLI on the subscription credit

Every call is logged to public.usage_log — the SAME ledger JobBot uses —
so the evening report can show combined monthly spend vs MONTHLY_CREDIT_USD.
Commands are prefixed 'food_' to separate FoodBot's share.
"""
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

from dotenv import load_dotenv

import db

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

MODEL = os.environ.get("VISION_MODEL", "claude-sonnet-4-5")

# $/Mtok (input, output) for spend estimates on the SDK path.
# CLI path reports exact total_cost_usd itself.
_PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (15.0, 75.0)}


def _price(model):
    for k, v in _PRICES.items():
        if k in model:
            return v
    return _PRICES["sonnet"]


def _log(label, cost, tokens):
    try:
        db.log_usage(label, cost, tokens)
    except Exception:
        pass  # never let spend logging break the pipeline


def _via_sdk(prompt, label, image_bytes):
    import anthropic
    client = anthropic.Anthropic()
    content = []
    if image_bytes:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.b64encode(image_bytes).decode()},
        })
    content.append({"type": "text", "text": prompt})
    msg = client.messages.create(
        model=MODEL, max_tokens=1000,
        messages=[{"role": "user", "content": content}])
    u = msg.usage
    pi, po = _price(MODEL)
    cache_w = getattr(u, "cache_creation_input_tokens", 0) or 0
    cache_r = getattr(u, "cache_read_input_tokens", 0) or 0
    cost = (u.input_tokens * pi + u.output_tokens * po
            + cache_w * pi * 1.25 + cache_r * pi * 0.1) / 1_000_000
    _log(label, round(cost, 5),
         u.input_tokens + u.output_tokens + cache_w + cache_r)
    return "".join(b.text for b in msg.content if b.type == "text")


def _via_cli(prompt, label, image_bytes):
    tmp = None
    try:
        if image_bytes:
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp.write(image_bytes)
            tmp.close()
            prompt += f"\nСначала открой (Read) фото еды: {tmp.name}"
        cmd = ["claude", "-p", prompt, "--output-format", "json",
               "--model", MODEL, "--max-turns", "4",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        if image_bytes:
            cmd += ["--allowedTools", "Read"]
        else:
            cmd += ["--disallowedTools",
                    "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI failed: {(proc.stderr or proc.stdout)[:300]}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return proc.stdout
        cost = payload.get("total_cost_usd")
        u = payload.get("usage", {}) or {}
        toks = (u.get("input_tokens", 0) + u.get("output_tokens", 0)
                + u.get("cache_read_input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0))
        if cost is not None:
            _log(label, cost, toks)
        return payload.get("result", "") or proc.stdout
    finally:
        if tmp:
            os.unlink(tmp.name)


def run(prompt: str, label: str, image_bytes: bytes | None = None) -> str:
    """Call Claude, log spend under `label`, return response text."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _via_sdk(prompt, label, image_bytes)
    return _via_cli(prompt, label, image_bytes)
