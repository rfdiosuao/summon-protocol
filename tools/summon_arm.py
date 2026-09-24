"""Agent-facing local B601-DM CLI. Importing this module never opens hardware."""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CONSOLE = Path(__file__).resolve().parents[1] / "arm-console"
sys.path.insert(0, str(CONSOLE))

from backend.arm import ArmError, JOINT_SPECS, POSES, checked_target  # noqa: E402
from backend.safety import ModelSafety, SafetyUnavailable, URDF  # noqa: E402


class CliError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def request(url: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    address = url.rstrip("/") + path
    if urllib.parse.urlsplit(address).hostname not in ("127.0.0.1", "localhost", "::1"):
        raise CliError("NONLOCAL_CONSOLE", "Only loopback arm-console URLs are accepted")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    call = urllib.request.Request(address, data=data, headers={"Content-Type": "application/json"}, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(call, timeout=5) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            problem = json.load(exc)["error"]
            raise CliError(str(problem["code"]), str(problem["message"])) from exc
        except (ValueError, KeyError, TypeError):
            raise CliError("CONSOLE_HTTP_ERROR", f"Console returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CliError("CONSOLE_OFFLINE", f"Local arm console unavailable at {url}") from exc


def live_state(url: str, *, hardware: bool = False) -> dict[str, Any]:
    state = request(url, "/api/state?refresh=1")
    if hardware and (state.get("mode") != "hardware" or not state.get("connected")):
        raise CliError("HARDWARE_NOT_CONNECTED", "Arm console is not connected to B601-DM hardware")
    return state


def parse_joint(text: str) -> int:
    if not text.upper().startswith("J") or not text[1:].isdigit():
        raise CliError("INVALID_JOINT", f"Expected J1..J7: {text}")
    joint_id = int(text[1:])
    if joint_id not in range(1, 8):
        raise CliError("INVALID_JOINT", f"Expected J1..J7: {text}")
    return joint_id


def assignments(values: list[str], *, complete: bool = False) -> dict[int, float]:
    result: dict[int, float] = {}
    for token in values:
        if "=" not in token:
            raise CliError("INVALID_ASSIGNMENT", f"Expected J3=-20: {token}")
        name, raw = token.split("=", 1)
        joint_id = parse_joint(name)
        if joint_id > 6 or joint_id in result:
            raise CliError("INVALID_ASSIGNMENT", f"Unknown or repeated arm axis: {name}")
        try:
            value = float(raw)
        except ValueError as exc:
            raise CliError("INVALID_ANGLE", f"Invalid angle: {token}") from exc
        if not math.isfinite(value):
            raise CliError("INVALID_ANGLE", f"Non-finite angle: {token}")
        result[joint_id] = value
    if complete and set(result) != set(range(1, 7)):
        raise CliError("INCOMPLETE_POSE", "Offline --from requires all J1..J6 angles")
    return result


def current_angles(state: dict[str, Any]) -> dict[int, float]:
    try:
        return {int(item["id"]): float(item["actualDeg"]) for item in state["joints"] if int(item["id"]) <= 6}
    except (KeyError, TypeError, ValueError) as exc:
        raise CliError("INVALID_STATE", "Console did not return six measured joint angles") from exc


def source_pose(args: argparse.Namespace) -> tuple[dict[int, float], float, str, dict[str, Any] | None]:
    if args.from_pose and args.from_angles:
        raise CliError("INVALID_SOURCE", "Choose either --from-pose or --from")
    if args.from_pose:
        pose = POSES[args.from_pose]
        return {int(k): float(v) for k, v in pose["joints"].items()}, float(pose["gripperMm"]), f"preset:{args.from_pose}", None
    if args.from_angles:
        return assignments(args.from_angles, complete=True), args.gripper_mm if args.gripper_mm is not None else 60.0, "explicit", None
    state = live_state(args.url)
    return current_angles(state), float(state["gripper"]["widthMm"]), f"{state['mode']}:measured", state


def preview(args: argparse.Namespace, *, execute: bool = False) -> dict[str, Any]:
    requested = assignments(args.targets)
    if not requested:
        raise CliError("INVALID_TARGET", "At least one J1..J6 target is required")
    for joint_id, angle in requested.items():
        checked_target(joint_id, angle, hardware=True)
    start, width, source, state = source_pose(args)
    if execute and state is None:
        raise CliError("LIVE_STATE_REQUIRED", "Execution requires live measured angles, not --from/--from-pose")
    if args.gripper_mm is not None:
        width = args.gripper_mm
    target = dict(start)
    target.update(requested)
    try:
        report = ModelSafety().trajectory(start, target, width)
    except SafetyUnavailable as exc:
        raise CliError("MODEL_CHECK_UNAVAILABLE", str(exc)) from exc
    return {"source": source, "startDeg": {f"J{k}": v for k, v in start.items()},
            "targetDeg": {f"J{k}": v for k, v in target.items()}, "gripperMm": width,
            "model": report, "_state": state}


def check_motion_state(state: dict[str, Any], speed: float, risk_confirmed: bool) -> None:
    if not math.isfinite(speed) or not 0.2 <= speed <= 25:
        raise CliError("INVALID_SPEED", "Speed must be 0.2..25 degrees/s")
    if speed > 10 and not risk_confirmed:
        raise CliError("HIGH_SPEED_CONFIRMATION_REQUIRED", "Speed above 10 degrees/s requires --confirm-risk")
    if state.get("fault"):
        raise CliError("ARM_FAULT", str(state["fault"]))
    if state.get("sampleAgeMs", 0) > 1000:
        raise CliError("STALE_FEEDBACK", "Measured feedback is older than 1000 ms")
    for joint in state["joints"]:
        if joint.get("fault") or int(joint.get("statusCode", 0)) not in (0, 1):
            raise CliError("MOTOR_FAULT", f"J{joint['id']}: {joint.get('fault') or joint.get('statusCode')}")
        if joint.get("moving"):
            raise CliError("ALREADY_MOVING", "Wait for the current motion to finish before sending another target")


def checked_hardware_state(url: str) -> dict[str, Any]:
    return live_state(url, hardware=True)


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "describe":
        return {"cliVersion": 1, "device": "Seeed reBot Arm B601-DM", "model": str(URDF),
                "joints": [item.public() for item in JOINT_SPECS], "poses": POSES,
                "commands": ["describe", "status", "preview", "move", "enable", "disable", "gripper", "gravity", "clear-fault", "stop"],
                "safety": {"model": "official URDF collision_runtime STL", "trajectoryStepDeg": 1,
                           "selfCollision": "exact non-adjacent collision meshes", "tablePlaneZMm": 6,
                           "warningClearanceMm": 20, "hardwareTargets": "safe joint ranges; model-blocked trajectories rejected",
                           "speedDps": [0.2, 25], "speedConfirmationAboveDps": 10,
                           "interlocks": ["J4 holds before J2/J3", "J3 clear of folded stop before J2", "J2/J3 clear before J6"],
                           "limits": "Model cannot detect people, loose objects, support geometry or unmodeled cables"},
                "gravity": {"model": "Pinocchio URDF torque estimate", "hardwareControl": "experimental backend opt-in; previous J3 tests tripped overspeed protection",
                            "notCollisionProtection": True},
                "execution": "preview is offline; writes require connected hardware and --execute; completed motion remains enabled and holding load"}
    if args.command == "status":
        return {"state": live_state(args.url)}
    if args.command == "preview":
        result = preview(args)
        result.pop("_state")
        return result
    if args.command == "move":
        if not math.isfinite(args.speed) or not 0.2 <= args.speed <= 25:
            raise CliError("INVALID_SPEED", "Speed must be 0.2..25 degrees/s")
        result = preview(args, execute=args.execute)
        state = result.pop("_state")
        result["speedDps"] = args.speed
        result["requiresHighSpeedConfirmation"] = args.speed > 10
        if not result["model"]["safe"]:
            raise CliError("MODEL_COLLISION", json.dumps(result["model"]["closest"], ensure_ascii=False))
        if not args.execute:
            result["executed"] = False
            return result
        assert state is not None
        check_motion_state(state, args.speed, args.confirm_risk)
        latest = checked_hardware_state(args.url)
        check_motion_state(latest, args.speed, args.confirm_risk)
        before = current_angles(state)
        after = current_angles(latest)
        if any(abs(before[id] - after[id]) > 0.25 for id in range(1, 7)):
            raise CliError("POSE_CHANGED", "Measured pose changed after preview; run move again")
        requested = assignments(args.targets)
        updated = request(args.url, "/api/joints/targets", {"targets": {str(k): v for k, v in requested.items()},
                           "speedDps": args.speed, "highSpeedConfirmed": args.confirm_risk, "autoEnable": True})
        result.update({"executed": True, "targetAccepted": True, "motionComplete": False, "state": updated})
        return result
    if args.command == "enable":
        ids = {parse_joint(name) for name in args.axes}
        if 7 in ids:
            raise CliError("INVALID_JOINT", "Use gripper for J7")
        if ids & {2, 3}:
            ids.add(4)
        order = [id for id in (4, 3, 2, 1, 5, 6) if id in ids]
        if not args.execute:
            return {"executed": False, "order": [f"J{id}" for id in order], "action": "enable and hold measured position"}
        state = checked_hardware_state(args.url)
        if state.get("fault"):
            raise CliError("ARM_FAULT", str(state["fault"]))
        for id in order:
            angles = current_angles(state)
            state = request(args.url, f"/api/joints/{id}/enabled", {"enabled": True, "targetDegrees": angles[id]})
        return {"executed": True, "order": [f"J{id}" for id in order], "state": state}
    if args.command == "disable":
        if not args.supported:
            raise CliError("SUPPORT_CONFIRMATION_REQUIRED", "Disabling load-bearing axes requires --supported")
        ids = {parse_joint(name) for name in args.axes}
        order = [id for id in (6, 5, 1, 2, 3, 4, 7) if id in ids]
        if not args.execute:
            return {"executed": False, "order": [f"J{id}" for id in order], "action": "disable"}
        state = checked_hardware_state(args.url)
        for id in order:
            state = request(args.url, f"/api/joints/{id}/enabled", {"enabled": False, "confirmSupported": True})
        return {"executed": True, "order": [f"J{id}" for id in order], "state": state}
    if args.command == "gripper":
        if not math.isfinite(args.width) or not 0 <= args.width <= 100:
            raise CliError("INVALID_GRIPPER_WIDTH", "Gripper width must be 0..100 mm")
        if not args.execute:
            return {"executed": False, "widthMm": args.width, "requiresCalibration": True}
        state = checked_hardware_state(args.url)
        if not state.get("gripper", {}).get("calibrated"):
            raise CliError("GRIPPER_NOT_CALIBRATED", "J7 angle-to-width calibration is missing")
        if state.get("fault"):
            raise CliError("ARM_FAULT", str(state["fault"]))
        pose = current_angles(state)
        try:
            safety = ModelSafety()
            old_width = float(state["gripper"]["widthMm"])
            samples = max(1, math.ceil(abs(args.width - old_width)))
            for step in range(samples + 1):
                width = old_width + (args.width - old_width) * step / samples
                report = safety.evaluate(pose, width)
                if not report["safe"]:
                    raise CliError("MODEL_COLLISION", json.dumps(report, ensure_ascii=False))
        except SafetyUnavailable as exc:
            raise CliError("MODEL_CHECK_UNAVAILABLE", str(exc)) from exc
        updated = request(args.url, "/api/gripper/target", {"widthMm": args.width, "speedMmS": args.speed})
        return {"executed": True, "widthMm": args.width, "state": updated}
    if args.command == "gravity":
        if not args.execute:
            return {"executed": False, "requested": args.mode, "state": live_state(args.url)["gravityCompensation"]}
        state = checked_hardware_state(args.url)
        if args.mode == "on" and not args.confirm_risk:
            raise CliError("GRAVITY_CONFIRMATION_REQUIRED", "Experimental gravity control requires --confirm-risk")
        if args.mode == "on" and not state.get("gravityCompensation", {}).get("available"):
            raise CliError("GRAVITY_UNAVAILABLE", "Backend gravity mode is unavailable or suspended")
        updated = request(args.url, "/api/gravity-compensation", {"enabled": args.mode == "on", "confirmed": args.confirm_risk})
        return {"executed": True, "state": updated}
    if args.command == "clear-fault":
        id = parse_joint(args.axis)
        if not args.execute:
            return {"executed": False, "axis": f"J{id}"}
        checked_hardware_state(args.url)
        return {"executed": True, "state": request(args.url, f"/api/joints/{id}/clear-fault", {})}
    if args.command == "stop":
        if not args.execute:
            return {"executed": False, "action": "stop target progression, keep motor hold"}
        checked_hardware_state(args.url)
        return {"executed": True, "state": request(args.url, "/api/motion/stop", {})}
    raise CliError("INVALID_COMMAND", "Unknown command")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SUMMON B601-DM local arm CLI; JSON output, offline model preview")
    p.add_argument("--url", default="http://127.0.0.1:8870", help="Loopback arm-console address")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("describe", help="Offline model, joints, safety and command contract")
    commands.add_parser("status", help="Read current console and hardware feedback")
    for name in ("preview", "move"):
        sub = commands.add_parser(name, help="Check a J1..J6 target path" if name == "preview" else "Preview and optionally execute motion")
        sub.add_argument("targets", nargs="+", metavar="J3=-20")
        sub.add_argument("--from-pose", choices=sorted(POSES), help="Offline model preset; never used for hardware execution")
        sub.add_argument("--from", dest="from_angles", nargs="+", metavar="J3=-5", help="Six explicit source angles for offline preview")
        sub.add_argument("--gripper-mm", type=float, default=None, help="Override gripper width for preview")
        if name == "move":
            sub.add_argument("--speed", type=float, default=3.0)
            sub.add_argument("--execute", action="store_true", help="Explicitly send target to connected hardware")
            sub.add_argument("--confirm-risk", action="store_true", help="Required for >10 deg/s")
    for name in ("enable", "disable"):
        sub = commands.add_parser(name, help="Set one or more motor hold states")
        sub.add_argument("axes", nargs="+", metavar="J3")
        sub.add_argument("--execute", action="store_true")
        if name == "disable":
            sub.add_argument("--supported", action="store_true", help="Confirm raised arm is physically supported")
    sub = commands.add_parser("gripper", help="Move calibrated gripper width")
    sub.add_argument("width", type=float, metavar="WIDTH_MM")
    sub.add_argument("--speed", type=float, default=12.0, help="mm/s")
    sub.add_argument("--execute", action="store_true")
    sub = commands.add_parser("gravity", help="Read or request existing experimental gravity mode")
    sub.add_argument("mode", choices=("on", "off"))
    sub.add_argument("--execute", action="store_true")
    sub.add_argument("--confirm-risk", action="store_true")
    sub = commands.add_parser("clear-fault", help="Clear a reported motor fault without motion")
    sub.add_argument("axis", metavar="J3")
    sub.add_argument("--execute", action="store_true")
    sub = commands.add_parser("stop", help="Stop target progression; leave enabled axes holding")
    sub.add_argument("--execute", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (CliError, ArmError) as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "INVALID_INPUT", "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
