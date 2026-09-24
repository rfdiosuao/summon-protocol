"""Record read-only arm feedback from the console WebSocket as JSON Lines."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientSession, WSMsgType


FIELDS = (
    "actualDeg", "commandedDeg", "targetDeg", "velocityDps", "torqueNm",
    "gravityTorqueNm", "gravityFeedforwardNm", "enabled", "statusCode", "fault",
)


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only B601-DM telemetry recorder")
    parser.add_argument("--url", default="http://127.0.0.1:8870", help="Local arm-console base URL")
    parser.add_argument("--seconds", type=float, default=10.0, help="Duration, 1..600 seconds")
    parser.add_argument("--output", type=Path, help="Destination .jsonl file")
    options = parser.parse_args()
    if not 1.0 <= options.seconds <= 600.0:
        parser.error("--seconds must be between 1 and 600")
    return options


def websocket_url(base: str) -> str:
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("--url must be an http(s) URL")
    return urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc, "/api/ws", "", ""))


async def capture(options: argparse.Namespace) -> tuple[Path, int, float, str | None]:
    destination = options.output or (
        Path(__file__).resolve().parents[1] / "diagnostics" /
        f"telemetry-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
    )
    destination = destination.resolve()
    base = options.url.rstrip("/")
    count = 0
    first_time: float | None = None
    last_time: float | None = None
    fault: str | None = None
    trace_written = False

    async with ClientSession() as session:
        async with session.get(f"{base}/api/state") as response:
            response.raise_for_status()
            initial = await response.json()
        if initial.get("mode") != "hardware" or not initial.get("connected"):
            raise RuntimeError("Console must already be connected to COM6 hardware; recorder never connects or enables motors")

        destination.parent.mkdir(parents=True, exist_ok=True)
        async with session.ws_connect(websocket_url(base), heartbeat=15) as socket:
            deadline = time.monotonic() + options.seconds
            with destination.open("w", encoding="utf-8") as output:
                while time.monotonic() < deadline:
                    try:
                        message = await socket.receive(timeout=min(1.0, max(0.01, deadline - time.monotonic())))
                    except asyncio.TimeoutError:
                        continue
                    if message.type == WSMsgType.TEXT:
                        received = time.monotonic()
                        state = json.loads(message.data)
                        if state.get("mode") != "hardware" or not state.get("connected"):
                            raise RuntimeError("Hardware disconnected during capture")
                        joints = {
                            str(item["id"]): {key: item.get(key) for key in FIELDS}
                            for item in state["joints"] if item["id"] in (2, 3, 4)
                        }
                        record = {
                            "hostTimeUtc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
                            "stateTimeUtc": state.get("timestamp"),
                            "sequence": state.get("sequence"),
                            "sampleAgeMs": state.get("sampleAgeMs"),
                            "feedbackHz": state.get("feedbackHz"),
                            "fault": state.get("fault"),
                            "feedforwardEnabled": state.get("motionGravityAssist", {}).get("enabled"),
                            "joints": joints,
                        }
                        trace = state.get("faultDiagnostics", {}).get("samples", [])
                        if trace and not trace_written:
                            record["faultTrace"] = trace
                            trace_written = True
                        output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                        count += 1
                        first_time = received if first_time is None else first_time
                        last_time = received
                        if state.get("fault"):
                            fault = state["fault"]
                            break
                    elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                        raise RuntimeError("WebSocket closed during capture")

    duration = (last_time - first_time) if first_time is not None and last_time is not None else 0.0
    return destination, count, duration, fault


def main() -> None:
    options = args()
    try:
        path, count, duration, fault = asyncio.run(capture(options))
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Capture failed: {exc}") from exc
    print(f"Saved {count} frames to {path}")
    print(f"Observed rate: {(count - 1) / duration:.1f} Hz" if duration > 0 else "No usable time span")
    if fault:
        print(f"Stopped on fault: {fault}")


if __name__ == "__main__":
    main()
