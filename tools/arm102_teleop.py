"""Bounded six-axis reBot Arm 102 (COM9) to B601-DM (COM6) teleoperation.

The leader is read-only. The follower is controlled through the local arm
console, so its interlocks and motor feedback remain active. A live run needs
fresh feedback from all seven leader servos and an already unfolded follower.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.arm_console import ModelSafety
from backend.arm import ArmError, checked_target  # noqa: E402
from tools.arm102_bus import ReopeningLeader
from tools.arm102_record import LEADER_SIGNS


def request(origin: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(origin + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        problem = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Arm console rejected {path}: {problem}") from exc


def fresh_leader(bus) -> list[float]:
    samples = bus.sync_monitor(list(range(7)))
    stale = [axis for axis in range(7) if samples.get(axis) is None or not samples[axis].reliable]
    if stale:
        raise RuntimeError(f"COM9 feedback is stale for servo IDs {stale}; teleoperation held")
    angles = [float(samples[axis].angle_deg) for axis in range(7)]
    if not all(math.isfinite(value) for value in angles):
        raise RuntimeError("COM9 returned a non-finite angle")
    return angles


def follower_pose(state: dict) -> dict[int, float]:
    if (state.get("mode") != "hardware" or not state.get("connected") or state.get("fault")
            or state.get("gravityCompensation", {}).get("active")
            or type(state.get("sampleAgeMs")) not in (int, float)
            or not math.isfinite(state["sampleAgeMs"]) or state["sampleAgeMs"] > 750):
        raise RuntimeError("COM6 feedback is disconnected, stale, faulted or in gravity mode")
    joints = {int(joint["id"]): joint for joint in state["joints"]}
    if any(joints[axis].get("fault") or joints[axis].get("statusCode") not in (0, 1)
           for axis in range(1, 7)):
        raise RuntimeError("A follower axis has a hardware fault")
    pose = {axis: float(joints[axis]["actualDeg"]) for axis in range(1, 7)}
    if not all(math.isfinite(value) for value in pose.values()):
        raise RuntimeError("Follower angle feedback is incomplete")
    return pose


def ready_for_six_axes(state: dict, pose: dict[int, float]) -> None:
    joints = {int(joint["id"]): joint for joint in state["joints"]}
    if pose[3] > -5 or pose[2] > -3:
        raise RuntimeError("Follower is folded: J3 must be <= -5° and J2 <= -3° "
                           "before J6 can be enabled; unfold under local supervision first")
    if any(joints[axis].get("moving") for axis in range(1, 7)):
        raise RuntimeError("Follower must be stationary before entering teleoperation")


def mapped_pose(home: dict[int, float], leader_home: list[float], leader: list[float]) -> dict[int, float]:
    return {axis: home[axis] + LEADER_SIGNS[axis - 1] * (leader[axis - 1] - leader_home[axis - 1])
            for axis in range(1, 7)}


def checked_step(model: ModelSafety, current: dict[int, float], desired: dict[int, float],
                 width_mm: float, clearance_mm: float, max_step_deg: float) -> dict[int, float]:
    target = {axis: current[axis] + max(-max_step_deg, min(max_step_deg, desired[axis] - current[axis]))
              for axis in range(1, 7)}
    for axis, angle in target.items():
        try:
            checked_target(axis, angle, hardware=True)
        except ArmError as exc:
            raise RuntimeError(f"J{axis} reached its hardware angle boundary") from exc
    report = model.trajectory(current, target, width_mm)["closest"]
    if not report["safe"] or report["clearanceMm"] < clearance_mm:
        raise RuntimeError(f"Model boundary: {report['reason']} {report['links']} "
                           f"{report['clearanceMm']} mm")
    return target


def hold(origin: str) -> None:
    try:
        request(origin, "/api/motion/stop", {})
    except Exception as exc:
        print(f"HOLD REQUEST FAILED: {exc}; use physical E-stop if needed", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leader-port", default="COM9")
    parser.add_argument("--follower-url", default="http://127.0.0.1:8870")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--hz", type=float, default=5)
    parser.add_argument("--speed-dps", type=float, default=5)
    parser.add_argument("--max-offset-deg", type=float, default=8)
    parser.add_argument("--min-clearance-mm", type=float, default=0.5)
    parser.add_argument("--execute", action="store_true", help="Enable and command the follower")
    args = parser.parse_args()
    if not (5 <= args.seconds <= 300 and 2 <= args.hz <= 10 and 0.5 <= args.speed_dps <= 10
            and 1 <= args.max_offset_deg <= 10 and 0.5 <= args.min_clearance_mm <= 20):
        parser.error("Invalid time, rate, speed, offset or clearance")
    if args.leader_port.upper() != "COM9" or args.follower_url.rstrip("/") != "http://127.0.0.1:8870":
        parser.error("This supervised demo is restricted to verified COM9 and local COM6 console")
    model = ModelSafety()
    state = request(args.follower_url, "/api/state?refresh=1")
    pose = follower_pose(state)
    width = float(state["gripper"]["widthMm"])
    report = model.evaluate(pose, width)
    if not report["safe"] or report["clearanceMm"] < args.min_clearance_mm:
        raise RuntimeError(f"Follower start pose fails model boundary: {report}")
    if args.execute:
        ready_for_six_axes(state, pose)
    with ReopeningLeader(args.leader_port) as bus:
        if not all(bus.ping(axis) for axis in range(7)):
            raise RuntimeError("All seven COM9 servos must respond before teleoperation")
        if args.execute:
            # The official leader configure() unloads all servos for manual
            # guidance. Keep its zero and multi-turn counters untouched.
            bus.unlock_all()
        for _ in range(3):
            leader_home = fresh_leader(bus)
            time.sleep(0.1)
        if args.execute:
            for axis in (4, 3, 2, 1, 5, 6):
                state = request(args.follower_url, "/api/state?refresh=1")
                pose = follower_pose(state)
                joint = state["joints"][axis - 1]
                if not joint["enabled"]:
                    state = request(args.follower_url, f"/api/joints/{axis}/enabled",
                                    {"enabled": True, "targetDegrees": pose[axis]})
                    if not state["joints"][axis - 1]["enabled"]:
                        raise RuntimeError(f"J{axis} did not confirm enabled hold")
            state = request(args.follower_url, "/api/state?refresh=1")
            pose = follower_pose(state)
            leader_home = fresh_leader(bus)
        home = pose.copy()
        print(f"TELEOP READY: {'LIVE SIX-AXIS' if args.execute else 'READ-ONLY PREVIEW'}; "
              f"{args.seconds:g}s; follower={home}", flush=True)
        started = time.monotonic()
        count = 0
        try:
            while time.monotonic() - started < args.seconds:
                leader = fresh_leader(bus)
                desired = mapped_pose(home, leader_home, leader)
                if args.execute and any(abs(desired[axis] - home[axis]) > args.max_offset_deg
                                        for axis in range(1, 7)):
                    raise RuntimeError("Leader exceeded the verified relative-angle window")
                state = request(args.follower_url, "/api/state?refresh=1")
                current = follower_pose(state)
                joints = {int(joint["id"]): joint for joint in state["joints"]}
                if args.execute and any(not joints[axis]["enabled"] for axis in range(1, 7)):
                    raise RuntimeError("A follower motor lost its enabled hold")
                if any(float(joints[axis]["stressRatio"]) > 0.7 or
                       float(joints[axis].get("rotorTempC") or 0) >= 70 for axis in range(1, 7)):
                    raise RuntimeError("Follower torque or temperature safety threshold reached")
                target = (checked_step(model, current, desired, width, args.min_clearance_mm,
                                       args.speed_dps / args.hz) if args.execute else desired)
                if args.execute and any(abs(target[axis] - current[axis]) >= 0.15 for axis in range(1, 7)):
                    request(args.follower_url, "/api/joints/targets",
                            {"targets": {str(axis): target[axis] for axis in range(1, 7)},
                             "speedDps": args.speed_dps})
                count += 1
                if count % round(args.hz) == 0:
                    print(json.dumps({"t_s": round(time.monotonic() - started, 1),
                                      "leader": [round(v, 1) for v in leader],
                                      "follower": {f"J{k}": round(v, 1) for k, v in current.items()},
                                      "target": {f"J{k}": round(v, 1) for k, v in target.items()}},
                                     ensure_ascii=False), flush=True)
                time.sleep(max(0, started + count / args.hz - time.monotonic()))
        finally:
            if args.execute:
                hold(args.follower_url)
                print("TELEOP STOPPED: follower axes remain enabled and holding", flush=True)


if __name__ == "__main__":
    main()
