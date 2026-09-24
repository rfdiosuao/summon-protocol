"""B601-DM arm adapter for the existing SUMMON Gateway lease and receipt flow.

The local console owns the serial port. This adapter only talks to its loopback
API and exposes locally bounded short motions and gestures.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

from gateway.errors import MotionRejected


ARM_CONSOLE = Path(__file__).resolve().parents[1] / "arm-console"
if str(ARM_CONSOLE) not in sys.path:
    sys.path.insert(0, str(ARM_CONSOLE))

from backend.arm import ArmError, checked_target  # noqa: E402
from backend.safety import ModelSafety  # noqa: E402


GESTURE_NAME = re.compile(r"[a-z][a-z0-9_]{1,31}\Z")
LOG = logging.getLogger("summon.gateway.arm")


class ArmConsoleAdapter:
    """Translate leased arm.gesture actions into checked local console motion."""

    capabilities = ["arm.gesture"]
    stop_kind = "physical_estop"

    def __init__(self, config: dict):
        address = config.get("url", "http://127.0.0.1:8870")
        parsed = urlsplit(address)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or parsed.port is None):
            raise ValueError("Arm console URL must be a bare loopback HTTP origin")
        if config.get("physical_estop_confirmed") is not True:
            raise ValueError("Arm Gateway requires a verified, reachable physical emergency stop")
        if config.get("motion_profiles_verified") is not True:
            raise ValueError("Arm Gateway requires locally verified gesture profiles")
        clearance = config.get("min_clearance_mm", 20)
        if (isinstance(clearance, bool) or not isinstance(clearance, (int, float))
                or not math.isfinite(clearance) or not 0.5 <= clearance <= 20):
            raise ValueError("min_clearance_mm must be 0.5..20 based on verified local geometry")
        self.min_clearance_mm = float(clearance)
        offset_limit = config.get("max_relative_offset_deg", 10)
        if (isinstance(offset_limit, bool) or not isinstance(offset_limit, (int, float))
                or not math.isfinite(offset_limit) or not 1 <= offset_limit <= 30):
            raise ValueError("max_relative_offset_deg must be 1..30")
        self.max_relative_offset_deg = float(offset_limit)
        gestures = config.get("gestures")
        if (not isinstance(gestures, dict) or not 1 <= len(gestures) <= 8
                or any(not isinstance(name, str) or not GESTURE_NAME.fullmatch(name) for name in gestures)):
            raise ValueError("Configure 1..8 named arm.gesture profiles")
        self.gestures = {name: self._checked_profile(profile, self.max_relative_offset_deg)
                         for name, profile in gestures.items()}
        raw_bounds = config.get("motion_bounds")
        self.motion_bounds: dict[int, tuple[float, float]] = {}
        self.max_motion_speed_dps = 0.0
        self.motion_axis_speed_caps_dps: dict[int, float] = {}
        if raw_bounds is not None:
            if not isinstance(raw_bounds, dict) or not raw_bounds or len(raw_bounds) > 6:
                raise ValueError("motion_bounds must name locally verified J1..J6 windows")
            for name, window in raw_bounds.items():
                if (name not in {f"J{axis}" for axis in range(1, 7)} or not isinstance(window, list)
                        or len(window) != 2 or any(isinstance(value, bool) or not isinstance(value, (int, float))
                                                    or not math.isfinite(value) for value in window)
                        or window[0] >= window[1]):
                    raise ValueError("Invalid local motion window")
                axis = int(name[1:])
                for endpoint in window:
                    try:
                        checked_target(axis, float(endpoint), hardware=True)
                    except ArmError as exc:
                        raise ValueError("Local motion window exceeds hardware joint limits") from exc
                self.motion_bounds[axis] = (float(window[0]), float(window[1]))
            maximum = config.get("max_motion_speed_dps", 8)
            if (isinstance(maximum, bool) or not isinstance(maximum, (int, float))
                    or not math.isfinite(maximum) or not 0.5 <= maximum <= 10):
                raise ValueError("max_motion_speed_dps must be 0.5..10")
            self.max_motion_speed_dps = float(maximum)
            raw_caps = config.get("motion_axis_speed_caps_dps", {})
            if not isinstance(raw_caps, dict) or set(raw_caps) - set(raw_bounds):
                raise ValueError("Axis speed caps must name configured motion axes")
            for name, value in raw_caps.items():
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not 0.2 <= value <= self.max_motion_speed_dps):
                    raise ValueError("Invalid axis speed cap")
                self.motion_axis_speed_caps_dps[int(name[1:])] = float(value)
        self.capabilities = ["arm.gesture"] + (["arm.observe", "arm.motion"] if self.motion_bounds else [])
        self.url = address.rstrip("/")
        self.http: aiohttp.ClientSession | None = None
        self.safety: ModelSafety | None = None
        self.state: dict | None = None
        self.last_poll = 0.0
        self.poll_task: asyncio.Task | None = None
        self.in_motion = False
        self.expected_targets: dict[int, float] = {}

    @staticmethod
    def _checked_profile(value: object, max_relative_offset_deg: float = 10) -> dict:
        if not isinstance(value, dict) or set(value) not in ({"speed_dps", "waypoints"},
                                                               {"description", "speed_dps", "waypoints"}):
            raise ValueError("Gesture profile needs speed_dps and waypoints; description is optional")
        description = value.get("description", "Locally verified arm gesture")
        if (not isinstance(description, str) or not 1 <= len(description.strip()) <= 160
                or any(ord(char) < 32 for char in description)):
            raise ValueError("Gesture description must be 1..160 printable characters")
        speed = value["speed_dps"]
        raw_steps = value["waypoints"]
        if not isinstance(raw_steps, list) or not 2 <= len(raw_steps) <= 4:
            raise ValueError("Gesture requires 2..4 relative waypoints")
        listed_axes: set[int] = set()
        steps: list[dict[int, float]] = []
        for raw in raw_steps:
            if not isinstance(raw, dict) or not raw:
                raise ValueError("Each waypoint must contain J1..J6 offsets")
            step = {}
            for name, offset in raw.items():
                if not isinstance(name, str) or name not in {f"J{axis}" for axis in range(1, 7)}:
                    raise ValueError("Waypoint axes must be J1..J6")
                if (isinstance(offset, bool) or not isinstance(offset, (int, float))
                        or not math.isfinite(offset) or abs(offset) > max_relative_offset_deg):
                    raise ValueError("Gesture offset exceeds the locally verified relative limit")
                axis = int(name[1:])
                step[axis] = float(offset)
                listed_axes.add(axis)
            steps.append(step)
        axes = {axis for axis in listed_axes if any(step.get(axis, 0) != 0 for step in steps)}
        if not axes:
            raise ValueError("Gesture must contain a nonzero motion")
        if any(steps[-1].get(axis, 0) != 0 for axis in axes):
            raise ValueError("Last waypoint must return every moved axis to its starting angle")
        if isinstance(speed, dict):
            if (not {f"J{axis}" for axis in axes}.issubset(speed)
                    or set(speed) - {f"J{axis}" for axis in listed_axes}):
                raise ValueError("Per-axis speeds must cover every moved axis")
            checked_speed = {}
            for name, value in speed.items():
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not 0.2 <= value <= 10):
                    raise ValueError("Per-axis speed must be 0.2..10 degrees/s")
                if int(name[1:]) in axes:
                    checked_speed[int(name[1:])] = float(value)
            speed = checked_speed
        elif (isinstance(speed, bool) or not isinstance(speed, (int, float))
              or not math.isfinite(speed) or not 0.5 <= speed <= 10):
            raise ValueError("Gateway gestures must use 0.5..10 degrees/s")
        else:
            speed = float(speed)
        return {"description": description.strip(), "speed_dps": speed,
                "waypoints": steps, "axes": sorted(axes)}

    @staticmethod
    def _speeds(profile: dict) -> dict[int, float]:
        speed = profile["speed_dps"]
        return speed if isinstance(speed, dict) else {axis: speed for axis in profile["axes"]}

    async def _json(self, path: str, payload: dict | None = None) -> dict:
        if self.http is None:
            raise RuntimeError("Arm console session is closed")
        address = self.url + path
        async with (self.http.get(address) if payload is None else self.http.post(address, json=payload)) as response:
            body = await response.json()
            if response.status != 200:
                problem = body.get("error", {}) if isinstance(body, dict) else {}
                raise RuntimeError(f"Arm console rejected request: {problem.get('code', response.status)}")
            if not isinstance(body, dict):
                raise RuntimeError("Arm console returned an invalid state")
            return body

    async def _read(self, refresh: bool = False) -> dict:
        state = await self._json("/api/state?refresh=1" if refresh else "/api/state")
        self.state = state
        self.last_poll = time.monotonic()
        return state

    @staticmethod
    def _operational(state: dict) -> bool:
        if (state.get("mode") != "hardware" or state.get("connected") is not True or state.get("fault")
                or state.get("gravityCompensation", {}).get("active")):
            return False
        age = state.get("sampleAgeMs")
        if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or age > 750:
            return False
        joints = state.get("joints")
        if not isinstance(joints, list) or len(joints) < 6:
            return False
        return all(joint.get("statusCode") in (0, 1) and not joint.get("fault") for joint in joints)

    @staticmethod
    def _angles(state: dict) -> dict[int, float]:
        angles = {int(joint["id"]): float(joint["actualDeg"]) for joint in state["joints"] if int(joint["id"]) <= 6}
        if set(angles) != set(range(1, 7)) or not all(math.isfinite(value) for value in angles.values()):
            raise RuntimeError("Incomplete measured arm pose")
        return angles

    @staticmethod
    def _commanded_motion(state: dict) -> bool:
        # The controller's `moving` bit can flicker at a stationary hold from
        # encoder velocity noise. A meaningful target error distinguishes an
        # active command from that idle jitter.
        return any(joint.get("moving") and
                   abs(float(joint["targetDeg"]) - float(joint["actualDeg"])) > 0.5
                   for joint in state["joints"][:6])

    async def _poll(self) -> None:
        while True:
            try:
                await self._read()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOG.warning("Arm feedback poll stopped: %s", exc)
                self.state = None
                return
            await asyncio.sleep(0.15)

    async def open(self) -> None:
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False)
        try:
            self.safety = await asyncio.get_running_loop().run_in_executor(None, ModelSafety)
            state = await self._read(refresh=True)
            if not self._operational(state) or self._commanded_motion(state):
                raise RuntimeError("B601-DM is not connected, idle and fault-free")
            self.poll_task = asyncio.create_task(self._poll())
        except BaseException:
            await self.close()
            raise

    def healthy(self) -> bool:
        state = self.state
        if (self.http is None or self.poll_task is None or self.poll_task.done() or state is None
                or time.monotonic() - self.last_poll > 1 or not self._operational(state)):
            LOG.warning("Arm adapter unhealthy: poll_done=%s state=%s age=%s fault=%s sample_age=%s",
                        self.poll_task.done() if self.poll_task else None,
                        state.get("connected") if state else None,
                        round(time.monotonic() - self.last_poll, 2),
                        state.get("fault") if state else None,
                        state.get("sampleAgeMs") if state else None)
            return False
        if not self.in_motion and self._commanded_motion(state):
            LOG.warning("Arm adapter saw unleased motion: %s",
                        [(joint["id"], joint.get("actualDeg"), joint.get("targetDeg"))
                         for joint in state["joints"][:6] if joint.get("moving")])
            return False
        if self.in_motion and self.expected_targets:
            targets = {int(joint["id"]): float(joint["targetDeg"]) for joint in state["joints"] if int(joint["id"]) in self.expected_targets}
            if any(abs(targets.get(axis, math.inf) - value) > 0.5 for axis, value in self.expected_targets.items()):
                LOG.warning("Arm adapter target changed outside Gateway: expected=%s actual=%s",
                            self.expected_targets, targets)
                return False
        return True

    def _preview(self, start: dict[int, float], plan: list[dict[int, float]], width: float,
                 speeds: dict[int, float] | None = None) -> None:
        assert self.safety is not None
        current = start
        for target in plan:
            report = self.safety.trajectory(current, target, width)
            if not report["safe"] or report["closest"]["clearanceMm"] < self.min_clearance_mm:
                closest = report["closest"]
                raise MotionRejected(f"Arm motion blocked by {closest['reason'] or 'LOW_CLEARANCE'}: {closest['links']}")
            if speeds:
                rates = {axis: min(speed, {1: 3.0, 2: 3.0, 3: 3.0, 4: 10.0, 5: 10.0, 6: 3.0}.get(axis, speed))
                         for axis, speed in speeds.items()}
                duration = max(abs(target[axis] - current[axis]) / rates[axis] for axis in rates)
                for index in range(max(1, math.ceil(duration / 0.25)) + 1):
                    elapsed = min(duration, index * 0.25)
                    pose = dict(current)
                    for axis, rate in rates.items():
                        delta = target[axis] - current[axis]
                        pose[axis] += math.copysign(min(abs(delta), rate * elapsed), delta)
                    observed = self.safety.evaluate(pose, width)
                    if not observed["safe"] or observed["clearanceMm"] < self.min_clearance_mm:
                        raise MotionRejected("Independently paced axes cross the model safety boundary")
            current = target

    async def _wait_target(self, target: dict[int, float], axes: list[int], start: dict[int, float], deadline: float) -> None:
        consecutive = 0
        while time.monotonic() < deadline:
            state = await self._read()
            if not self._operational(state):
                raise RuntimeError("Arm feedback became stale or faulted during motion")
            angles = self._angles(state)
            if any(abs(angles[axis] - start[axis]) > 0.5 for axis in range(1, 7) if axis not in axes):
                raise RuntimeError("An uncontrolled arm axis drifted during the gesture")
            assert self.safety is not None
            report = await asyncio.get_running_loop().run_in_executor(
                None, self.safety.evaluate, angles, float(state["gripper"]["widthMm"]))
            if not report["safe"] or report["clearanceMm"] < self.min_clearance_mm:
                raise RuntimeError("Measured arm pose crossed the model safety boundary")
            joints = {joint["id"]: joint for joint in state["joints"]}
            if any(abs(float(joints[axis]["targetDeg"]) - target[axis]) > 0.5 for axis in axes):
                raise RuntimeError("Arm target changed outside the Gateway")
            arrived = all(abs(angles[axis] - target[axis]) <= 0.4 and
                          abs(float(joints[axis].get("velocityDps", 0))) <= 2.0 for axis in axes)
            consecutive = consecutive + 1 if arrived else 0
            if consecutive >= 2:
                return
            await asyncio.sleep(0.08)
        raise TimeoutError("Arm did not reach the configured gesture waypoint")

    async def observe(self) -> dict:
        state = await self._read(refresh=True)
        if not self._operational(state):
            raise RuntimeError("Arm feedback is unavailable")
        angles = self._angles(state)
        assert self.safety is not None
        width = float(state["gripper"]["widthMm"])
        report = await asyncio.get_running_loop().run_in_executor(None, self.safety.evaluate, angles, width)
        hand = await asyncio.get_running_loop().run_in_executor(None, self.safety.end_effector_pose, angles)
        observed = {
            "joints_deg": {f"J{axis}": round(value, 2) for axis, value in angles.items()},
            "hand_xyz_mm": hand["xyz_mm"],
            "hand_rpy_deg": hand["rpy_deg"],
            "clearance_mm": report["clearanceMm"],
            "boundary": report.get("reason") or "CLEAR",
            "enabled_axes": [joint["id"] for joint in state["joints"][:6] if joint["enabled"]],
            "stress_pct": {f"J{joint['id']}": round(float(joint.get("stressRatio", 0)) * 100)
                           for joint in state["joints"][:6]},
        }
        return {"evidence": "controller_feedback", "result": json.dumps(observed, separators=(",", ":"))}

    def _motion_profile(self, args: dict, start: dict[int, float]) -> dict:
        if not self.motion_bounds or not isinstance(args, dict) or set(args) != {"intent", "speed_dps", "waypoints"}:
            raise MotionRejected("Free-form motion is not locally enabled")
        intent = args["intent"]
        if not isinstance(intent, str) or not 1 <= len(intent.strip()) <= 120:
            raise MotionRejected("Motion intent is missing")
        try:
            profile = self._checked_profile({"description": intent, "speed_dps": args["speed_dps"],
                                             "waypoints": args["waypoints"]}, self.max_relative_offset_deg)
        except (ValueError, KeyError, TypeError) as exc:
            raise MotionRejected("Invalid generated motion plan") from exc
        speeds = self._speeds(profile)
        if any(speed > min(self.max_motion_speed_dps,
                           self.motion_axis_speed_caps_dps.get(axis, self.max_motion_speed_dps))
               for axis, speed in speeds.items()):
            raise MotionRejected("Generated motion exceeds locally verified speed")
        if set(profile["axes"]) - self.motion_bounds.keys():
            raise MotionRejected("Generated motion uses an unverified axis")
        for waypoint in profile["waypoints"]:
            for axis in profile["axes"]:
                target = start[axis] + waypoint.get(axis, 0.0)
                low, high = self.motion_bounds[axis]
                if not low <= target <= high:
                    raise MotionRejected("Generated motion exits the locally verified joint window")
        return profile

    async def execute(self, request: dict) -> dict:
        action = request["action"]
        capability = action["capability"]
        if capability == "arm.observe" and self.motion_bounds:
            return await self.observe()
        if capability not in ("arm.gesture", "arm.motion"):
            raise MotionRejected("Arm capability is not enabled")
        args = action["args"]
        state = await self._read(refresh=True)
        if not self._operational(state) or self._commanded_motion(state):
            raise RuntimeError("Arm is not idle and fault-free")
        start = self._angles(state)
        if any(abs(float(joint["targetDeg"]) - start[joint["id"]]) > 0.5 for joint in state["joints"] if joint["id"] <= 6):
            raise RuntimeError("Arm already has an unfinished target")
        width = float(state["gripper"]["widthMm"])
        if capability == "arm.gesture":
            name, repeat = args["name"], args["repeat"]
            if name not in self.gestures or type(repeat) is not int or not 1 <= repeat <= 3:
                raise MotionRejected("Gesture is not locally configured")
            profile = self.gestures[name]
        else:
            profile = self._motion_profile(args, start)
            name, repeat = args["intent"], 1
        steps = [{axis: start[axis] + offset.get(axis, 0.0) for axis in range(1, 7)}
                 for offset in profile["waypoints"]] * repeat
        speeds = self._speeds(profile)
        effective = {axis: min(speed, {1: 3.0, 2: 3.0, 3: 3.0, 4: 10.0, 5: 10.0, 6: 3.0}.get(axis, speed))
                     for axis, speed in speeds.items()}
        previous = start
        estimated = 0.0
        for target in steps:
            for axis in profile["axes"]:
                checked_target(axis, target[axis], hardware=True)
            estimated += max(abs(target[axis] - previous[axis]) / effective[axis] for axis in profile["axes"])
            previous = target
        # Gateway has a hard 10-second execution window. Reserve room for the
        # final feedback sample and a stop attempt; never start a long action.
        if estimated > 6.5:
            raise MotionRejected("Motion exceeds the Gateway short-action budget")
        await asyncio.get_running_loop().run_in_executor(None, self._preview, start, steps, width,
                                                          speeds if isinstance(profile["speed_dps"], dict) else None)
        deadline = time.monotonic() + 8.5
        state = await self._read(refresh=True)
        if (not self._operational(state) or self._commanded_motion(state)
                or any(abs(self._angles(state)[axis] - start[axis]) > 0.25 for axis in range(1, 7))
                or time.monotonic() + estimated + 0.5 > deadline):
            raise RuntimeError("Arm pose changed or execution window is too short")
        self.in_motion = True
        try:
            for target in steps:
                if isinstance(profile["speed_dps"], dict):
                    self.expected_targets = {}
                    for axis in profile["axes"]:
                        acknowledged = await self._json(f"/api/joints/{axis}/target", {
                            "degrees": target[axis], "speedDps": speeds[axis], "autoEnable": True,
                        })
                        self.state = acknowledged
                        self.last_poll = time.monotonic()
                        self.expected_targets[axis] = target[axis]
                else:
                    acknowledged = await self._json("/api/joints/targets", {
                        "targets": {str(axis): target[axis] for axis in profile["axes"]},
                        "speedDps": profile["speed_dps"], "autoEnable": True,
                    })
                    self.state = acknowledged
                    self.last_poll = time.monotonic()
                    self.expected_targets = {axis: target[axis] for axis in profile["axes"]}
                await self._wait_target(target, profile["axes"], start, deadline)
            return {"evidence": "controller_feedback",
                    "result": f"B601-DM confirmed {name} ({repeat} repetition(s)) at measured joint positions."}
        finally:
            self.in_motion = False
            self.expected_targets = {}

    async def stop(self, session: dict | None) -> bool:
        del session
        if self.http is None:
            return False
        try:
            await self._json("/api/motion/stop", {})
            previous: dict[int, float] | None = None
            stable = 0
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                state = await self._read(refresh=True)
                if not self._operational(state):
                    return False
                angles = self._angles(state)
                settled = all(abs(float(joint["targetDeg"]) - angles[joint["id"]]) <= 0.5
                              for joint in state["joints"] if joint["id"] <= 6)
                if previous is not None:
                    settled = settled and all(abs(angles[axis] - previous[axis]) <= 0.12 for axis in range(1, 7))
                stable = stable + 1 if settled else 0
                if stable >= 3:
                    return True
                previous = angles
                await asyncio.sleep(0.12)
        except Exception as exc:
            LOG.warning("Arm stop could not confirm stable controller feedback: %s", exc)
            return False
        LOG.warning("Arm stop timed out before three stable feedback samples")
        return False

    async def close(self) -> None:
        if self.poll_task is not None:
            self.poll_task.cancel()
            await asyncio.gather(self.poll_task, return_exceptions=True)
            self.poll_task = None
        if self.http is not None:
            await self.http.close()
            self.http = None
