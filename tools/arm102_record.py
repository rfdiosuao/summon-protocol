"""Read-only COM9 arm102 demonstration recorder for the B601-DM follower.

The leader bus is only pinged and sampled. This tool never calls unlock,
set_origin_point, reset_multi_turn, or set_angle on either arm.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.arm_console import ArmConsoleAdapter, GESTURE_NAME
from tools.arm_gesture import build_profile, state_pose

# Seeed's LeRobot rebot_102_leader mapping, servo IDs 0..5 -> follower J1..J6.
# Use relative deltas: the leader's absolute zero need not match the follower.
LEADER_SIGNS = (-1, -1, 1, 1, 1, -1)


def read_leader(port: str) -> dict[int, float]:
    if not port.upper().startswith("COM") or not port[3:].isdigit():
        raise ValueError("Specify the verified arm102 COM port explicitly")
    try:
        from motorbridge_smart_servo import FashionStarServo
    except ImportError as exc:
        raise RuntimeError("Install motorbridge-smart-servo in the recorder Python environment") from exc
    with FashionStarServo(port, baudrate=1_000_000) as bus:
        angles = {}
        for servo_id in range(7):
            if not bus.ping(servo_id):
                raise RuntimeError(f"arm102 servo {servo_id} did not respond")
            reading = bus.read_angle(servo_id, multi_turn=True)
            if not reading.reliable or not math.isfinite(reading.filtered_deg):
                raise RuntimeError(f"arm102 servo {servo_id} has unreliable feedback")
            angles[servo_id] = float(reading.filtered_deg)
    return angles


def map_leader_sample(record: dict, raw: dict[int, float]) -> dict[str, float]:
    leader_home = record["leader_home"]
    follower_home = record["home"]
    return {str(axis): round(float(follower_home[str(axis)]) + LEADER_SIGNS[axis - 1] *
                             (raw[axis - 1] - float(leader_home[str(axis - 1)])), 4)
            for axis in range(1, 7)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="Capture leader and follower baselines without moving either arm")
    start.add_argument("--record", type=Path, required=True)
    start.add_argument("--name", required=True)
    start.add_argument("--description", required=True)
    start.add_argument("--speed", type=float, required=True)
    start.add_argument("--leader-port", default="COM9")
    start.add_argument("--follower-url", default="http://127.0.0.1:8870")
    sample = sub.add_parser("sample", help="Read a settled leader key pose; follower does not move")
    sample.add_argument("--record", type=Path, required=True)
    finish = sub.add_parser("finish", help="Preview mapped path and save a private Gateway gesture")
    finish.add_argument("--record", type=Path, required=True)
    finish.add_argument("--config", type=Path, required=True)
    finish.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.command == "start":
        if not GESTURE_NAME.fullmatch(args.name):
            raise ValueError("Use a simple gesture name")
        if args.record.exists():
            raise FileExistsError("Record file already exists")
        ArmConsoleAdapter._checked_profile({"description": args.description, "speed_dps": args.speed,
                                             "waypoints": [{"J4": 1}, {"J4": 0}]})
        follower, width = state_pose(args.follower_url)
        leader = read_leader(args.leader_port)
        record = {"source": "arm102_read_only", "name": args.name, "description": args.description,
                  "speed_dps": args.speed, "port": args.leader_port,
                  "leader_home": {str(axis): angle for axis, angle in leader.items()},
                  "home": {str(axis): angle for axis, angle in follower.items()},
                  "gripper_width_mm": width, "samples": []}
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print("arm102 and B601-DM baselines captured; move only the leader, then sample each settled pose")
    elif args.command == "sample":
        record = json.loads(args.record.read_text(encoding="utf-8"))
        if record.get("source") != "arm102_read_only" or len(record["samples"]) >= 4:
            raise ValueError("Invalid arm102 recording or too many samples")
        raw = read_leader(record["port"])
        mapped = map_leader_sample(record, raw)
        record["samples"].append(mapped)
        args.record.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"sample": len(record["samples"]), "follower_equivalent_deg": mapped}))
    else:
        record = json.loads(args.record.read_text(encoding="utf-8"))
        if record.get("source") != "arm102_read_only":
            raise ValueError("Not an arm102 recording")
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        gestures = config["adapter"]["gestures"]
        if record["name"] in gestures and not args.replace:
            raise ValueError("Gesture name exists; pass --replace to replace it")
        if len(gestures) >= 8 and record["name"] not in gestures:
            raise ValueError("At most eight local gestures are allowed")
        threshold = float(config["adapter"].get("min_clearance_mm", 20))
        profile = build_profile(record, clearance_mm=threshold)
        gestures[record["name"]] = profile
        args.config.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Stored mapped {record['name']} in {args.config}; restart the Gateway demo to load it")


if __name__ == "__main__":
    main()
