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


ALIASES = {
    "claude-opus-4-8": "gpt-5.6-sol",
    "claude-sonnet-4-5": "gpt-5.6-terra",
    "claude-haiku-4-5-20251001": "gpt-5.6-luna",
}
EFFORT = "max"


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
            "codex_model": ALIASES["claude-opus-4-8"],
            "codex_reasoning_effort": EFFORT,
            "codex_model_map": {
                "claude-opus": ALIASES["claude-opus-4-8"],
                "claude-sonnet": ALIASES["claude-sonnet-4-5"],
                "claude-haiku": ALIASES["claude-haiku-4-5-20251001"],
            },
            "force_model": "",
        }
        (config_dir / "config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        connector = load_connector(proxy_path, config_dir)
        connector.codex_auth_store.authorization_header = lambda: "Bearer contract-test"
        connector.codex_auth_store.account_id = lambda: "contract-account"
        connector.log_request = lambda *args, **kwargs: None
        client = CaptureClient()
        connector.get_client = lambda: client

        catalog_response = await connector.list_models(None)
        catalog = json.loads(bytes(catalog_response.body).decode("utf-8"))["data"]
        expected_catalog = [
            {
                "id": alias,
                "type": "model",
                "display_name": f"ChatGPT Codex | {model} | {EFFORT}",
            }
            for alias, model in ALIASES.items()
        ]
        if catalog != expected_catalog:
            raise AssertionError(f"unexpected model catalog: {catalog!r}")

        for alias, expected_model in ALIASES.items():
            backend = connector.config.resolve_backend(alias)
            if backend["model"] != expected_model:
                raise AssertionError(f"{alias} resolved to {backend['model']!r}")
            if backend["reasoning_effort"] != EFFORT:
                raise AssertionError(f"{alias} effort is {backend['reasoning_effort']!r}")
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
            if outbound["json"].get("reasoning") != {"effort": EFFORT}:
                raise AssertionError(f"{alias} outbound effort drifted: {outbound['json']!r}")

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


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: connector_contract.py /path/to/patched/proxy.py", file=sys.stderr)
        return 2
    proxy_path = Path(sys.argv[1]).resolve()
    if not proxy_path.is_file():
        print(f"connector is missing: {proxy_path}", file=sys.stderr)
        return 2
    asyncio.run(verify(proxy_path))
    print("CONNECTOR_CONTRACT_OK catalog=3 routes=sol,terra,luna effort=max retry502=once network=disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
