"""Preview and execute one simultaneous, independently paced arm movement.

Example: python tools/arm_choreo.py J1=-5@20 J2=-5@0.8 J3=-7@0.8 \
    J4=-3@20 J5=-30@25 J6=-3@5 --execute --confirm-risk
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time

from summon_arm import (CliError, ModelSafety, check_motion_state,
                        checked_target, current_angles, live_state, request)


def parse_targets(values: list[str]) -> tuple[dict[int, float], dict[int, float]]:
    targets: dict[int, float] = {}
    speeds: dict[int, float] = {}
    for value in values:
        try:
            name, setting = value.split("=", 1)
            angle, speed = (float(part) for part in setting.split("@", 1))
            axis = int(name[1:]) if name.startswith("J") else 0
        except (ValueError, TypeError) as exc:
            raise CliError("INVALID_ASSIGNMENT", f"Expected J1=-5@10: {value}") from exc
        if axis not in range(1, 7) or axis in targets or not math.isfinite(angle) or not math.isfinite(speed):
            raise CliError("INVALID_ASSIGNMENT", f"Invalid or repeated axis: {value}")
        checked_target(axis, angle, hardware=True)
        if not 0.2 <= speed <= 25:
            raise CliError("INVALID_SPEED", f"J{axis} speed must be 0.2..25 degrees/s")
        targets[axis], speeds[axis] = angle, speed
    if not targets:
        raise CliError("INVALID_TARGET", "At least one axis is required")
    return targets, speeds


def preview(start: dict[int, float], targets: dict[int, float], speeds: dict[int, float],
            width: float) -> dict:
    end = dict(start)
    end.update(targets)
    model = ModelSafety()
    direct = model.trajectory(start, end, width)
    if not direct["safe"]:
        raise CliError("MODEL_COLLISION", json.dumps(direct["closest"]))
    # Different motor speeds trace a curved path through configuration space.
    # Check that path as well as the straight joint interpolation above.
    effective = {axis: min(speed, {1: 3.0, 2: 3.0, 3: 3.0, 4: 10.0, 5: 10.0, 6: 3.0}.get(axis, speed))
                 for axis, speed in speeds.items()}
    duration = max(abs(targets[axis] - start[axis]) / effective[axis] for axis in targets)
    samples = max(1, math.ceil(duration / 0.25))
    closest = direct["closest"]
    for index in range(samples + 1):
        elapsed = duration * index / samples
        pose = dict(start)
        for axis in targets:
            delta = targets[axis] - start[axis]
            progress = min(abs(delta), effective[axis] * elapsed)
            pose[axis] = start[axis] + math.copysign(progress, delta)
        result = model.evaluate(pose, width)
        if not result["safe"]:
            raise CliError("MODEL_COLLISION", json.dumps(result))
        if result["clearanceMm"] < closest["clearanceMm"]:
            closest = result
    return {"targetDeg": {f"J{axis}": angle for axis, angle in targets.items()},
            "speedDps": {f"J{axis}": speed for axis, speed in speeds.items()},
            "model": {"safe": True, "closest": closest}, "estimatedSeconds": round(duration, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="+", metavar="J1=-5@10")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-risk", action="store_true", help="Required for every execution containing an axis above 10 degrees/s")
    parser.add_argument("--url", default="http://127.0.0.1:8870")
    args = parser.parse_args()
    try:
        targets, speeds = parse_targets(args.targets)
        state = live_state(args.url, hardware=args.execute)
        start = current_angles(state)
        report = preview(start, targets, speeds, float(state["gripper"]["widthMm"]))
        if not args.execute:
            print(json.dumps({"ok": True, "executed": False, **report}, ensure_ascii=False))
            return 0
        check_motion_state(state, max(speeds.values()), args.confirm_risk)
        latest = live_state(args.url, hardware=True)
        check_motion_state(latest, max(speeds.values()), args.confirm_risk)
        if any(abs(start[axis] - current_angles(latest)[axis]) > 0.25 for axis in range(1, 7)):
            raise CliError("POSE_CHANGED", "Pose changed after preview")
        if any(not latest["joints"][axis - 1]["enabled"] for axis in targets):
            raise CliError("MOTOR_NOT_ENABLED", "Enable and verify every selected axis before choreography")
        try:
            for axis in (2, 3, 4, 5, 1, 6):
                if axis in targets:
                    request(args.url, f"/api/joints/{axis}/target", {
                        "degrees": targets[axis], "speedDps": speeds[axis],
                        "highSpeedConfirmed": args.confirm_risk and speeds[axis] > 10})
            beginning = time.monotonic()
            while time.monotonic() - beginning < max(30, report["estimatedSeconds"] + 10):
                current = live_state(args.url, hardware=True)
                j3 = current["joints"][2]
                if (current.get("fault") or current.get("sampleAgeMs", 0) > 750
                        or j3["stressRatio"] > 0.75 or j3["rotorTempC"] >= 65):
                    raise CliError("MOTION_ABORTED", f"Controller feedback limit: {current.get('fault') or 'J3 load/feedback'}")
                observed = current_angles(current)
                print(json.dumps({"elapsed": round(time.monotonic() - beginning, 2),
                                  "actualDeg": {f"J{axis}": round(observed[axis], 2) for axis in targets},
                                  "J3stressPct": round(j3["stressRatio"] * 100)}, ensure_ascii=False), flush=True)
                if all(abs(observed[axis] - target) < 0.35 for axis, target in targets.items()):
                    print(json.dumps({"ok": True, "completed": True, **report}, ensure_ascii=False))
                    return 0
                time.sleep(0.25)
            raise CliError("MOTION_TIMEOUT", "One or more axes did not reach the target")
        except BaseException:
            request(args.url, "/api/motion/stop", {})
            raise
    except CliError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
