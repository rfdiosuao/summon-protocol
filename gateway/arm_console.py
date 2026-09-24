"""B601-DM arm adapter for the existing SUMMON Gateway lease and receipt flow.

The local console owns the serial port. This adapter only talks to its loopback
API and exposes configured short gestures, never remote raw motor commands.
"""
from __future__ import annotations

import asyncio
import math
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp


ARM_CONSOLE = Path(__file__).resolve().parents[1] / "arm-console"
if str(ARM_CONSOLE) not in sys.path:
    sys.path.insert(0, str(ARM_CONSOLE))

from backend.arm import checked_target  # noqa: E402
from backend.safety import ModelSafety  # noqa: E402


GESTURE_NAMES = frozenset({"nod", "wave", "point_left", "point_center", "point_right"})


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
        gestures = config.get("gestures")
        if not isinstance(gestures, dict) or not gestures or set(gestures) - GESTURE_NAMES:
            raise ValueError("Configure at least one supported arm.gesture profile")
        self.gestures = {name: self._checked_profile(profile) for name, profile in gestures.items()}
        self.url = address.rstrip("/")
        self.http: aiohttp.ClientSession | None = None
        self.safety: ModelSafety | None = None
        self.state: dict | None = None
        self.last_poll = 0.0
        self.poll_task: asyncio.Task | None = None
        self.in_motion = False
        self.expected_targets: dict[int, float] = {}

    @staticmethod
    def _checked_profile(value: object) -> dict:
        if not isinstance(value, dict) or set(value) != {"speed_dps", "waypoints"}:
            raise ValueError("Gesture profile needs speed_dps and waypoints")
        speed = value["speed_dps"]
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.5 <= speed <= 10:
            raise ValueError("Gateway gestures must use 0.5..10 degrees/s")
        raw_steps = value["waypoints"]
        if not isinstance(raw_steps, list) or not 2 <= len(raw_steps) <= 4:
            raise ValueError("Gesture requires 2..4 relative waypoints")
        axes: set[int] = set()
        steps: list[dict[int, float]] = []
        for raw in raw_steps:
            if not isinstance(raw, dict) or not raw:
                raise ValueError("Each waypoint must contain J1..J6 offsets")
            step = {}
            for name, offset in raw.items():
                if not isinstance(name, str) or name not in {f"J{axis}" for axis in range(1, 7)}:
                    raise ValueError("Waypoint axes must be J1..J6")
                if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or abs(offset) > 10:
                    raise ValueError("Gesture offsets must be finite and within ±10 degrees")
                axis = int(name[1:])
                step[axis] = float(offset)
                axes.add(axis)
            steps.append(step)
        if not axes or all(all(step.get(axis, 0) == 0 for axis in axes) for step in steps):
            raise ValueError("Gesture must contain a nonzero motion")
        if any(steps[-1].get(axis, 0) != 0 for axis in axes):
            raise ValueError("Last waypoint must return every moved axis to its starting angle")
        return {"speed_dps": float(speed), "waypoints": steps, "axes": sorted(axes)}

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

    async def _poll(self) -> None:
        while True:
            try:
                await self._read()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state = None
                return
            await asyncio.sleep(0.15)

    async def open(self) -> None:
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False)
        try:
            self.safety = await asyncio.get_running_loop().run_in_executor(None, ModelSafety)
            state = await self._read(refresh=True)
            if not self._operational(state) or any(joint.get("moving") for joint in state["joints"][:6]):
                raise RuntimeError("B601-DM is not connected, idle and fault-free")
            self.poll_task = asyncio.create_task(self._poll())
        except BaseException:
            await self.close()
            raise

    def healthy(self) -> bool:
        state = self.state
        if (self.http is None or self.poll_task is None or self.poll_task.done() or state is None
                or time.monotonic() - self.last_poll > 1 or not self._operational(state)):
            return False
        if not self.in_motion and any(joint.get("moving") for joint in state["joints"][:6]):
            return False
        if self.in_motion and self.expected_targets:
            targets = {int(joint["id"]): float(joint["targetDeg"]) for joint in state["joints"] if int(joint["id"]) in self.expected_targets}
            if any(abs(targets.get(axis, math.inf) - value) > 0.5 for axis, value in self.expected_targets.items()):
                return False
        return True

    def _preview(self, start: dict[int, float], plan: list[dict[int, float]], width: float) -> None:
        assert self.safety is not None
        current = start
        for target in plan:
            report = self.safety.trajectory(current, target, width)
            # A remote, potentially unattended gesture needs clearance rather
            # than merely non-overlap; the CLI may display this as a warning.
            if not report["safe"] or report["closest"]["clearanceMm"] < 20:
                closest = report["closest"]
                raise RuntimeError(f"Arm gesture blocked by {closest['reason'] or 'LOW_CLEARANCE'}: {closest['links']}")
            current = target

    async def _wait_target(self, target: dict[int, float], axes: list[int], deadline: float) -> None:
        consecutive = 0
        while time.monotonic() < deadline:
            state = await self._read()
            if not self._operational(state):
                raise RuntimeError("Arm feedback became stale or faulted during motion")
            angles = self._angles(state)
            joints = {joint["id"]: joint for joint in state["joints"]}
            if any(abs(float(joints[axis]["targetDeg"]) - target[axis]) > 0.5 for axis in axes):
                raise RuntimeError("Arm target changed outside the Gateway")
            arrived = all(abs(angles[axis] - target[axis]) <= 0.4 and not joints[axis]["moving"] for axis in axes)
            consecutive = consecutive + 1 if arrived else 0
            if consecutive >= 2:
                return
            await asyncio.sleep(0.08)
        raise TimeoutError("Arm did not reach the configured gesture waypoint")

    async def execute(self, request: dict) -> dict:
        action = request["action"]
        if action["capability"] != "arm.gesture":
            raise ValueError("Only arm.gesture is supported")
        args = action["args"]
        name, repeat = args["name"], args["repeat"]
        if name not in self.gestures or type(repeat) is not int or not 1 <= repeat <= 3:
            raise ValueError("Gesture is not locally configured")
        profile = self.gestures[name]
        state = await self._read(refresh=True)
        if not self._operational(state) or any(joint.get("moving") for joint in state["joints"][:6]):
            raise RuntimeError("Arm is not idle and fault-free")
        start = self._angles(state)
        if any(abs(float(joint["targetDeg"]) - start[joint["id"]]) > 0.5 for joint in state["joints"] if joint["id"] <= 6):
            raise RuntimeError("Arm already has an unfinished target")
        width = float(state["gripper"]["widthMm"])
        steps = [{axis: start[axis] + offset.get(axis, 0.0) for axis in range(1, 7)}
                 for offset in profile["waypoints"]] * repeat
        previous = start
        estimated = 0.0
        for target in steps:
            for axis, value in target.items():
                checked_target(axis, value, hardware=True)
            estimated += max(abs(target[axis] - previous[axis]) for axis in profile["axes"]) / profile["speed_dps"]
            previous = target
        # Gateway has a hard 10-second execution window. Reserve room for the
        # final feedback sample and a stop attempt; never start a long action.
        if estimated > 5.0:
            raise ValueError("Gesture exceeds the Gateway short-action budget")
        deadline = time.monotonic() + 8.0
        await asyncio.get_running_loop().run_in_executor(None, self._preview, start, steps, width)
        state = await self._read(refresh=True)
        if (not self._operational(state) or any(joint.get("moving") for joint in state["joints"][:6])
                or any(abs(self._angles(state)[axis] - start[axis]) > 0.25 for axis in range(1, 7))
                or time.monotonic() + estimated + 0.5 > deadline):
            raise RuntimeError("Arm pose changed or execution window is too short")
        self.in_motion = True
        try:
            for target in steps:
                acknowledged = await self._json("/api/joints/targets", {
                    "targets": {str(axis): target[axis] for axis in profile["axes"]},
                    "speedDps": profile["speed_dps"], "autoEnable": True,
                })
                self.state = acknowledged
                self.last_poll = time.monotonic()
                self.expected_targets = {axis: target[axis] for axis in profile["axes"]}
                await self._wait_target(target, profile["axes"], deadline)
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
                settled = all(not joint.get("moving") and abs(float(joint["targetDeg"]) - angles[joint["id"]]) <= 0.5
                              for joint in state["joints"] if joint["id"] <= 6)
                if previous is not None:
                    settled = settled and all(abs(angles[axis] - previous[axis]) <= 0.12 for axis in range(1, 7))
                stable = stable + 1 if settled else 0
                if stable >= 3:
                    return True
                previous = angles
                await asyncio.sleep(0.12)
        except Exception:
            return False
        return False

    async def close(self) -> None:
        if self.poll_task is not None:
            self.poll_task.cancel()
            await asyncio.gather(self.poll_task, return_exceptions=True)
            self.poll_task = None
        if self.http is not None:
            await self.http.close()
            self.http = None
