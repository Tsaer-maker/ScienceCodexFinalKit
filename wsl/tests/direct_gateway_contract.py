#!/usr/bin/env python3
"""Offline contract for direct provider Model/Reasoning request routing."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("finalkit_direct_gateway_contract", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import direct gateway: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_fd(value: bytes) -> int:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, value)
    os.close(write_fd)
    return read_fd


def runtime_config(provider: str) -> dict:
    upstream = {
        "deepseek": "https://api.deepseek.com/anthropic",
        "kimi": "https://api.moonshot.ai/anthropic",
        "glm": "https://open.bigmodel.cn/api/anthropic",
    }[provider]
    reasoning = {
        "deepseek": ("max", "high", "none"),
        "kimi": ("max", "high", "low"),
        "glm": ("max", "high", "none"),
    }[provider]
    return {
        "host": "127.0.0.1",
        "port": 19888,
        "path_secret": "a" * 43,
        "provider": provider,
        "upstream": upstream,
        "model_opus": f"{provider}-opus",
        "reasoning_opus": reasoning[0],
        "model_sonnet": f"{provider}-sonnet",
        "reasoning_sonnet": reasoning[1],
        "model_haiku": f"{provider}-haiku",
        "reasoning_haiku": reasoning[2],
        "instance_id": "fixture-instance",
        "profile_id": "fixture-profile",
    }


def verify(path: Path) -> None:
    module = load_module(path)
    with tempfile.TemporaryDirectory(prefix="finalkit-direct-gateway-"):
        for provider in ("deepseek", "kimi", "glm"):
            source = runtime_config(provider)
            config_fd = private_fd(json.dumps(source).encode("utf-8"))
            key_fd = private_fd(b"fixture-key")
            config, key = module.load_runtime(config_fd, key_fd)
            assert key == "fixture-key"
            assert module.route_for(config, "claude-opus-4-8") == (
                "opus", f"{provider}-opus", source["reasoning_opus"]
            )
            assert module.route_for(config, "claude-sonnet-4-5") == (
                "sonnet", f"{provider}-sonnet", source["reasoning_sonnet"]
            )
            assert module.route_for(config, "claude-haiku-4-5-20251001") == (
                "haiku", f"{provider}-haiku", source["reasoning_haiku"]
            )

        original = {"thinking": {"type": "enabled", "budget_tokens": 10}}
        assert module.apply_provider_reasoning(original, "deepseek", "auto") is original

        deepseek = module.apply_provider_reasoning(
            {"output_config": {"format": "json"}, "reasoning_effort": "low"},
            "deepseek",
            "max",
        )
        assert deepseek["thinking"] == {"type": "enabled"}
        assert deepseek["output_config"] == {"format": "json", "effort": "max"}
        assert "reasoning_effort" not in deepseek

        kimi = module.apply_provider_reasoning(
            {"output_config": {"effort": "old", "format": "text"}}, "kimi", "low"
        )
        assert kimi["thinking"] == {"type": "enabled"}
        assert kimi["reasoning_effort"] == "low"
        assert kimi["output_config"] == {"format": "text"}

        disabled = module.apply_provider_reasoning(
            {"reasoning_effort": "high", "output_config": {"effort": "max"}},
            "glm",
            "none",
        )
        assert disabled == {"thinking": {"type": "disabled"}}

        invalid = runtime_config("deepseek")
        invalid["reasoning_sonnet"] = "low"
        try:
            module.load_runtime(
                private_fd(json.dumps(invalid).encode("utf-8")), private_fd(b"fixture-key")
            )
        except ValueError as exc:
            assert "sonnet reasoning" in str(exc)
        else:
            raise AssertionError("unsupported provider reasoning was accepted")

    print("DIRECT_GATEWAY_CONTRACT_OK")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: direct_gateway_contract.py /path/to/direct_gateway.py", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
