"""Anthropic Messages API backend for llm.run().

This is the hosted-build path: a server-side API key instead of the instructor's
Claude Code login. It is not used by the local build unless config.json sets
`"llm_backend": "api"` and ANTHROPIC_API_KEY is in the environment.

It keeps the exact contract of claude_cli.run(): same arguments, same
ClaudeResult, same exceptions. Prompts are built by the callers; this module is
transport only. It uses the official `anthropic` SDK (pip install anthropic),
imported lazily so the local build never needs it.
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Callable

from .claude_cli import ClaudeError, ClaudeResult, NotLoggedIn, parse_json_ex

# Aliases the UI offers -> current model ids. Overridable via Config.api_models.
DEFAULT_MODELS = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}
# Rough list prices per million tokens (input, output), for the cost readout only.
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
MAX_TOKENS = 16000
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp"}


def _client(cfg):
    try:
        import anthropic  # noqa: WPS433  (optional dependency)
    except ImportError as exc:
        raise ClaudeError("The API backend needs the anthropic package: "
                          "pip install anthropic") from exc
    key_env = (getattr(cfg, "anthropic_api_key_env", "") or "ANTHROPIC_API_KEY")
    key = os.environ.get(key_env)
    if not key:
        raise NotLoggedIn(f"{key_env} is not set. The API backend needs a key in the "
                          "environment; it is never read from config.json.")
    return anthropic.Anthropic(api_key=key)


def resolve_model(alias: str, cfg=None) -> str:
    table = dict(DEFAULT_MODELS)
    table.update(getattr(cfg, "api_models", None) or {})
    return table.get((alias or "opus").lower(), alias)


def _image_block(path: Path) -> dict | None:
    media = MEDIA.get(path.suffix.lower())
    if not media:
        return None
    data = path.read_bytes()
    if len(data) > 3_500_000:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.standard_b64encode(data).decode("ascii")}}


def run(prompt: str, model: str = "opus", timeout_s: int = 600,
        system: str | None = None, expect_json: bool = True,
        images: list | None = None,
        on_activity: Callable[[dict], None] | None = None, cfg=None) -> ClaudeResult:
    import anthropic

    client = _client(cfg)
    model_id = resolve_model(model, cfg)
    content: list[dict] = []
    for img in (images or [])[:4]:
        block = _image_block(Path(img))
        if block:
            content.append(block)
    content.append({"type": "text", "text": prompt})

    kwargs = dict(model=model_id, max_tokens=MAX_TOKENS,
                  messages=[{"role": "user", "content": content}])
    if system:
        kwargs["system"] = system
    # Adaptive thinking is the current default on the Opus/Sonnet 5 generation;
    # Haiku 4.5 still takes the older budget form, so it simply gets none.
    if "haiku" not in model_id:
        kwargs["thinking"] = {"type": "adaptive"}

    if on_activity:
        on_activity({"phase": "requesting"})
    started = time.monotonic()
    try:
        response = client.with_options(timeout=float(timeout_s)).messages.create(**kwargs)
    except anthropic.AuthenticationError as exc:
        raise NotLoggedIn(f"The API key was rejected: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise ClaudeError(f"Rate limited by the API: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise ClaudeError(f"API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeError(f"Could not reach the API: {exc}") from exc
    duration_ms = int((time.monotonic() - started) * 1000)

    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        why = getattr(detail, "explanation", "") if detail else ""
        raise ClaudeError(f"The model declined this request. {why}".strip())

    text = "".join(block.text for block in response.content if block.type == "text")
    usage = response.usage
    price_in, price_out = PRICES.get(model_id, (0.0, 0.0))
    cost = ((usage.input_tokens or 0) * price_in + (usage.output_tokens or 0) * price_out) / 1e6

    data, how, err = (None, "", "")
    if expect_json:
        data, how, err = parse_json_ex(text)
    if on_activity:
        on_activity({"phase": "finishing"})
    return ClaudeResult(text=text, data=data, cost_usd=cost, duration_ms=duration_ms,
                        session_id=getattr(response, "id", "") or "",
                        repaired=bool(data is not None and how.startswith("repair")),
                        parse_error=err if data is None and expect_json else "")


def doctor(cfg=None) -> dict:
    """Mirror claude_cli.doctor(): is the key present, and does the API answer."""
    info = {"cli": "anthropic API", "version": "", "logged_in": False, "detail": ""}
    try:
        client = _client(cfg)
        model = client.models.retrieve(resolve_model("haiku", cfg))
        info["version"] = getattr(model, "display_name", "") or model.id
        info["logged_in"] = True
    except NotLoggedIn as exc:
        info["detail"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        info["detail"] = f"{type(exc).__name__}: {exc}"
    return info
