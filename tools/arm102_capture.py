"""Continuously capture a reBot Arm 102 leader demonstration from COM9.

Only servo ping/angle reads are sent; the B601-DM follower never moves.
Output is JSONL: one metadata row followed by timestamped reliable samples.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.arm_gesture import state_pose
from tools.arm102_bus import ReopeningLeader
from tools.arm102_record import LEADER_SIGNS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", default="COM9")
    parser.add_argument("--seconds", type=float, default=45)
    parser.add_argument("--hz", type=float, default=10)
    parser.add_argument("--follower-url", default="http://127.0.0.1:8870")
    args = parser.parse_args()
    if not 5 <= args.seconds <= 120 or not 2 <= args.hz <= 20:
        raise ValueError("Capture needs 5..120 seconds at 2..20 Hz")
    if args.output.exists():
        raise FileExistsError("Output file already exists")
    if not args.port.upper().startswith("COM") or not args.port[3:].isdigit():
        raise ValueError("Specify a verified COM port")
    follower, width = state_pose(args.follower_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ReopeningLeader(args.port) as bus:
        last_fresh = [time.monotonic()] * 7
        def read() -> tuple[list[float], list[bool], list[int]]:
            readings = bus.sync_monitor(list(range(7)))
            now = time.monotonic()
            result, reliable, ages = [], [], []
            for axis in range(7):
                sample = readings.get(axis)
                if sample is None or not math.isfinite(sample.angle_deg):
                    raise RuntimeError(f"arm102 servo {axis} has no valid feedback")
                if sample.reliable:
                    last_fresh[axis] = now
                result.append(round(float(sample.angle_deg), 3))
                reliable.append(bool(sample.reliable))
                ages.append(round((now - last_fresh[axis]) * 1000))
            return result, reliable, ages

        if not all(bus.ping(axis) for axis in range(7)):
            raise RuntimeError("One or more arm102 servos did not respond")
        baseline, _, _ = read()
        header = {"type": "header", "source": "arm102_com_read_only", "port": args.port,
                  "leader_home_deg": baseline,
                  "follower_home_deg": {f"J{axis}": follower[axis] for axis in range(1, 7)},
                  "gripper_width_mm": width, "leader_signs": LEADER_SIGNS, "requested_hz": args.hz}
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(header, ensure_ascii=False) + "\n")
            stream.flush()
            started = time.monotonic()
            count = 0
            missed = 0
            print(f"RECORDING READY: {args.port}; {args.seconds:g}s; {args.output}", flush=True)
            try:
                while time.monotonic() - started < args.seconds:
                    now = time.monotonic()
                    try:
                        raw, reliable, ages = read()
                        missed = 0
                    except RuntimeError:
                        missed += 1
                        if missed >= 10:
                            raise RuntimeError("arm102 feedback was lost repeatedly; recording stopped")
                        time.sleep(1 / args.hz)
                        continue
                    mapped = {f"J{axis}": round(follower[axis] + LEADER_SIGNS[axis - 1] *
                                                (raw[axis - 1] - baseline[axis - 1]), 3)
                              for axis in range(1, 7)}
                    stream.write(json.dumps({"type": "sample", "t_ms": round((now - started) * 1000),
                                             "leader_deg": raw, "reliable": reliable, "age_ms": ages,
                                             "follower_equivalent_deg": mapped},
                                            separators=(",", ":")) + "\n")
                    stream.flush()
                    count += 1
                    time.sleep(max(0, started + count / args.hz - time.monotonic()))
            except KeyboardInterrupt:
                pass
            print(f"CAPTURE COMPLETE: {count} samples; {args.output}", flush=True)


if __name__ == "__main__":
    main()
