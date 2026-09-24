"""Record a locally demonstrated B601-DM gesture from measured, settled poses.

Start at a supported home pose, move the arm with the existing local console,
sample each reached key pose (including the return home), then finish. This
tool records feedback; it never enables or moves a motor.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.arm_console import ArmConsoleAdapter, GESTURE_NAME, ModelSafety, checked_target


def state_pose(url: str) -> tuple[dict[int, float], float]:
    if not url.startswith("http://127.0.0.1:") and not url.startswith("http://localhost:"):
        raise ValueError("The arm console must be a loopback address")
    with urlopen(url.rstrip("/") + "/api/state?refresh=1", timeout=3) as response:
        state = json.load(response)
    if not ArmConsoleAdapter._operational(state) or any(joint.get("moving") for joint in state["joints"][:6]):
        raise RuntimeError("Arm must be connected, settled, and fault-free before sampling")
    return ArmConsoleAdapter._angles(state), float(state["gripper"]["widthMm"])


def build_profile(record: dict, *, clearance_mm: float) -> dict:
    samples = record["samples"]
    if not 2 <= len(samples) <= 4:
        raise ValueError("Record 2..4 settled key poses including the return home")
    home = {int(axis): float(value) for axis, value in record["home"].items()}
    axes = [axis for axis in range(1, 7) if any(abs(float(sample[str(axis)]) - home[axis]) >= 0.25
                                                   for sample in samples)]
    if not axes:
        raise ValueError("No meaningful joint movement was recorded")
    if any(abs(float(samples[-1][str(axis)]) - home[axis]) > 0.4 for axis in range(1, 7)):
        raise ValueError("The final sample must return to the recorded home pose")
    profile = {
        "description": record["description"], "speed_dps": record["speed_dps"],
        "waypoints": [
            {f"J{axis}": 0.0 if index == len(samples) - 1 else
             round(float(sample[str(axis)]) - home[axis], 2) for axis in axes}
            for index, sample in enumerate(samples)],
    }
    checked = ArmConsoleAdapter._checked_profile(profile)
    safety = ModelSafety()
    current = home
    for waypoint in checked["waypoints"]:
        target = {axis: home[axis] + waypoint.get(axis, 0.0) for axis in range(1, 7)}
        for axis in axes:
            checked_target(axis, target[axis], hardware=True)
        report = safety.trajectory(current, target, float(record["gripper_width_mm"]))
        if not report["safe"] or report["closest"]["clearanceMm"] < clearance_mm:
            raise ValueError("Recorded gesture crosses the model safety boundary")
        current = target
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Capture the current measured pose as home")
    start.add_argument("--record", type=Path, required=True)
    start.add_argument("--name", required=True)
    start.add_argument("--description", required=True)
    start.add_argument("--speed", type=float, required=True)
    start.add_argument("--url", default="http://127.0.0.1:8870")
    sample = sub.add_parser("sample", help="Capture one settled, measured key pose")
    sample.add_argument("--record", type=Path, required=True)
    sample.add_argument("--url", default="http://127.0.0.1:8870")
    finish = sub.add_parser("finish", help="Model-check and store the gesture in a private demo config")
    finish.add_argument("--record", type=Path, required=True)
    finish.add_argument("--config", type=Path, required=True)
    finish.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.command == "start":
        if not GESTURE_NAME.fullmatch(args.name) or not math.isfinite(args.speed):
            raise ValueError("Use a simple gesture name and finite speed")
        ArmConsoleAdapter._checked_profile({"description": args.description, "speed_dps": args.speed,
                                             "waypoints": [{"J4": 1}, {"J4": 0}]})
        if args.record.exists():
            raise FileExistsError("Record file already exists")
        pose, width = state_pose(args.url)
        record = {"name": args.name, "description": args.description, "speed_dps": args.speed,
                  "home": {str(axis): angle for axis, angle in pose.items()},
                  "gripper_width_mm": width, "samples": []}
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"HOME captured: {args.name}; now use the local console, then run sample at each settled pose")
    elif args.command == "sample":
        record = json.loads(args.record.read_text(encoding="utf-8"))
        if len(record["samples"]) >= 4:
            raise ValueError("At most four key poses are allowed")
        pose, width = state_pose(args.url)
        if abs(width - float(record["gripper_width_mm"])) > 0.5:
            raise ValueError("Gripper width changed during recording")
        record["samples"].append({str(axis): angle for axis, angle in pose.items()})
        args.record.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Sample {len(record['samples'])} captured from measured joints")
    else:
        record = json.loads(args.record.read_text(encoding="utf-8"))
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        adapter = config["adapter"]
        name = record["name"]
        if name in adapter["gestures"] and not args.replace:
            raise ValueError("Gesture name exists; pass --replace to replace it")
        if len(adapter["gestures"]) >= 8 and name not in adapter["gestures"]:
            raise ValueError("At most eight local gestures are allowed")
        clearance = float(adapter.get("min_clearance_mm", 20))
        profile = build_profile(record, clearance_mm=clearance)
        adapter["gestures"][name] = profile
        args.config.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Stored {name} in {args.config}; restart the Gateway demo to load it")


if __name__ == "__main__":
    main()
