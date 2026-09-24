"""Run a private, local LIVE Hub + B601-DM Gateway + model Agent demo.

The browser talks to Hub. Only the Gateway talks to the loopback arm console.
The model observes the arm and proposes locally bounded short motion.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.arm_console import ArmConsoleAdapter
from gateway.runtime import Gateway
from hub.app import create_app
from hub.passport_agent import run as run_agent


PROFILE = {"model": "Seeed reBot B601-DM", "firmware": "MotorBridge DM", "adapter_version": "arm-console-1"}
SHELL_ID = "arm_demo"


def private_secrets(folder: Path) -> dict[str, str]:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "secrets.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if set(value) != {"gateway_token", "operator_code"}:
            raise ValueError("Invalid existing demo secrets file")
        return value
    value = {"gateway_token": secrets.token_urlsafe(32), "operator_code": secrets.token_urlsafe(16)}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream)
    return value


async def serve(config_path: Path, run_dir: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config.get("model"), dict):
        raise ValueError("A configured real model is required; see tools/arm_demo.example.json")
    arm = ArmConsoleAdapter(config["adapter"])
    if config.get("origin", "http://127.0.0.1:8840") != "http://127.0.0.1:8840":
        raise ValueError("Local demo origin must stay on loopback; use deployed HTTPS Hub for remote access")
    secrets_value = private_secrets(run_dir)
    hub_config = {
        "origin": "http://127.0.0.1:8840",
        "mode": "LIVE", "secure_cookie": False,
        "operator_codes": {secrets_value["operator_code"]: "demo_operator"},
        "gateway_tokens": {SHELL_ID: secrets_value["gateway_token"]},
        "shell_labels": {SHELL_ID: "B601-DM 机械臂"},
        "shell_policies": {SHELL_ID: {
            "capabilities": arm.capabilities, "allowed_actions": arm.capabilities,
            "identity_gates": ["web"], "stop_kind": "physical_estop", "gate": "whitelist",
        }},
        "device_profiles": {SHELL_ID: PROFILE}, "demo_shell_ids": [],
    }
    hub = create_app(run_dir / "hub.db", hub_config)

    async def demo_page(request: web.Request) -> web.FileResponse:
        return web.FileResponse(ROOT / "web" / "arm-demo.html")

    async def demo_status(request: web.Request) -> web.Response:
        hub["hub"].operator(request)
        state = arm.state or {}
        return web.json_response({
            "model": config["model"].get("name", "configured model"),
            "connected": state.get("connected") is True,
            "fault": state.get("fault"),
            "sample_age_ms": state.get("sampleAgeMs"),
            "joints": [{key: joint.get(key) for key in
                        ("id", "actualDeg", "targetDeg", "stressRatio", "rotorTempC", "enabled", "fault")}
                       for joint in state.get("joints", [])],
            "gestures": [{"name": name, "description": profile["description"],
                          "speed_dps": profile["speed_dps"]}
                         for name, profile in arm.gestures.items()],
            "motion_windows": {f"J{axis}": list(window) for axis, window in arm.motion_bounds.items()},
        })

    async def demo_trace(request: web.Request) -> web.Response:
        hub["hub"].operator(request)
        trace = run_dir / "decision-trace.jsonl"
        items = []
        if trace.exists():
            with trace.open("rb") as stream:
                stream.seek(max(0, trace.stat().st_size - 131072))
                for raw in stream.read().splitlines()[-120:]:
                    try:
                        items.append(json.loads(raw.decode("utf-8")))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
        return web.json_response({"items": items})

    hub.router.add_get("/demo", demo_page)
    hub.router.add_get("/demo/status", demo_status)
    hub.router.add_get("/demo/trace", demo_trace)
    runner = web.AppRunner(hub, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8840)
    gateway = None
    tasks: list[asyncio.Task] = []
    try:
        await site.start()
        gateway_config = {
            "hub_url": "http://127.0.0.1:8840", "shell_id": SHELL_ID,
            "database": str(run_dir / "gateway.db"), "mode": "LIVE", "enabled": True,
            "experience_upload": True, "profile": PROFILE,
        }
        gateway = Gateway(gateway_config, secrets_value["gateway_token"], arm)
        agent_config = {
            "hub_url": "http://127.0.0.1:8840", "name": "B601-DM 演示 Agent",
            "bio": "模型观察六轴姿态并规划现场边界内的短动作；网关确认实机反馈。",
            "enable_arm_gestures": True, "model": config["model"],
            "enable_arm_planning": bool(arm.motion_bounds),
            "motion_policy": {"absolute_joint_windows_deg":
                              {f"J{axis}": list(window) for axis, window in arm.motion_bounds.items()},
                              "max_speed_dps": arm.max_motion_speed_dps,
                              "max_relative_offset_deg": 10,
                              "must_return_to_start": True},
            "gesture_catalog": [
                {"name": name, "description": profile["description"],
                 "axes": [f"J{axis}" for axis in profile["axes"]],
                 "relative_waypoints_deg": [
                     {f"J{axis}": offset for axis, offset in step.items()} for step in profile["waypoints"]],
                 "speed_dps": profile["speed_dps"]}
                for name, profile in arm.gestures.items()],
            "conversation_log": str(run_dir / "decision-trace.jsonl"),
        }
        (run_dir / "agent.json").write_text(json.dumps(agent_config, ensure_ascii=False), encoding="utf-8")
        tasks = [asyncio.create_task(gateway.run(), name="gateway"),
                 asyncio.create_task(run_agent(run_dir / "agent.json", run_dir / "agent-credentials.json"), name="agent")]
        print("SUMMON LIVE arm demo: http://127.0.0.1:8840/demo", flush=True)
        print(f"Private operator access code: {run_dir / 'secrets.json'}", flush=True)
        print(f"Decision and device receipt log: {run_dir / 'decision-trace.jsonl'}", flush=True)
        print("Press Ctrl+C to stop. The arm must remain supported before disconnecting.", flush=True)
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if gateway is not None:
            gateway.close()
        await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Private model and verified gesture configuration")
    parser.add_argument("--run-dir", type=Path, required=True, help="Private state directory outside the repository")
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.config.resolve(), args.run_dir.resolve()))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Arm demo stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
