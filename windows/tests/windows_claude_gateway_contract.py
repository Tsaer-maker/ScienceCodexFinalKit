#!/usr/bin/env python3
"""Offline contract for the independent Windows Claude provider gateway."""

from __future__ import annotations

import importlib.util
import io
import json
import base64
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("windows_claude_gateway", path)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load gateway module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(url: str, token: str = "", method: str = "GET", body=None, control: str = ""):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if control:
        headers["X-FinalKit-Control"] = control
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def verify_template(path: Path) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["schema_version"] == 4
    assert value["host"] == "127.0.0.1"
    assert value["port"] == 18987
    assert set(value["profiles"]) == {"deepseek", "kimi", "glm", "codex"}
    for mode, profile in value["profiles"].items():
        assert "api_key" not in profile
        assert "path_secret" not in profile
        assert "client_token" not in profile
        for role in ("opus", "sonnet", "haiku"):
            assert profile[f"model_{role}"]
            assert profile[f"reasoning_{role}"]
        assert "model_default" not in profile
        assert "model_fast" not in profile
        assert "reasoning_effort" not in profile
        if mode == "codex":
            assert profile["model_opus"] == "gpt-5.6-sol"
            assert profile["model_sonnet"] == "gpt-5.6-terra"
            assert profile["model_haiku"] == "gpt-5.6-luna"
            assert profile["reasoning_opus"] == "max"
            assert profile["reasoning_sonnet"] == "max"
            assert profile["reasoning_haiku"] == "max"
            assert profile["protocol"] == "openai-responses"
            assert profile["auth_style"] == "codex-cli"
            assert profile["upstream"] == "https://chatgpt.com/backend-api/codex"
            assert profile["name"] == "Codex Login"
        else:
            assert profile["reasoning_opus"] == "auto"
            assert profile["reasoning_sonnet"] == "auto"
            assert profile["reasoning_haiku"] == "auto"
            assert profile["protocol"] == "anthropic-messages"


def verify_model_constraints(module) -> None:
    with tempfile.TemporaryDirectory(prefix="switchboard-windows-models-") as raw_dir:
        path = Path(raw_dir) / "runtime.json"
        config = {
            "schema_version": 3,
            "instance_id": str(uuid.uuid4()),
            "profile": "kimi",
            "profile_id": str(uuid.uuid4()),
            "profile_name": "Kimi API",
            "host": "127.0.0.1",
            "port": 18988,
            "path_secret": "p" * 43,
            "client_token": "c" * 43,
            "control_secret": "s" * 43,
            "protocol": "anthropic-messages",
            "upstream": "https://api.moonshot.ai/anthropic",
            "auth_style": "bearer",
            "model_opus": "kimi-k3[1m]",
            "reasoning_opus": "max",
            "model_sonnet": "kimi-k3[1m]",
            "reasoning_sonnet": "none",
            "model_haiku": "kimi-k2.6",
            "reasoning_haiku": "none",
        }
        path.write_text(json.dumps(config), encoding="utf-8")
        try:
            module.load_config(path)
        except module.GatewayError as exc:
            assert "sonnet reasoning" in str(exc)
        else:
            raise AssertionError("Windows gateway accepted Kimi K3 Reasoning=none")
        config["model_sonnet"] = "kimi-k2.6"
        path.write_text(json.dumps(config), encoding="utf-8")
        loaded = module.load_config(path)
        assert loaded["reasoning_sonnet"] == "none"


def verify_conversion(module) -> None:
    assert "auto" in module.PROFILE_REASONING["codex"]
    for reserved in (9876, 18987):
        try:
            module._validate_url(f"http://127.0.0.1:{reserved}", "anthropic-messages")
        except module.GatewayError:
            pass
        else:
            raise AssertionError(f"reserved loopback port {reserved} was accepted")

    request = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 128,
        "system": [{"type": "text", "text": "Be exact."}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Read x"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "mcp__files__read",
                        "input": {"path": "x"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Continue with the returned value."},
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"},
                ],
            },
        ],
        "tools": [
            {
                "name": "mcp__files__read",
                "description": "read",
                "input_schema": {"properties": {"path": {"type": "string"}}, "required": ["path"]},
            }
        ],
        "stream": False,
    }
    converted, reverse = module.anthropic_to_responses(request, "gpt-test", "high")
    assert converted["model"] == "gpt-test"
    assert converted["instructions"] == "Be exact."
    assert converted["store"] is False
    assert "max_output_tokens" not in converted
    assert converted["reasoning"] == {"effort": "high"}
    assert any(item.get("type") == "function_call" for item in converted["input"])
    assert any(item.get("type") == "function_call_output" for item in converted["input"])
    tool_output_index = next(
        index
        for index, item in enumerate(converted["input"])
        if item.get("type") == "function_call_output"
    )
    followup_message_index = next(
        index
        for index, item in enumerate(converted["input"])
        if item.get("type") == "message"
        and any(
            part.get("text") == "Continue with the returned value."
            for part in item.get("content", [])
        )
    )
    assert tool_output_index < followup_message_index
    assert "strict" not in converted["tools"][0]

    auto_request = dict(request)
    auto_request["output_config"] = {"effort": "xhigh"}
    auto_converted, _ = module.anthropic_to_responses(auto_request, "gpt-test", "auto")
    assert auto_converted["reasoning"] == {"effort": "xhigh"}
    default_converted, _ = module.anthropic_to_responses(request, "gpt-test", "auto")
    assert "reasoning" not in default_converted
    invalid_auto_request = dict(request)
    invalid_auto_request["output_config"] = {"effort": "future-unsupported"}
    try:
        module.anthropic_to_responses(invalid_auto_request, "gpt-test", "auto")
    except module.GatewayError as exc:
        assert "unsupported incoming Codex reasoning effort" in str(exc)
    else:
        raise AssertionError("unsupported Codex auto effort silently used the model default")
    luna_ultra_request = dict(request)
    luna_ultra_request["output_config"] = {"effort": "ultra"}
    try:
        module.anthropic_to_responses(
            luna_ultra_request,
            "gpt-5.6-luna",
            "auto",
            ["low", "medium", "high", "xhigh", "max"],
        )
    except module.GatewayError as exc:
        assert "gpt-5.6-luna" in str(exc) and "does not support" in str(exc)
    else:
        raise AssertionError("Luna auto accepted cache-unsupported Reasoning=ultra")
    disabled_request = dict(request)
    disabled_request["thinking"] = {"type": "disabled"}
    disabled_converted, _ = module.anthropic_to_responses(disabled_request, "gpt-test", "auto")
    assert disabled_converted["reasoning"] == {"effort": "none"}

    codex_routes = {
        "profile": "codex",
        "model_opus": "gpt-opus",
        "model_sonnet": "gpt-sonnet",
        "model_haiku": "gpt-haiku",
        "reasoning_opus": "max",
        "reasoning_sonnet": "high",
        "reasoning_haiku": "medium",
    }
    assert module._route_for(codex_routes, "claude-opus-4-8") == ("gpt-opus", "max")
    assert module._route_for(codex_routes, "claude-sonnet-4-5") == ("gpt-sonnet", "high")
    assert module._route_for(codex_routes, "claude-haiku-4-5") == ("gpt-haiku", "medium")
    assert module._route_for(codex_routes, "unknown-claude-alias") == ("gpt-sonnet", "high")

    deepseek = module.apply_provider_reasoning(
        {"output_config": {"format": "json"}, "reasoning_effort": "low"},
        "deepseek",
        "deepseek-v4-pro",
        "max",
    )
    assert deepseek["thinking"] == {"type": "enabled"}
    assert deepseek["output_config"] == {"format": "json", "effort": "max"}
    assert "reasoning_effort" not in deepseek
    kimi = module.apply_provider_reasoning({}, "kimi", "kimi-k3[1m]", "low")
    assert kimi == {"reasoning_effort": "low"}
    glm_strength = module.apply_provider_reasoning(
        {"output_config": {"format": "json", "effort": "old"}},
        "glm",
        "glm-5.2",
        "xhigh",
    )
    assert glm_strength == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "xhigh",
        "output_config": {"format": "json"},
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
    for profile, model, effort in (
        ("deepseek", "deepseek-v4-pro", "xhigh"),
        ("glm", "glm-4.7-flash", "high"),
        ("glm", "glm-5.3", "none"),
        ("kimi", "kimi-k3[1m]", "none"),
        ("kimi", "kimi-k3[1m]", "xhigh"),
        ("kimi", "kimi-k2.6", "high"),
    ):
        try:
            module.apply_provider_reasoning(
                {"reasoning_effort": effort}, profile, model, "auto"
            )
        except module.GatewayError:
            pass
        else:
            raise AssertionError(
                f"unsupported auto effort {profile}/{model}/{effort} was forwarded"
            )

    safe_name = converted["tools"][0]["name"]
    response = {
        "id": "resp_1",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": safe_name,
                "arguments": '{"path":"x"}',
            }
        ],
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }
    translated = module.responses_to_anthropic(response, request["model"], reverse)
    assert translated["stop_reason"] == "tool_use"
    assert translated["content"][0]["name"] == "mcp__files__read"
    assert translated["content"][0]["input"] == {"path": "x"}
    assert translated["usage"] == {"input_tokens": 11, "output_tokens": 7}

    stream = io.BytesIO(
        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"OK"}\n\n'
        b'event: response.completed\ndata: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
    )
    events = b"".join(module.translate_responses_stream(stream, request["model"], {}))
    assert b"message_start" in events
    assert b"text_delta" in events
    assert b"message_stop" in events

    completed = io.BytesIO(
        b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_final","status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}],"usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
    )
    collected = module.collect_responses_stream(completed)
    assert collected["id"] == "resp_final"
    assert collected["output"][0]["content"][0]["text"] == "OK"


def jwt(claims: dict) -> str:
    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode(claims)}.signature"


def verify_codex_auth(module) -> None:
    with tempfile.TemporaryDirectory(prefix="finalkit-windows-codex-auth-") as raw_dir:
        auth_path = Path(raw_dir) / "auth.json"
        old_access = jwt({"exp": int(time.time()) + 3600, "version": "old"})
        new_access = jwt({"exp": int(time.time()) + 3600, "version": "new"})
        auth_path.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "OPENAI_API_KEY": None,
                    "tokens": {
                        "access_token": old_access,
                        "refresh_token": "offline-refresh-old",
                        "id_token": jwt({"chatgpt_account_id": "account-offline"}),
                        "account_id": "account-offline",
                    },
                    "preserved_owner_field": "keep-me",
                }
            ),
            encoding="utf-8",
        )
        initial_bytes = auth_path.read_bytes()
        store = module.CodexAuthStore(auth_path)
        store.assert_configured()
        first_headers = store.headers()
        assert first_headers["Authorization"] == f"Bearer {old_access}"
        assert auth_path.read_bytes() == initial_bytes
        try:
            store.headers(old_access)
        except module.CodexAuthError as exc:
            assert "official Windows Codex token" in str(exc)
        else:
            raise AssertionError("gateway tried to reuse a rejected official token")
        assert auth_path.read_bytes() == initial_bytes

        # Simulate the official CLI atomically publishing a newer token chain.
        externally_refreshed = {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "access_token": new_access,
                "refresh_token": "offline-refresh-new",
                "id_token": jwt({"chatgpt_account_id": "account-offline"}),
                "account_id": "account-offline",
            },
            "preserved_owner_field": "keep-me",
        }
        external_bytes = (json.dumps(externally_refreshed) + "\n").encode("utf-8")
        auth_path.write_bytes(external_bytes)
        headers = store.headers(old_access)
        assert headers["Authorization"] == f"Bearer {new_access}"
        assert headers["chatgpt-account-id"] == "account-offline"
        assert headers["OpenAI-Beta"] == "responses=experimental"
        assert headers["originator"] == "codex_cli_rs"
        assert auth_path.read_bytes() == external_bytes
        assert not list(auth_path.parent.glob(".auth.json.*.tmp"))


def verify_process(gateway_path: Path) -> None:
    port = free_port()
    path_secret = "p" * 43
    client_token = "c" * 43
    control_secret = "s" * 43
    with tempfile.TemporaryDirectory(prefix="finalkit-windows-claude-") as raw_dir:
        root = Path(raw_dir)
        config_path = root / "runtime.json"
        log_path = root / "gateway.log"
        auth_path = root / "auth.json"
        auth_path.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "OPENAI_API_KEY": None,
                    "tokens": {
                        "access_token": jwt({"exp": int(time.time()) + 3600}),
                        "refresh_token": "offline-refresh",
                        "id_token": "offline-id",
                        "account_id": "account-offline",
                    },
                }
            ),
            encoding="utf-8",
        )
        config = {
            "schema_version": 3,
            "instance_id": str(uuid.uuid4()),
            "profile": "codex",
            "profile_id": "642becc7-c4ca-52ec-8c7d-7e66a1c56023",
            "profile_name": "Codex Login",
            "host": "127.0.0.1",
            "port": port,
            "path_secret": path_secret,
            "client_token": client_token,
            "control_secret": control_secret,
            "protocol": "openai-responses",
            "upstream": "https://chatgpt.com/backend-api/codex",
            "auth_style": "codex-cli",
            "codex_auth_file": str(auth_path),
            "model_opus": "gpt-test-opus",
            "model_sonnet": "gpt-test-sonnet",
            "model_haiku": "gpt-test-haiku",
            "reasoning_opus": "max",
            "reasoning_sonnet": "high",
            "reasoning_haiku": "medium",
            "supported_reasoning_opus": ["low", "medium", "high", "xhigh", "max", "ultra"],
            "supported_reasoning_sonnet": ["low", "medium", "high", "xhigh", "max", "ultra"],
            "supported_reasoning_haiku": ["low", "medium", "high", "xhigh", "max"],
            "offline_smoke": True,
        }
        config_path.write_text(json.dumps(config), encoding="utf-8")
        command = [
            sys.executable,
            str(gateway_path),
            "--config",
            str(config_path),
            "--log-file",
            str(log_path),
        ]
        assert "offline-refresh" not in " ".join(command)
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}/{path_secret}"
        health = None
        for _ in range(100):
            if process.poll() is not None:
                break
            try:
                status, health = request_json(base + "/health", client_token)
                if status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.05)
        assert health and health["owner"] == "ScienceCodexFinalKit-WindowsClaude"
        assert health["profile"] == "codex"
        assert health["auth_owner"] == "windows-codex-cli"
        assert health["routes"]["sonnet"]["model"] == "gpt-test-sonnet"
        assert health["routes"]["sonnet"]["reasoning"] == "high"
        assert health["routes"]["sonnet"]["supported_reasoning"][-1] == "ultra"
        assert health["routes"]["sonnet"]["capability_source"] == "local-codex-cache"
        status, models = request_json(base + "/v1/models", client_token)
        assert status == 200
        labels = {item["id"]: item["display_name"] for item in models["data"]}
        assert "gpt-test-opus | reasoning=max" in labels["claude-opus-4-8"]
        assert "gpt-test-sonnet | reasoning=high" in labels["claude-sonnet-4-5"]
        assert "gpt-test-haiku | reasoning=medium" in labels["claude-haiku-4-5-20251001"]
        status, _ = request_json(base + "/health")
        assert status == 401
        status, count = request_json(
            base + "/v1/messages/count_tokens",
            client_token,
            "POST",
            {"messages": [{"role": "user", "content": "OK"}]},
        )
        assert status == 200 and count["input_tokens"] > 0
        status, error = request_json(
            base + "/v1/messages",
            client_token,
            "POST",
            {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "OK"}]},
        )
        assert status == 503
        assert "offline smoke" in error["error"]["message"]
        status, stopped = request_json(
            base + "/control/stop", client_token, "POST", control=control_secret
        )
        assert status == 200 and stopped["status"] == "stopping"
        process.wait(timeout=10)
        assert process.returncode == 0
        log = log_path.read_text(encoding="utf-8")
        assert "offline-refresh" not in log
        assert "WINDOWS_CLAUDE_GATEWAY_READY" in log
        assert "WINDOWS_CLAUDE_GATEWAY_STOPPED" in log


def verify_static_isolation(gateway_path: Path, controller_path: Path) -> None:
    gateway = gateway_path.read_text(encoding="utf-8")
    controller = controller_path.read_text(encoding="utf-8-sig")
    assert "subprocess" not in gateway
    assert "os.system" not in gateway
    assert "shell=True" not in gateway
    assert "API key" in gateway
    assert "RedirectStandardInput = $true" in controller
    assert "ProtectedData]::Protect" in controller
    assert "ProtectedData]::Unprotect" in controller
    assert "Get-WindowsCodexAuthPath" in controller
    assert '"codex_auth_file"' in controller
    assert "Invoke-Fkctl" not in controller
    assert "Get-Fkctl" not in controller
    assert "Invoke-Wsl" not in controller
    assert "Get-Wsl" not in controller


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: windows_claude_gateway_contract.py GATEWAY TEMPLATE CONTROLLER",
            file=sys.stderr,
        )
        return 2
    gateway = Path(sys.argv[1]).resolve()
    template = Path(sys.argv[2]).resolve()
    controller = Path(sys.argv[3]).resolve()
    module = load_module(gateway)
    verify_template(template)
    verify_model_constraints(module)
    verify_conversion(module)
    verify_codex_auth(module)
    verify_static_isolation(gateway, controller)
    verify_process(gateway)
    print(
        "WINDOWS_CLAUDE_GATEWAY_CONTRACT_OK profiles=4 api-empty=3 "
        "codex-routes=opus+sonnet+haiku reasoning=per-route+listed+auto-pass-through "
        "provider-auto=validated codex_auth=official-read-only+external-refresh-adoption "
        "dpapi=3 loopback=verified responses=tools+stream"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
