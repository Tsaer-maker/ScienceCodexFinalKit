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
        "kimi": ("max", "high", "none"),
        "glm": ("max", "high", "none"),
    }[provider]
    models = {
        "deepseek": ("deepseek-v4-pro", "deepseek-v4-pro", "deepseek-v4-flash"),
        "kimi": ("kimi-k3[1m]", "kimi-k3[1m]", "kimi-k2.6"),
        "glm": ("glm-5.2", "glm-5.2", "glm-4.7-flash"),
    }[provider]
    return {
        "host": "127.0.0.1",
        "port": 19888,
        "path_secret": "a" * 43,
        "provider": provider,
        "upstream": upstream,
        "model_opus": models[0],
        "reasoning_opus": reasoning[0],
        "model_sonnet": models[1],
        "reasoning_sonnet": reasoning[1],
        "model_haiku": models[2],
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
                "opus", source["model_opus"], source["reasoning_opus"]
            )
            assert module.route_for(config, "claude-fable-5") == (
                "opus", source["model_opus"], source["reasoning_opus"]
            )
            assert module.route_for(config, "claude-sonnet-4-5") == (
                "sonnet", source["model_sonnet"], source["reasoning_sonnet"]
            )
            assert module.route_for(config, "claude-haiku-4-5-20251001") == (
                "haiku", source["model_haiku"], source["reasoning_haiku"]
            )

        original = {"thinking": {"type": "enabled", "budget_tokens": 10}}
        assert module.apply_provider_reasoning(
            original, "deepseek", "deepseek-v4-pro", "auto"
        ) is original

        auto_high = module.apply_provider_reasoning(
            {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "xhigh",
                "output_config": {"format": "json", "effort": "high"},
            },
            "deepseek",
            "deepseek-v4-pro",
            "auto",
        )
        assert auto_high["output_config"] == {"format": "json", "effort": "high"}
        assert "reasoning_effort" not in auto_high

        deepseek = module.apply_provider_reasoning(
            {"output_config": {"format": "json"}, "reasoning_effort": "low"},
            "deepseek",
            "deepseek-v4-pro",
            "max",
        )
        assert deepseek["thinking"] == {"type": "enabled"}
        assert deepseek["output_config"] == {"format": "json", "effort": "max"}
        assert "reasoning_effort" not in deepseek

        kimi = module.apply_provider_reasoning(
            {"output_config": {"effort": "old", "format": "text"}},
            "kimi",
            "kimi-k3[1m]",
            "low",
        )
        assert "thinking" not in kimi
        assert kimi["reasoning_effort"] == "low"
        assert kimi["output_config"] == {"format": "text"}

        glm_strength = module.apply_provider_reasoning(
            {"output_config": {"effort": "old", "format": "text"}},
            "glm",
            "glm-5.2",
            "xhigh",
        )
        assert glm_strength == {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "xhigh",
            "output_config": {"format": "text"},
        }
        glm_53 = module.apply_provider_reasoning({}, "glm", "glm-5.3", "max")
        assert glm_53 == {
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
        }
        assert module.apply_provider_reasoning({}, "glm", "glm-5.3", "auto") == {
            "thinking": {"type": "enabled"}
        }

        disabled = module.apply_provider_reasoning(
            {"reasoning_effort": "high", "output_config": {"effort": "max"}},
            "glm",
            "glm-5.2",
            "none",
        )
        assert disabled == {"thinking": {"type": "disabled"}}

        auto_disabled = module.apply_provider_reasoning(
            {
                "thinking": {"type": "disabled"},
                "reasoning_effort": "xhigh",
                "output_config": {"effort": "medium"},
            },
            "glm",
            "glm-5.2",
            "auto",
        )
        assert auto_disabled == {"thinking": {"type": "disabled"}}

        for provider, model, effort in (
            ("deepseek", "deepseek-v4-pro", "xhigh"),
            ("glm", "glm-4.7-flash", "high"),
            ("glm", "glm-5.3", "none"),
            ("kimi", "kimi-k3[1m]", "none"),
            ("kimi", "kimi-k3[1m]", "xhigh"),
            ("kimi", "kimi-k2.6", "high"),
        ):
            try:
                module.apply_provider_reasoning(
                    {"reasoning_effort": effort}, provider, model, "auto"
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"unsupported auto effort {provider}/{model}/{effort} was forwarded"
                )

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

        kimi_k3_none = runtime_config("kimi")
        kimi_k3_none["model_sonnet"] = "kimi-k3[1m]"
        kimi_k3_none["reasoning_sonnet"] = "none"
        try:
            module.load_runtime(
                private_fd(json.dumps(kimi_k3_none).encode("utf-8")),
                private_fd(b"fixture-key"),
            )
        except ValueError as exc:
            assert "sonnet reasoning" in str(exc)
        else:
            raise AssertionError("Kimi K3 accepted Reasoning=none")

        kimi_k26_none = runtime_config("kimi")
        kimi_k26_none["model_sonnet"] = "kimi-k2.6"
        kimi_k26_none["reasoning_sonnet"] = "none"
        config, _ = module.load_runtime(
            private_fd(json.dumps(kimi_k26_none).encode("utf-8")),
            private_fd(b"fixture-key"),
        )
        assert config["reasoning_sonnet"] == "none"

        for provider, model in (("kimi", "kimi-k2.6"), ("glm", "glm-4.7-flash")):
            invalid_model_effort = runtime_config(provider)
            invalid_model_effort["model_sonnet"] = model
            invalid_model_effort["reasoning_sonnet"] = "high"
            try:
                module.load_runtime(
                    private_fd(json.dumps(invalid_model_effort).encode("utf-8")),
                    private_fd(b"fixture-key"),
                )
            except ValueError as exc:
                assert "sonnet reasoning" in str(exc)
            else:
                raise AssertionError(f"{model} accepted an unsupported strength")

    print("DIRECT_GATEWAY_CONTRACT_OK model-specific=K3+K2.6+GLM5.2+GLM5.3+GLM4.7 auto=validated+normalized")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: direct_gateway_contract.py /path/to/direct_gateway.py", file=sys.stderr)
        return 2
    verify(Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
