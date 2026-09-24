"""aiohttp server for the local SUMMON B601-DM operator console."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from backend.arm import ArmError, ArmService


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
LOG = logging.getLogger("summon.arm_console")


@web.middleware
async def errors(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        return await handler(request)
    except ArmError as exc:
        return web.json_response({"error": {"code": exc.code, "message": exc.message}}, status=exc.status)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return web.json_response({"error": {"code": "INVALID_REQUEST", "message": str(exc)}}, status=400)
    except web.HTTPException:
        raise
    except Exception as exc:
        LOG.exception("Unhandled request failure")
        return web.json_response({"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}, status=500)


def service(request: web.Request) -> ArmService:
    return request.app["arm_service"]


async def body(request: web.Request) -> dict[str, Any]:
    if request.content_length is not None and request.content_length > 16_384:
        raise ArmError("REQUEST_TOO_LARGE", "Request body exceeds 16 KiB", 413)
    value = await request.json()
    if not isinstance(value, dict):
        raise ArmError("INVALID_REQUEST", "JSON object required", 400)
    return value


async def health(request: web.Request) -> web.Response:
    snapshot = service(request).snapshot()
    return web.json_response({"ok": True, "mode": snapshot["mode"], "connected": snapshot["connected"], "fault": snapshot["fault"]})


async def config(request: web.Request) -> web.Response:
    return web.json_response(service(request).config())


async def state(request: web.Request) -> web.Response:
    if request.query.get("refresh") == "1":
        await service(request).refresh()
    return web.json_response(service(request).snapshot())


async def hardware_connect(request: web.Request) -> web.Response:
    value = await body(request)
    port = str(value.get("port") or service(request).port)
    if not port or len(port) > 64:
        raise ArmError("INVALID_REQUEST", "Invalid serial port", 400)
    await service(request).connect_hardware(port)
    return web.json_response(service(request).snapshot())


async def hardware_disconnect(request: web.Request) -> web.Response:
    await body(request)
    await service(request).disconnect_hardware()
    return web.json_response(service(request).snapshot())


async def joint_enabled(request: web.Request) -> web.Response:
    value = await body(request)
    joint_id = int(request.match_info["joint_id"])
    await service(request).driver.enable(
        joint_id,
        bool(value.get("enabled")),
        float(value.get("targetDegrees", 0.0)),
        bool(value.get("confirmSupported", False)),
    )
    return web.json_response(service(request).snapshot())


async def joint_clear_fault(request: web.Request) -> web.Response:
    await body(request)
    joint_id = int(request.match_info["joint_id"])
    await service(request).driver.clear_fault(joint_id)
    return web.json_response(service(request).snapshot())


async def joint_target(request: web.Request) -> web.Response:
    value = await body(request)
    joint_id = int(request.match_info["joint_id"])
    await service(request).driver.set_target(
        joint_id,
        float(value["degrees"]),
        float(value.get("speedDps", 3.0)),
        bool(value.get("highSpeedConfirmed", False)),
        bool(value.get("autoEnable", False)),
    )
    return web.json_response(service(request).snapshot())


async def joint_targets(request: web.Request) -> web.Response:
    value = await body(request)
    raw_targets = value.get("targets")
    if not isinstance(raw_targets, dict):
        raise ArmError("INVALID_REQUEST", "targets must be an object", 400)
    targets = {int(joint_id): float(degrees) for joint_id, degrees in raw_targets.items()}
    await service(request).driver.set_targets(
        targets,
        float(value.get("speedDps", 3.0)),
        bool(value.get("highSpeedConfirmed", False)),
        bool(value.get("autoEnable", False)),
    )
    return web.json_response(service(request).snapshot())


async def gripper_target(request: web.Request) -> web.Response:
    value = await body(request)
    await service(request).driver.set_gripper(float(value["widthMm"]), float(value.get("speedMmS", 12.0)))
    return web.json_response(service(request).snapshot())


async def pose(request: web.Request) -> web.Response:
    value = await body(request)
    await service(request).driver.set_pose(
        str(value["name"]),
        float(value.get("speedDps", 3.0)),
        bool(value.get("highSpeedConfirmed", False)),
    )
    return web.json_response(service(request).snapshot())


async def stop_motion(request: web.Request) -> web.Response:
    await body(request)
    await service(request).driver.stop_motion()
    return web.json_response(service(request).snapshot())


async def disable_all(request: web.Request) -> web.Response:
    value = await body(request)
    await service(request).driver.disable_all(bool(value.get("confirmSupported", False)))
    return web.json_response(service(request).snapshot())


async def gravity_compensation(request: web.Request) -> web.Response:
    value = await body(request)
    await service(request).driver.set_gravity_compensation(bool(value.get("enabled")), bool(value.get("confirmed", False)))
    return web.json_response(service(request).snapshot())


async def websocket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=15, max_msg_size=4096)
    await ws.prepare(request)
    last_sequence = -1
    try:
        while not ws.closed:
            snapshot = service(request).snapshot()
            if snapshot["sequence"] != last_sequence:
                await ws.send_json(snapshot)
                last_sequence = snapshot["sequence"]
            try:
                message = await ws.receive(timeout=0.02)
            except asyncio.TimeoutError:
                continue
            if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                break
    except (ConnectionError, RuntimeError):
        pass
    return ws


async def index(_request: web.Request) -> web.StreamResponse:
    target = DIST / "index.html"
    if not target.exists():
        return web.json_response(
            {"error": {"code": "FRONTEND_NOT_BUILT", "message": "Run npm install && npm run build in arm-console"}},
            status=503,
        )
    return web.FileResponse(target)


async def on_startup(app: web.Application) -> None:
    await app["arm_service"].start()


async def on_cleanup(app: web.Application) -> None:
    await app["arm_service"].close()


def create_app(arm_service: ArmService | None = None) -> web.Application:
    app = web.Application(middlewares=[errors], client_max_size=16_384)
    app["arm_service"] = arm_service or ArmService()
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/config", config)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/ws", websocket)
    app.router.add_post("/api/hardware/connect", hardware_connect)
    app.router.add_post("/api/hardware/disconnect", hardware_disconnect)
    app.router.add_post("/api/joints/{joint_id}/enabled", joint_enabled)
    app.router.add_post("/api/joints/{joint_id}/clear-fault", joint_clear_fault)
    app.router.add_post("/api/joints/{joint_id}/target", joint_target)
    app.router.add_post("/api/joints/targets", joint_targets)
    app.router.add_post("/api/gripper/target", gripper_target)
    app.router.add_post("/api/pose", pose)
    app.router.add_post("/api/motion/stop", stop_motion)
    app.router.add_post("/api/joints/disable-all", disable_all)
    app.router.add_post("/api/gravity-compensation", gravity_compensation)
    if DIST.exists():
        app.router.add_static("/assets/", DIST / "assets", show_index=False)
        model_dir = DIST / "model"
        if model_dir.exists():
            app.router.add_static("/model/", model_dir, show_index=False)
    app.router.add_get("/", index)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SUMMON B601-DM local arm console")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (loopback by default)")
    parser.add_argument("--port", type=int, default=8870, help="HTTP port")
    parser.add_argument("--serial-port", default="COM6", help="MotorBridge DM serial port")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--allow-hardware", action="store_true", help="Permit explicit UI requests to open and write the serial port")
    parser.add_argument("--experimental-gravity-feedforward", action="store_true", help="Enable uncalibrated MIT torque feedforward for supervised tests")
    parser.add_argument("--gripper-closed-deg", type=float, default=None)
    parser.add_argument("--gripper-open-deg", type=float, default=None)
    return parser.parse_args()


def main() -> None:
    args = arguments()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    arm_service = ArmService(
        hardware_allowed=args.allow_hardware,
        port=args.serial_port,
        baud=args.baud,
        gripper_closed_deg=args.gripper_closed_deg,
        gripper_open_deg=args.gripper_open_deg,
        experimental_gravity_feedforward=args.experimental_gravity_feedforward,
    )
    web.run_app(create_app(arm_service), host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
