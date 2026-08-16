#!/usr/bin/env python3
"""Offline contract test for FinalKit's pinned Codex connector.

The test imports the already-patched connector from a caller-supplied path,
uses an isolated temporary config, replaces account auth and HTTP with local
fakes, and captures the exact Responses payload for all three Science aliases.
It never reads a real credential or opens a network connection.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True


CATALOG_ALIASES = {
    "claude-opus-4-8": "gpt-5.6-sol",
    "claude-sonnet-4-5": "gpt-5.6-terra",
    "claude-haiku-4-5-20251001": "gpt-5.6-luna",
}
ROUTE_ALIASES = {
    **CATALOG_ALIASES,
    # The optional multi-agent plugin currently launches these newer aliases.
    "claude-opus-5": "gpt-5.6-sol",
    "claude-sonnet-5": "gpt-5.6-terra",
    "claude-fable-5": "gpt-5.6-sol",
}
REASONING = {
    "claude-opus-4-8": "max",
    "claude-sonnet-4-5": "high",
    "claude-haiku-4-5-20251001": "low",
    "claude-opus-5": "max",
    "claude-sonnet-5": "high",
    "claude-fable-5": "max",
}


class FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        yield 'data: {"type":"response.output_text.delta","delta":"BACKEND_OK"}'
        yield 'data: {"type":"response.completed","response":{"status":"completed","usage":{}}}'
        yield "data: [DONE]"


class FakeStream:
    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse()

    async def __aenter__(self) -> FakeResponse:
        return self.response

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        return False


class CaptureClient:
    def __init__(self, statuses: list[int] | None = None) -> None:
        self.requests: list[dict] = []
        self.statuses = list(statuses or [])

    def stream(self, method: str, url: str, *, json: dict, headers: dict) -> FakeStream:
        self.requests.append(
            {"method": method, "url": url, "json": json, "headers": headers}
        )
        status = self.statuses.pop(0) if self.statuses else 200
        return FakeStream(FakeResponse(status))


def load_connector(proxy_path: Path, config_dir: Path):
    os.environ["CLAUDE_SCIENCE_PROXY_DIR"] = str(config_dir)
    os.environ.pop("FINALKIT_CONTROL_FD", None)
    os.environ.pop("FINALKIT_INSTANCE_FD", None)
    spec = importlib.util.spec_from_file_location("finalkit_connector_contract", proxy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import connector: {proxy_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def verify(proxy_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="finalkit-connector-contract-") as temporary:
        config_dir = Path(temporary)
        # The upstream app mounts this directory at import time even though the
        # contract exercises no static or browser route.
        (config_dir / "static").mkdir()
        config = {
            "openai_auth_mode": "codex_device",
            "default_backend": "openai",
            "codex_backend_url": "https://chatgpt.com/backend-api/codex",
            "codex_model": CATALOG_ALIASES["claude-opus-4-8"],
            "codex_reasoning": "max",
            "codex_reasoning_map": {
                "claude-opus": REASONING["claude-opus-4-8"],
                "claude-sonnet": REASONING["claude-sonnet-4-5"],
                "claude-haiku": REASONING["claude-haiku-4-5-20251001"],
            },
            "codex_supported_reasoning": ["high", "max", "ultra"],
            "codex_supported_reasoning_map": {
                "claude-opus": ["high", "max", "ultra"],
                "claude-sonnet": ["high", "max"],
                "claude-haiku": ["low", "medium", "high", "xhigh", "max"],
            },
            "codex_model_map": {
                "claude-opus": CATALOG_ALIASES["claude-opus-4-8"],
                "claude-sonnet": CATALOG_ALIASES["claude-sonnet-4-5"],
                "claude-haiku": CATALOG_ALIASES["claude-haiku-4-5-20251001"],
            },
            "force_model": "",
        }
        (config_dir / "config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        connector = load_connector(proxy_path, config_dir)

        # The connector may read the official CLI cache, but it must never
        # refresh, rewrite, or delete that credential owner.
        auth_path = config_dir / "auth.json"
        connector.codex_auth_store.path = auth_path
        old_auth = {
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": "official-access-old",
                "refresh_token": "official-refresh-old",
                "id_token": "",
                "account_id": "contract-account",
            },
            "preserved_owner_field": "keep-me",
        }
        auth_path.write_text(json.dumps(old_auth) + "\n", encoding="utf-8")
        old_bytes = auth_path.read_bytes()
        assert connector.codex_auth_store.authorization_header() == "Bearer official-access-old"
        assert auth_path.read_bytes() == old_bytes
        try:
            connector.codex_auth_store.refresh({"access_token": "official-access-old"})
        except ValueError as exc:
            assert "official Codex token" in str(exc)
        else:
            raise AssertionError("connector refreshed an unchanged official token")
        assert auth_path.read_bytes() == old_bytes

        new_auth = dict(old_auth)
        new_auth["tokens"] = dict(old_auth["tokens"])
        new_auth["tokens"]["access_token"] = "official-access-new"
        new_auth["tokens"]["refresh_token"] = "official-refresh-new"
        new_bytes = (json.dumps(new_auth) + "\n").encode("utf-8")
        auth_path.write_bytes(new_bytes)
        adopted = connector.codex_auth_store.refresh(
            {"access_token": "official-access-old"}
        )
        assert adopted["access_token"] == "official-access-new"
        assert auth_path.read_bytes() == new_bytes
        for forbidden in (
            lambda: connector.codex_auth_store.save(adopted),
            connector.codex_auth_store.clear,
        ):
            try:
                forbidden()
            except ValueError:
                pass
            else:
                raise AssertionError("connector mutated the official auth owner")
        assert auth_path.read_bytes() == new_bytes

        connector.codex_auth_store.authorization_header = lambda: "Bearer contract-test"
        connector.codex_auth_store.account_id = lambda: "contract-account"

        assert connector.FAKE_ACCOUNT_UUID == "00000000-0000-4000-8000-000000000001"
        assert connector.FAKE_ORG_UUID == "00000000-0000-4000-8000-000000000002"
        assert connector.FAKE_ACCESS_TOKEN == "sk-ant-finalkit-local-session"
        token_response = connector.fake_token_response()
        assert token_response["refresh_token"] == ""
        assert token_response["expires_at"] == "2099-01-01T00:00:00.000Z"
        assert connector.fake_user_response()["email"] == "virtual@localhost.invalid"
        connector.log_request = lambda *args, **kwargs: None
        client = CaptureClient()
        connector.get_client = lambda: client

        catalog_response = await connector.list_models(None)
        catalog = json.loads(bytes(catalog_response.body).decode("utf-8"))["data"]
        expected_catalog = [
            {
                "id": alias,
                "type": "model",
                "display_name": f"ChatGPT Codex | {model} | reasoning={REASONING[alias]}",
            }
            for alias, model in CATALOG_ALIASES.items()
        ]
        if catalog != expected_catalog:
            raise AssertionError(f"unexpected model catalog: {catalog!r}")

        for alias, expected_model in ROUTE_ALIASES.items():
            backend = connector.config.resolve_backend(alias)
            if backend["model"] != expected_model:
                raise AssertionError(f"{alias} resolved to {backend['model']!r}")
            expected_reasoning = REASONING[alias]
            if backend["reasoning"] != expected_reasoning:
                raise AssertionError(f"{alias} reasoning is {backend['reasoning']!r}")
            before = len(client.requests)
            response = await connector._handle_codex_messages(
                {
                    "model": alias,
                    "max_tokens": 64,
                    "messages": [{"role": "user", "content": "Return BACKEND_OK"}],
                },
                backend,
                alias,
                "msg_contract",
                False,
            )
            if response.status_code != 200 or len(client.requests) != before + 1:
                raise AssertionError(f"{alias} did not complete one captured request")
            outbound = client.requests[-1]
            if outbound["json"].get("model") != expected_model:
                raise AssertionError(f"{alias} outbound model drifted: {outbound['json']!r}")
            if outbound["json"].get("reasoning") != {"effort": expected_reasoning}:
                raise AssertionError(f"{alias} outbound reasoning drifted: {outbound['json']!r}")

        retry_client = CaptureClient([502, 200])
        connector.get_client = lambda: retry_client
        alias = "claude-opus-4-8"
        backend = connector.config.resolve_backend(alias)
        response = await connector._handle_codex_messages(
            {
                "model": alias,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "Return BACKEND_OK"}],
            },
            backend,
            alias,
            "msg_retry_contract",
            False,
        )
        if response.status_code != 200 or len(retry_client.requests) != 2:
            raise AssertionError("one transient pre-response Codex 502 was not retried exactly once")

        connector.config._data["codex_reasoning"] = "auto"
        connector.config._data["codex_reasoning_map"] = {
            "claude-opus": "auto",
            "claude-sonnet": "auto",
            "claude-haiku": "auto",
        }
        auto_client = CaptureClient()
        connector.get_client = lambda: auto_client
        alias = "claude-sonnet-5"
        backend = connector.config.resolve_backend(alias)
        if backend["reasoning"] != "auto":
            raise AssertionError("auto route was not retained by connector config")
        if connector._codex_reasoning_for_request(
            {"thinking": {"type": "disabled"}}, "auto"
        ) != "none":
            raise AssertionError("auto route did not preserve an explicit thinking disable")
        response = await connector._handle_codex_messages(
            {
                "model": alias,
                "max_tokens": 64,
                "output_config": {"effort": "high"},
                "messages": [{"role": "user", "content": "Return BACKEND_OK"}],
            },
            backend,
            alias,
            "msg_auto_contract",
            False,
        )
        if response.status_code != 200:
            raise AssertionError("auto route did not complete")
        if auto_client.requests[-1]["json"].get("reasoning") != {"effort": "high"}:
            raise AssertionError("incoming role effort was not passed through")
        before_invalid = len(auto_client.requests)
        response = await connector._handle_codex_messages(
            {
                "model": alias,
                "max_tokens": 64,
                "output_config": {"effort": "future-unsupported"},
                "messages": [{"role": "user", "content": "Return BACKEND_OK"}],
            },
            backend,
            alias,
            "msg_invalid_auto_contract",
            False,
        )
        if response.status_code != 400 or len(auto_client.requests) != before_invalid:
            raise AssertionError("unsupported Codex auto effort did not fail before upstream I/O")
        invalid_payload = json.loads(bytes(response.body).decode("utf-8"))
        if "unsupported incoming Codex reasoning effort" not in str(invalid_payload):
            raise AssertionError("invalid Codex auto effort returned an unclear error")
        haiku_alias = "claude-haiku-5"
        haiku_backend = connector.config.resolve_backend(haiku_alias)
        before_model_invalid = len(auto_client.requests)
        response = await connector._handle_codex_messages(
            {
                "model": haiku_alias,
                "max_tokens": 64,
                "output_config": {"effort": "ultra"},
                "messages": [{"role": "user", "content": "Return BACKEND_OK"}],
            },
            haiku_backend,
            haiku_alias,
            "msg_model_invalid_auto_contract",
            False,
        )
        if response.status_code != 400 or len(auto_client.requests) != before_model_invalid:
            raise AssertionError("model-specific Codex auto effort reached upstream I/O")
        if "gpt-5.6-luna" not in str(json.loads(bytes(response.body).decode("utf-8"))):
            raise AssertionError("model-specific Codex auto failure did not name the selected model")
        response = await connector._handle_codex_messages(
            {
                "model": alias,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": "Return BACKEND_OK"}],
            },
            backend,
            alias,
            "msg_default_contract",
            False,
        )
        if "reasoning" in auto_client.requests[-1]["json"]:
            raise AssertionError("auto route invented an effort when the request omitted one")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: connector_contract.py /path/to/patched/proxy.py", file=sys.stderr)
        return 2
    proxy_path = Path(sys.argv[1]).resolve()
    if not proxy_path.is_file():
        print(f"connector is missing: {proxy_path}", file=sys.stderr)
        return 2
    asyncio.run(verify(proxy_path))
    print(
        "CONNECTOR_CONTRACT_OK catalog=3 routes=sol,terra,luna "
        "plugin-aliases=opus5,sonnet5,fable5 reasoning=max,high,low+auto-pass-through+invalid-reject "
        "codex-auth=official-read-only retry502=once network=disabled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
