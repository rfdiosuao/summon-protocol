"""Model-aware simulation and MotorBridge drivers for the B601-DM console."""
from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import datetime as dt
import math
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path
from typing import Any


@dataclasses.dataclass(frozen=True)
class JointSpec:
    id: int
    model_joint: str | None
    label: str
    short_label: str
    description: str
    lower_deg: float
    upper_deg: float
    safe_lower_deg: float
    safe_upper_deg: float
    max_torque_nm: float

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "modelJoint": self.model_joint,
            "label": self.label,
            "shortLabel": self.short_label,
            "description": self.description,
            "lowerDeg": self.lower_deg,
            "upperDeg": self.upper_deg,
            "safeLowerDeg": self.safe_lower_deg,
            "safeUpperDeg": self.safe_upper_deg,
            "maxTorqueNm": self.max_torque_nm,
        }


JOINT_SPECS = (
    JointSpec(1, "joint1", "底座旋转", "BASE", "base yaw", -160.4, 160.4, -150.0, 150.0, 14.0),
    JointSpec(2, "joint2", "肩部俯仰", "SHOULDER", "shoulder lift", -179.9, 0.0, -175.0, -3.0, 14.0),
    JointSpec(3, "joint3", "肘部俯仰", "ELBOW", "elbow flex", -179.9, 0.0, -175.0, -5.0, 14.0),
    JointSpec(4, "joint4", "腕部俯仰", "WRIST A", "wrist flex", -107.1, 90.0, -100.0, 85.0, 8.0),
    JointSpec(5, "joint5", "腕部偏航", "WRIST B", "wrist yaw", -90.0, 90.0, -85.0, 85.0, 8.0),
    JointSpec(6, "joint6", "腕部旋转", "WRIST C", "wrist roll", -179.9, 179.9, -170.0, 170.0, 8.0),
    JointSpec(7, None, "夹爪电机", "GRIPPER", "gripper actuator", -180.0, 180.0, -180.0, 180.0, 8.0),
)
SPEC_BY_ID = {item.id: item for item in JOINT_SPECS}
CONTROL_TIMEOUT_MS = 2000
MOTION_ASSIST_MAX_BIAS_DEG = 0.35
MOTION_ASSIST_RAMP_DPS = 2.0
MOTION_ASSIST_TORQUE_FRACTION = 0.70
MAX_POSITION_LEAD_DEG = 2.0
POSITION_LEAD_DEG_BY_AXIS = {1: 4.0, 2: 4.0, 3: 4.0, 5: 4.0}
TRACKING_STALL_DWELL_S = 0.6
GRAVITY_FF_FRACTION = 0.40
GRAVITY_FF_RAMP_NM_S = 1.5
GRAVITY_FF_RELEASE_NM_S = 10.0
GRAVITY_FF_CAP_NM = {2: 2.5, 3: 2.5, 4: 0.8}

MOTOR_STATUS_LABELS = {
    0x0: "disabled",
    0x1: "enabled",
    0x8: "过压",
    0x9: "欠压",
    0xA: "过流",
    0xB: "MOS 过温",
    0xC: "转子过温",
    0xD: "通讯丢失",
    0xE: "过载",
}


def motor_fault_text(status_code: int) -> str | None:
    if status_code in (0, 1):
        return None
    return f"{MOTOR_STATUS_LABELS.get(status_code, '未知故障')} (CODE {status_code})"

POSES: dict[str, dict[str, Any]] = {
    "folded": {"joints": {"1": 0.0, "2": -3.0, "3": -5.0, "4": 0.0, "5": 0.0, "6": 0.0}, "gripperMm": 25.0},
    "ready": {"joints": {"1": 0.0, "2": -35.0, "3": -70.0, "4": -12.0, "5": 18.0, "6": 0.0}, "gripperMm": 60.0},
    "inspect": {"joints": {"1": 32.0, "2": -58.0, "3": -92.0, "4": -28.0, "5": 36.0, "6": 24.0}, "gripperMm": 84.0},
}


class ArmError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclasses.dataclass
class JointTelemetry:
    id: int
    actual_deg: float = 0.0
    target_deg: float = 0.0
    commanded_deg: float = 0.0
    velocity_dps: float = 0.0
    torque_nm: float = 0.0
    gravity_torque_nm: float = 0.0
    gravity_assist_deg: float = 0.0
    gravity_feedforward_nm: float = 0.0
    stress_ratio: float = 0.0
    mos_temp_c: float | None = None
    rotor_temp_c: float | None = None
    enabled: bool = False
    moving: bool = False
    status_code: int = 0
    fault: str | None = None
    speed_dps: float = 3.0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "actualDeg": round(self.actual_deg, 5),
            "targetDeg": round(self.target_deg, 5),
            "commandedDeg": round(self.commanded_deg, 5),
            "velocityDps": round(self.velocity_dps, 5),
            "torqueNm": round(self.torque_nm, 5),
            "gravityTorqueNm": round(self.gravity_torque_nm, 5),
            "gravityAssistDeg": round(self.gravity_assist_deg, 5),
            "gravityFeedforwardNm": round(self.gravity_feedforward_nm, 5),
            "stressRatio": round(self.stress_ratio, 5),
            "mosTempC": self.mos_temp_c,
            "rotorTempC": self.rotor_temp_c,
            "enabled": self.enabled,
            "moving": self.moving,
            "statusCode": self.status_code,
            "fault": self.fault,
        }


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def checked_speed(value: float, high_speed_confirmed: bool = False) -> float:
    if not math.isfinite(value) or not 0.2 <= value <= 25.0:
        raise ArmError("INVALID_SPEED", "Speed must be between 0.2 and 25 degrees per second", 400)
    if value > 10.0 and not high_speed_confirmed:
        raise ArmError("HIGH_SPEED_CONFIRMATION_REQUIRED", "Speed above 10 degrees per second requires confirmation", 400)
    return value


def checked_target(joint_id: int, degrees: float, hardware: bool) -> float:
    if joint_id not in SPEC_BY_ID or not math.isfinite(degrees):
        raise ArmError("INVALID_TARGET", "Invalid joint target", 400)
    spec = SPEC_BY_ID[joint_id]
    lower = spec.safe_lower_deg if hardware else spec.lower_deg
    upper = spec.safe_upper_deg if hardware else spec.upper_deg
    if not lower <= degrees <= upper:
        raise ArmError("TARGET_OUT_OF_RANGE", f"J{joint_id} target {degrees:.2f} is outside {lower:.2f}..{upper:.2f}")
    return degrees


class GravityModel:
    """Pinocchio gravity model loaded from the same official URDF as the UI."""

    def __init__(self) -> None:
        self.available = False
        self.error: str | None = None
        self._pin: Any = None
        self._np: Any = None
        self._model: Any = None
        try:
            import numpy as np
            import pinocchio as pin

            urdf = Path(__file__).resolve().parents[1] / "public" / "model" / "DM" / "urdf" / "ReBot_Arm_DM.urdf"
            # buildModelFromXML avoids urdfdom's Windows path encoding limitation.
            self._model = pin.buildModelFromXML(urdf.read_text(encoding="utf-8"), True)
            self._pin = pin
            self._np = np
            self.available = self._model.nq >= 6
        except BaseException as exc:
            self.error = str(exc)

    def compute(self, joints: dict[int, JointTelemetry], gripper_width_mm: float = 60.0) -> list[float]:
        if not self.available:
            return [0.0] * 6
        q = self._np.zeros(self._model.nq)
        for joint_id in range(1, 7):
            q[joint_id - 1] = math.radians(joints[joint_id].actual_deg)
        if self._model.nq > 6:
            q[6] = min(0.05, max(0.0, gripper_width_mm / 2000.0))
        gravity = self._pin.computeGeneralizedGravity(self._model, self._model.createData(), q)
        return [float(value) for value in gravity[:6]]


def update_load_estimates(joints: dict[int, JointTelemetry], gravity: GravityModel, gripper_width_mm: float = 60.0) -> None:
    torques = gravity.compute(joints, gripper_width_mm)
    for joint_id in range(1, 7):
        joint = joints[joint_id]
        joint.gravity_torque_nm = torques[joint_id - 1]
        joint.stress_ratio = min(2.0, abs(joint.torque_nm) / max(0.01, SPEC_BY_ID[joint_id].max_torque_nm))
    gripper = joints[7]
    gripper.gravity_torque_nm = 0.0
    gripper.stress_ratio = min(2.0, abs(gripper.torque_nm) / max(0.01, SPEC_BY_ID[7].max_torque_nm))


def motion_gravity_bias(joint: JointTelemetry, joints: dict[int, JointTelemetry]) -> float:
    """Small position-mode support bias; never substitute for calibrated torque FF."""
    if joint.id not in (2, 3, 4) or not joint.enabled or joint.fault:
        return 0.0
    if joints[3].actual_deg > -15.0 or (joint.id == 2 and joint.actual_deg > -8.0):
        return 0.0  # Folded/table-supported arm: do not press into the stop.
    if joint.id == 4 and not (joints[2].enabled and joints[2].actual_deg <= -8.0 and joints[3].enabled):
        return 0.0
    if abs(joint.target_deg - joint.commanded_deg) < 0.1:
        return 0.0
    gravity = joint.gravity_torque_nm
    measured = joint.torque_nm
    limit = SPEC_BY_ID[joint.id].max_torque_nm
    if (abs(gravity) < 1.0 or gravity * measured <= 0.0 or
            abs(measured) < 0.8 or abs(measured) > limit * MOTION_ASSIST_TORQUE_FRACTION):
        return 0.0
    # Feedback must agree with the model. A weak/zero reading likely means
    # external support; an excessive reading may mean contact or a jam.
    support = min(1.0, abs(measured) / abs(gravity))
    return math.copysign(MOTION_ASSIST_MAX_BIAS_DEG * support, gravity)


def gravity_feedforward_target(joint: JointTelemetry, joints: dict[int, JointTelemetry]) -> float:
    """Conservative MIT feedforward, armed only after torque sign agrees."""
    if joint.id not in GRAVITY_FF_CAP_NM or not joint.enabled or joint.fault:
        return 0.0
    if joint.id == 4 and not (joints[3].enabled and joints[3].status_code == 1 and joints[3].actual_deg <= -8.0):
        return 0.0
    if joint.id == 2 and (not joints[3].enabled or joints[3].status_code != 1 or joints[3].actual_deg > -5.0 or not joints[4].enabled or joints[4].status_code != 1):
        return 0.0
    if joint.id == 3 and not (joints[4].enabled and joints[4].status_code == 1):
        return 0.0
    away_from_fold = joint.target_deg < joint.actual_deg - 0.5
    lifted = joints[3].actual_deg <= -8.0 if joint.id == 4 else joint.actual_deg <= (-10.0 if joint.id == 3 else -8.0)
    if not (away_from_fold or lifted):
        return 0.0
    gravity = joint.gravity_torque_nm
    measured = joint.torque_nm
    if abs(gravity) < 0.8 or gravity * measured <= 0.0 or abs(measured) < 0.3:
        return 0.0
    if abs(measured) >= SPEC_BY_ID[joint.id].max_torque_nm * MOTION_ASSIST_TORQUE_FRACTION:
        return 0.0
    limit = GRAVITY_FF_CAP_NM[joint.id]
    return max(-limit, min(limit, gravity * GRAVITY_FF_FRACTION))


class ArmDriver(ABC):
    mode: str

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    def snapshot(self) -> dict[str, Any]: ...

    @abstractmethod
    async def enable(self, joint_id: int, enabled: bool, target_degrees: float, confirm_supported: bool) -> None: ...

    @abstractmethod
    async def clear_fault(self, joint_id: int) -> None: ...

    @abstractmethod
    async def set_target(self, joint_id: int, degrees: float, speed_dps: float, high_speed_confirmed: bool = False, auto_enable: bool = False) -> None: ...

    @abstractmethod
    async def set_targets(self, targets: dict[int, float], speed_dps: float, high_speed_confirmed: bool = False, auto_enable: bool = False) -> None: ...

    @abstractmethod
    async def set_gripper(self, width_mm: float, speed_mm_s: float) -> None: ...

    @abstractmethod
    async def stop_motion(self) -> None: ...

    @abstractmethod
    async def disable_all(self, confirm_supported: bool) -> None: ...

    @abstractmethod
    async def set_gravity_compensation(self, enabled: bool, confirmed: bool) -> None: ...

    async def set_pose(self, name: str, speed_dps: float, high_speed_confirmed: bool = False) -> None:
        del high_speed_confirmed
        raise ArmError("SIMULATION_ONLY", "Preset poses are available in simulation only")


class SimArm(ArmDriver):
    mode = "simulation"

    def __init__(self, initial: dict[int, float] | None = None):
        ready = {int(key): float(value) for key, value in POSES["ready"]["joints"].items()}
        ready.update(initial or {})
        self.joints = {joint_id: JointTelemetry(joint_id, ready.get(joint_id, 0.0), ready.get(joint_id, 0.0), ready.get(joint_id, 0.0)) for joint_id in range(1, 8)}
        self.gripper_width = float(POSES["ready"]["gripperMm"])
        self.gripper_target = self.gripper_width
        self.sequence = 0
        self.fault: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._last = time.monotonic()
        self._gravity = GravityModel()
        self._gravity_active = False
        self._feedback_hz = 50.0
        self._last_feedback = time.monotonic()

    async def start(self) -> None:
        self._last = time.monotonic()
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            now = time.monotonic()
            elapsed = min(0.1, max(0.0, now - self._last))
            self._last = now
            for joint in self.joints.values():
                previous = joint.actual_deg
                if joint.enabled:
                    delta = joint.target_deg - joint.actual_deg
                    step = math.copysign(min(abs(delta), joint.speed_dps * elapsed), delta) if delta else 0.0
                    joint.actual_deg += step
                    joint.commanded_deg = joint.actual_deg
                    joint.moving = abs(delta) > 0.03
                    joint.velocity_dps = (joint.actual_deg - previous) / elapsed if elapsed else 0.0
                    joint.torque_nm = 0.08 + min(2.4, abs(delta) * 0.018)
                    joint.mos_temp_c = 31.0 + abs(joint.torque_nm) * 0.22
                    joint.rotor_temp_c = 30.0 + abs(joint.torque_nm) * 0.3
                    joint.status_code = 1
                else:
                    joint.velocity_dps = 0.0
                    joint.torque_nm = 0.0
                    joint.moving = False
                    joint.status_code = 0
                    joint.mos_temp_c = 30.0
                    joint.rotor_temp_c = 30.0
            gripper_motor = self.joints[7]
            if gripper_motor.enabled:
                delta = self.gripper_target - self.gripper_width
                step = math.copysign(min(abs(delta), 20.0 * elapsed), delta) if delta else 0.0
                self.gripper_width += step
                gripper_motor.actual_deg = self.gripper_width * 0.9
                gripper_motor.target_deg = self.gripper_target * 0.9
                gripper_motor.commanded_deg = gripper_motor.actual_deg
            self.sequence += 1
            self._last_feedback = now
            await asyncio.sleep(0.02)

    def snapshot(self) -> dict[str, Any]:
        update_load_estimates(self.joints, self._gravity, self.gripper_width)
        return {
            "mode": self.mode,
            "connected": True,
            "sequence": self.sequence,
            "timestamp": now_iso(),
            "fault": self.fault,
            "feedbackHz": round(self._feedback_hz, 1),
            "feedbackLatencyMs": 0.0,
            "sampleAgeMs": round((time.monotonic() - self._last_feedback) * 1000, 1),
            "gravityCompensation": {
                "available": self._gravity.available,
                "active": self._gravity_active,
                "transitionProgress": 1.0 if self._gravity_active else 0.0,
                "message": self._gravity.error,
            },
            "motionGravityAssist": {
                "available": self._gravity.available,
                "enabled": False,
                "active": False,
                "maxBiasDeg": MOTION_ASSIST_MAX_BIAS_DEG,
                "maxFeedforwardNm": max(GRAVITY_FF_CAP_NM.values()),
                "preparedAxes": [],
            },
            "joints": [self.joints[index].public() for index in range(1, 8)],
            "gripper": {
                "widthMm": round(self.gripper_width, 4),
                "targetMm": round(self.gripper_target, 4),
                "calibrated": True,
                "motorDegrees": round(self.joints[7].actual_deg, 4),
            },
        }

    async def enable(self, joint_id: int, enabled: bool, target_degrees: float, confirm_supported: bool) -> None:
        del confirm_supported
        del target_degrees
        if joint_id not in self.joints:
            raise ArmError("NOT_FOUND", "Unknown joint", 404)
        joint = self.joints[joint_id]
        if enabled:
            joint.target_deg = joint.actual_deg
            joint.commanded_deg = joint.actual_deg
            joint.enabled = True
            joint.status_code = 1
        else:
            joint.target_deg = joint.actual_deg
            joint.enabled = False
            joint.status_code = 0
        self.sequence += 1

    async def clear_fault(self, joint_id: int) -> None:
        if joint_id not in self.joints:
            raise ArmError("NOT_FOUND", "Unknown joint", 404)
        joint = self.joints[joint_id]
        joint.enabled = False
        joint.status_code = 0
        joint.fault = None
        self.fault = None
        self.sequence += 1

    async def set_target(self, joint_id: int, degrees: float, speed_dps: float, high_speed_confirmed: bool = False, auto_enable: bool = False) -> None:
        if joint_id not in self.joints or joint_id == 7:
            raise ArmError("NOT_FOUND", "Unknown revolute joint", 404)
        joint = self.joints[joint_id]
        if not joint.enabled and not auto_enable:
            raise ArmError("MOTOR_NOT_ENABLED", f"J{joint_id} is not enabled")
        target = checked_target(joint_id, degrees, hardware=False)
        speed = checked_speed(speed_dps, high_speed_confirmed)
        if auto_enable:
            joint.enabled = True
            joint.status_code = 1
        joint.target_deg = target
        joint.speed_dps = speed
        self.sequence += 1

    async def set_targets(self, targets: dict[int, float], speed_dps: float, high_speed_confirmed: bool = False, auto_enable: bool = False) -> None:
        speed = checked_speed(speed_dps, high_speed_confirmed)
        checked: dict[int, float] = {}
        for joint_id, degrees in targets.items():
            if joint_id not in range(1, 7):
                raise ArmError("NOT_FOUND", "Unknown revolute joint", 404)
            if not self.joints[joint_id].enabled and not auto_enable:
                raise ArmError("MOTOR_NOT_ENABLED", f"J{joint_id} is not enabled")
            checked[joint_id] = checked_target(joint_id, degrees, hardware=False)
        for joint_id, target in checked.items():
            if auto_enable:
                self.joints[joint_id].enabled = True
                self.joints[joint_id].status_code = 1
            self.joints[joint_id].target_deg = target
            self.joints[joint_id].speed_dps = speed
        self.sequence += 1

    async def set_gripper(self, width_mm: float, speed_mm_s: float) -> None:
        if not math.isfinite(width_mm) or not 0.0 <= width_mm <= 100.0:
            raise ArmError("TARGET_OUT_OF_RANGE", "Gripper width must be 0..100 mm")
        if not math.isfinite(speed_mm_s) or not 1.0 <= speed_mm_s <= 50.0:
            raise ArmError("INVALID_SPEED", "Gripper speed must be 1..50 mm/s", 400)
        if not self.joints[7].enabled:
            raise ArmError("MOTOR_NOT_ENABLED", "J7 is not enabled")
        self.gripper_target = width_mm
        self.sequence += 1

    async def set_pose(self, name: str, speed_dps: float, high_speed_confirmed: bool = False) -> None:
        if name not in POSES:
            raise ArmError("NOT_FOUND", "Unknown preset pose", 404)
        speed = checked_speed(speed_dps, high_speed_confirmed)
        pose = POSES[name]
        for key, value in pose["joints"].items():
            joint = self.joints[int(key)]
            joint.enabled = True
            joint.status_code = 1
            joint.target_deg = float(value)
            joint.speed_dps = speed
        self.joints[7].enabled = True
        self.joints[7].status_code = 1
        self.gripper_target = float(pose["gripperMm"])
        self.sequence += 1

    async def stop_motion(self) -> None:
        for joint in self.joints.values():
            joint.target_deg = joint.actual_deg
            joint.commanded_deg = joint.actual_deg
            joint.moving = False
        self.gripper_target = self.gripper_width
        self.sequence += 1

    async def disable_all(self, confirm_supported: bool) -> None:
        del confirm_supported
        await self.stop_motion()
        for joint in self.joints.values():
            joint.enabled = False
            joint.status_code = 0
        self.sequence += 1

    async def set_gravity_compensation(self, enabled: bool, confirmed: bool) -> None:
        del confirmed
        self._gravity_active = enabled
        self.sequence += 1


class HardwareArm(ArmDriver):
    """All MotorBridge access stays on one dedicated owner thread."""

    mode = "hardware"

    def __init__(
        self,
        port: str,
        baud: int,
        gripper_closed_deg: float | None = None,
        gripper_open_deg: float | None = None,
        experimental_gravity_feedforward: bool = False,
    ):
        self.port = port
        self.baud = baud
        self.gripper_closed_deg = gripper_closed_deg
        self.gripper_open_deg = gripper_open_deg
        self.experimental_gravity_feedforward = experimental_gravity_feedforward
        self.joints = {joint_id: JointTelemetry(joint_id) for joint_id in range(1, 8)}
        self.sequence = 0
        self.fault: str | None = None
        self.connected = False
        self._state_lock = threading.RLock()
        self._commands: queue.Queue[tuple[str, tuple[Any, ...], concurrent.futures.Future[Any]]] = queue.Queue()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_error: BaseException | None = None
        self._controller: Any = None
        self._motors: dict[int, Any] = {}
        self._mit_axes: set[int] = set()
        self._configured_timeout: set[int] = set()
        self._last_tick = time.monotonic()
        self._torque_strikes = {joint_id: 0 for joint_id in range(1, 8)}
        self._velocity_strikes = {joint_id: 0 for joint_id in range(1, 8)}
        self._following_since: dict[int, float | None] = {joint_id: None for joint_id in range(1, 8)}
        self._following_anchor = {joint_id: 0.0 for joint_id in range(1, 8)}
        self._fault_hold_angles: dict[int, float] = {}
        self._motion_samples: deque[dict[str, Any]] = deque(maxlen=100)
        self._fault_trace: list[dict[str, Any]] = []
        self._feedforward_inhibited = False
        self._gravity = GravityModel()
        self._gravity_active = False
        self._gravity_started_at = 0.0
        self._gravity_anchor = {joint_id: 0.0 for joint_id in range(1, 7)}
        self._feedback_hz = 0.0
        self._feedback_latency_ms = 0.0
        self._last_feedback = time.monotonic()

    async def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="b601-motorbridge", daemon=False)
        self._thread.start()
        ready = await asyncio.to_thread(self._ready.wait, 8.0)
        if not ready:
            raise ArmError("SERIAL_TIMEOUT", f"Timed out opening {self.port}", 503)
        if self._start_error:
            message = str(self._start_error)
            code = "FEEDBACK_TIMEOUT" if "missing or mismatched feedback" in message else "SERIAL_BUSY"
            raise ArmError(code, f"Could not open {self.port}: {message}", 503)

    async def close(self) -> None:
        if not self._thread:
            return
        await self._request("disconnect")
        await asyncio.to_thread(self._thread.join, 4.0)
        self._thread = None

    async def force_close_preserving_hold(self) -> None:
        """Stop target progression and close transport without releasing active axes."""
        if not self._thread:
            return
        await self._request("force_disconnect")
        await asyncio.to_thread(self._thread.join, 4.0)
        self._thread = None

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            gripper = self._gripper_snapshot()
            return {
                "mode": self.mode,
                "connected": self.connected,
                "sequence": self.sequence,
                "timestamp": now_iso(),
                "fault": self.fault,
                "feedbackHz": round(self._feedback_hz, 1),
                "feedbackLatencyMs": round(self._feedback_latency_ms, 1),
                "sampleAgeMs": round((time.monotonic() - self._last_feedback) * 1000, 1),
                "gravityCompensation": {
                    "available": self._gravity.available and self.experimental_gravity_feedforward and not self._feedforward_inhibited,
                    "active": self._gravity_active,
                    "transitionProgress": min(1.0, max(0.0, (time.monotonic() - self._gravity_started_at) / 0.5)) if self._gravity_active else 0.0,
                    "message": self._gravity.error,
                },
                "motionGravityAssist": {
                    "available": self._gravity.available,
                    "enabled": self.experimental_gravity_feedforward and not self._feedforward_inhibited,
                    "active": any(abs(joint.gravity_feedforward_nm) > 0.1 or abs(joint.gravity_assist_deg) > 0.01 for joint in self.joints.values()),
                    "maxBiasDeg": MOTION_ASSIST_MAX_BIAS_DEG,
                    "maxFeedforwardNm": max(GRAVITY_FF_CAP_NM.values()),
                    "preparedAxes": sorted(self._mit_axes),
                },
                "faultDiagnostics": {
                    "feedforwardInhibited": self._feedforward_inhibited,
                    "samples": self._fault_trace,
                },
                "joints": [self.joints[index].public() for index in range(1, 8)],
                "gripper": gripper,
            }

    async def enable(self, joint_id: int, enabled: bool, target_degrees: float, confirm_supported: bool) -> None:
        await self._request("enable", joint_id, enabled, target_degrees, confirm_supported)

    async def clear_fault(self, joint_id: int) -> None:
        await self._request("clear_fault", joint_id)

    async def set_target(self, joint_id: int, degrees: float, speed_dps: float, high_speed_confirmed: bool = False, auto_enable: bool = False) -> None:
        await self._request("target", joint_id, degrees, speed_dps, high_speed_confirmed, auto_enable)

    async def set_targets(self, targets: dict[int, float], speed_dps: float, high_speed_confirmed: bool = False, auto_enable: bool = False) -> None:
        await self._request("targets", targets, speed_dps, high_speed_confirmed, auto_enable)

    async def set_gripper(self, width_mm: float, speed_mm_s: float) -> None:
        await self._request("gripper", width_mm, speed_mm_s)

    async def stop_motion(self) -> None:
        await self._request("stop")

    async def disable_all(self, confirm_supported: bool) -> None:
        await self._request("disable_all", confirm_supported)

    async def set_gravity_compensation(self, enabled: bool, confirmed: bool) -> None:
        await self._request("gravity_compensation", enabled, confirmed)

    async def refresh(self) -> None:
        await self._request("refresh")

    async def _request(self, name: str, *args: Any) -> Any:
        if not self._thread or not self._thread.is_alive():
            raise ArmError("HARDWARE_DISCONNECTED", "Hardware driver is not connected", 503)
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._commands.put((name, args, future))
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout=8.0)
        except asyncio.TimeoutError as exc:
            raise ArmError("SERIAL_TIMEOUT", "Motor command timed out", 504) from exc

    def _run(self) -> None:
        try:
            from motorbridge import Controller

            self._controller = Controller.from_dm_serial(self.port, self.baud)
            self._motors = {
                joint_id: self._controller.add_damiao_motor(
                    joint_id,
                    joint_id + 0x10,
                    "4340P" if joint_id <= 3 else "4310",
                )
                for joint_id in range(1, 8)
            }
            # Connecting only reads feedback. Motor configuration changes are
            # deferred until the operator explicitly enables an axis.
            self._read_all(timeout_s=0.3)
            for joint_id, joint in self.joints.items():
                if joint.status_code != 1:
                    continue
                mode = self._motors[joint_id].get_register_u32(10, 300)
                if mode == 1:
                    self._mit_axes.add(joint_id)
                    joint.gravity_feedforward_nm = gravity_feedforward_target(joint, self.joints)
                elif mode != 2:
                    raise RuntimeError(f"J{joint_id}: unsupported active control mode {mode}")
            with self._state_lock:
                # A motor can remain enabled after the previous transport closes.
                # Adopt every measured position before the first control tick so a
                # reconnect always resumes as a stationary hold at the live pose.
                for joint in self.joints.values():
                    joint.target_deg = joint.actual_deg
                    joint.commanded_deg = joint.actual_deg
                self.connected = True
                self.sequence += 1
        except BaseException as exc:
            self._start_error = exc
            self._ready.set()
            self._close_bus()
            return
        self._ready.set()
        try:
            while not self._stop.is_set():
                self._drain_commands()
                if self._stop.is_set():
                    break
                try:
                    self._tick()
                except BaseException as exc:
                    with self._state_lock:
                        self.fault = f"MotorBridge feedback error: {exc}"
                        self.sequence += 1
                    time.sleep(0.15)
        finally:
            with self._state_lock:
                self.connected = False
                self.sequence += 1
            self._close_bus()
            while not self._commands.empty():
                _, _, future = self._commands.get_nowait()
                if not future.done():
                    future.set_exception(ArmError("HARDWARE_DISCONNECTED", "Hardware driver stopped", 503))

    def _drain_commands(self) -> None:
        while True:
            try:
                name, args, future = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                result = getattr(self, f"_cmd_{name}")(*args)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)

    def _read(self, joint_id: int) -> Any:
        motor = self._motors[joint_id]
        motor.request_feedback()
        time.sleep(0.006)
        self._controller.poll_feedback_once()
        state = motor.get_state()
        if state is None or state.can_id != joint_id or state.arbitration_id != joint_id + 0x10:
            raise RuntimeError(f"J{joint_id}: missing or mismatched feedback")
        values = (state.pos, state.vel, state.torq, state.t_mos, state.t_rotor)
        if not all(math.isfinite(value) for value in values):
            raise RuntimeError(f"J{joint_id}: non-finite feedback")
        return state

    def _read_all(self, timeout_s: float = 0.08) -> None:
        started = time.monotonic()
        for motor in self._motors.values():
            motor.request_feedback()
        deadline = started + timeout_s
        while True:
            time.sleep(0.004)
            for _ in self._motors:
                self._controller.poll_feedback_once()
            samples = {joint_id: motor.get_state() for joint_id, motor in self._motors.items()}
            missing = [
                joint_id for joint_id, raw in samples.items()
                if raw is None or raw.can_id != joint_id or raw.arbitration_id != joint_id + 0x10
            ]
            if not missing:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"J{','.join(map(str, missing))}: missing or mismatched feedback")
            for joint_id in missing:
                self._motors[joint_id].request_feedback()
        with self._state_lock:
            for joint_id in range(1, 8):
                raw = samples[joint_id]
                values = (raw.pos, raw.vel, raw.torq, raw.t_mos, raw.t_rotor)
                if not all(math.isfinite(value) for value in values):
                    raise RuntimeError(f"J{joint_id}: non-finite feedback")
                joint = self.joints[joint_id]
                joint.actual_deg = math.degrees(raw.pos)
                joint.velocity_dps = math.degrees(raw.vel)
                joint.torque_nm = raw.torq
                joint.mos_temp_c = raw.t_mos
                joint.rotor_temp_c = raw.t_rotor
                joint.status_code = raw.status_code
                joint.enabled = raw.status_code == 1
                if not joint.enabled:
                    joint.target_deg = joint.actual_deg
                    joint.commanded_deg = joint.actual_deg
                    joint.gravity_assist_deg = 0.0
                    joint.gravity_feedforward_nm = 0.0
                # Idle feedback has a fixed -0.14/-0.42 deg/s velocity bias
                # across these motors. An unpowered joint cannot be in a
                # commanded move, and the bias must not trigger collision stop.
                joint.moving = joint.enabled and (
                    abs(joint.velocity_dps) > 0.6
                    or abs(joint.target_deg - joint.actual_deg) > 0.15
                )
                if raw.status_code not in (0, 1):
                    joint.fault = motor_fault_text(raw.status_code)
                # Keep faults latched even if a later frame says "disabled".
                # An operator must explicitly acknowledge the fault before a
                # fresh target can be accepted.
            update_load_estimates(self.joints, self._gravity, self._gripper_snapshot()["widthMm"])
            active_faults = self._active_faults()
            self.fault = "; ".join(active_faults) if active_faults else None
            finished = time.monotonic()
            interval = finished - self._last_feedback
            instantaneous = 1.0 / interval if interval > 0 else 0.0
            self._feedback_hz = instantaneous if self._feedback_hz <= 0 else self._feedback_hz * 0.82 + instantaneous * 0.18
            self._feedback_latency_ms = (finished - started) * 1000.0
            self._last_feedback = finished
            self.sequence += 1

    def _tick(self) -> None:
        now = time.monotonic()
        elapsed = min(0.3, max(0.01, now - self._last_tick))
        self._last_tick = now
        with self._state_lock:
            faulted = bool(self._active_faults())
            if self._gravity_active:
                progress = min(1.0, max(0.0, (now - self._gravity_started_at) / 0.5))
                for joint_id in range(1, 7):
                    joint = self.joints[joint_id]
                    stiff_kp = 120.0 if joint_id <= 3 else 18.0
                    stiff_kd = 8.0 if joint_id <= 3 else 2.0
                    kp = stiff_kp + (1.5 - stiff_kp) * progress
                    kd = stiff_kd + (1.0 - stiff_kd) * progress
                    target = self._gravity_anchor[joint_id] + (joint.actual_deg - self._gravity_anchor[joint_id]) * progress
                    torque = max(-SPEC_BY_ID[joint_id].max_torque_nm, min(SPEC_BY_ID[joint_id].max_torque_nm, joint.gravity_torque_nm))
                    self._motors[joint_id].send_mit(math.radians(target), 0.0, kp, kd, torque)
                    joint.commanded_deg = target
                    joint.target_deg = joint.actual_deg
                    joint.gravity_assist_deg = 0.0
            else:
                if faulted and not self._fault_hold_angles:
                    self._fault_hold_angles = {
                        joint_id: joint.actual_deg for joint_id, joint in self.joints.items() if joint.enabled
                    }
                for joint_id, joint in self.joints.items():
                    if not joint.enabled or joint.fault:
                        joint.gravity_assist_deg = 0.0
                        if joint.enabled and joint.fault and joint_id in self._mit_axes:
                            folded_support = (
                                joint_id == 3 and joint.actual_deg > -10.0
                                or joint_id == 2 and joint.actual_deg > -8.0
                                or joint_id == 4 and self.joints[3].actual_deg > -8.0
                            )
                            if folded_support:
                                change = GRAVITY_FF_RELEASE_NM_S * elapsed
                                joint.gravity_feedforward_nm -= math.copysign(
                                    min(abs(joint.gravity_feedforward_nm), change),
                                    joint.gravity_feedforward_nm,
                                )
                            self._send_hold(
                                joint_id, self._fault_hold_angles.get(joint_id, joint.commanded_deg), joint.speed_dps
                            )
                        continue
                    if faulted:
                        # A fault on any axis freezes every healthy enabled axis
                        # at its latest measured pose while preserving load hold.
                        joint.target_deg = self._fault_hold_angles.get(joint_id, joint.actual_deg)
                        joint.commanded_deg = joint.target_deg
                        joint.gravity_assist_deg = 0.0
                    delta = joint.target_deg - joint.commanded_deg
                    step = math.copysign(min(abs(delta), joint.speed_dps * elapsed), delta) if delta else 0.0
                    if (joint_id in self._mit_axes and delta * joint.velocity_dps > 0.0 and
                            abs(joint.velocity_dps) > max(1.2, joint.speed_dps * 1.2)):
                        step = 0.0
                    joint.commanded_deg += step
                    if joint_id <= 6:
                        # Do not wind up the position loop while a loaded axis
                        # is slow to start. Feedback must advance the window.
                        lead = POSITION_LEAD_DEG_BY_AXIS.get(joint_id, MAX_POSITION_LEAD_DEG)
                        joint.commanded_deg = max(
                            joint.actual_deg - lead,
                            min(joint.actual_deg + lead, joint.commanded_deg),
                        )
                    if joint_id in self._mit_axes:
                        # A stop freezes the pose and existing support torque;
                        # ramping a raised load to zero could make it drop.
                        desired_tau = joint.gravity_feedforward_nm if faulted else (
                            gravity_feedforward_target(joint, self.joints) if self._gravity.available else 0.0
                        )
                        if not faulted and delta * joint.velocity_dps > 0.0:
                            speed_floor = max(0.8, joint.speed_dps * 0.85)
                            speed_excess = max(0.0, abs(joint.velocity_dps) - speed_floor)
                            desired_tau *= max(0.0, 1.0 - speed_excess / max(0.8, joint.speed_dps * 0.8))
                        rate = GRAVITY_FF_RELEASE_NM_S if abs(desired_tau) < abs(joint.gravity_feedforward_nm) else GRAVITY_FF_RAMP_NM_S
                        torque_step = rate * elapsed
                        joint.gravity_feedforward_nm += max(
                            -torque_step,
                            min(torque_step, desired_tau - joint.gravity_feedforward_nm),
                        )
                        joint.gravity_assist_deg = 0.0
                        kp, kd = self._mit_gains(joint_id)
                        self._motors[joint_id].send_mit(
                            math.radians(joint.commanded_deg), 0.0, kp, kd,
                            joint.gravity_feedforward_nm,
                        )
                        continue
                    desired_bias = 0.0 if faulted or not self._gravity.available else motion_gravity_bias(joint, self.joints)
                    change = MOTION_ASSIST_RAMP_DPS * elapsed
                    joint.gravity_assist_deg += max(-change, min(change, desired_bias - joint.gravity_assist_deg))
                    # The sent pose remains on the already-previewed actual-to-target
                    # segment, even if the bias points beyond the final target.
                    lower = min(joint.actual_deg, joint.target_deg)
                    upper = max(joint.actual_deg, joint.target_deg)
                    output_deg = max(lower, min(upper, joint.commanded_deg + joint.gravity_assist_deg))
                    lead = POSITION_LEAD_DEG_BY_AXIS.get(joint_id, MAX_POSITION_LEAD_DEG)
                    output_deg = max(joint.actual_deg - lead, min(joint.actual_deg + lead, output_deg))
                    joint.gravity_assist_deg = output_deg - joint.commanded_deg
                    self._motors[joint_id].send_pos_vel(math.radians(output_deg), math.radians(joint.speed_dps))
        self._read_all()
        with self._state_lock:
            self._motion_samples.append({
                "timestamp": now_iso(),
                "j3": self._trace_joint(3),
                "j4": self._trace_joint(4),
            })
            active_faults: list[str] = []
            for joint_id, joint in self.joints.items():
                if joint.status_code not in (0, 1):
                    joint.fault = motor_fault_text(joint.status_code)
                hottest = max(joint.mos_temp_c or 0.0, joint.rotor_temp_c or 0.0)
                if hottest >= 75.0:
                    joint.fault = f"temperature {hottest:.0f} C"
                torque_limit = SPEC_BY_ID[joint_id].max_torque_nm
                if joint_id in self._mit_axes:
                    torque_limit *= 0.75
                if abs(joint.torque_nm) > torque_limit:
                    self._torque_strikes[joint_id] += 1
                else:
                    self._torque_strikes[joint_id] = 0
                if self._torque_strikes[joint_id] >= 2:
                    joint.fault = f"torque {joint.torque_nm:.2f} Nm"
                velocity = abs(joint.velocity_dps)
                if joint_id in self._mit_axes and velocity > max(8.0, joint.speed_dps * 2.0):
                    joint.fault = f"motion stopped: excessive velocity ({joint.velocity_dps:.1f} deg/s)"
                if joint_id in self._mit_axes and velocity > max(4.0, joint.speed_dps * 1.5):
                    self._velocity_strikes[joint_id] += 1
                else:
                    self._velocity_strikes[joint_id] = 0
                if self._velocity_strikes[joint_id] >= 2:
                    joint.fault = f"motion stopped: excessive velocity ({joint.velocity_dps:.1f} deg/s)"
                if joint.fault and "excessive velocity" in joint.fault and joint_id in self._mit_axes:
                    self._feedforward_inhibited = True
                lag = abs(joint.commanded_deg - joint.actual_deg)
                stalled_candidate = joint.enabled and abs(joint.target_deg - joint.actual_deg) > 0.5 and lag >= 1.5
                if stalled_candidate:
                    since = self._following_since[joint_id]
                    progress = (joint.actual_deg - self._following_anchor[joint_id]) * math.copysign(1.0, joint.target_deg - joint.actual_deg)
                    progress_needed = min(0.2, max(0.08, joint.speed_dps * 0.08))
                    if since is None or progress >= progress_needed:
                        self._following_since[joint_id] = now
                        self._following_anchor[joint_id] = joint.actual_deg
                    elif now - since >= TRACKING_STALL_DWELL_S:
                        joint.fault = f"motion stopped: no position progress ({lag:.1f} deg lag)"
                else:
                    self._following_since[joint_id] = None
                if joint.fault and joint.enabled:
                    if joint_id not in self._fault_hold_angles:
                        self._fault_hold_angles[joint_id] = joint.actual_deg
                    joint.target_deg = self._fault_hold_angles[joint_id]
                    joint.commanded_deg = joint.target_deg
                    self._send_hold(joint_id, joint.commanded_deg, joint.speed_dps)
                if joint.fault:
                    active_faults.append(f"J{joint_id} {joint.fault}")
            if active_faults:
                if not self._fault_trace:
                    self._fault_trace = list(self._motion_samples)[-30:]
                # A contact on one axis must stop the whole planned move.
                for joint_id, joint in self.joints.items():
                    if not joint.enabled:
                        continue
                    if joint_id not in self._fault_hold_angles:
                        self._fault_hold_angles[joint_id] = joint.actual_deg
                    joint.target_deg = self._fault_hold_angles[joint_id]
                    joint.commanded_deg = joint.target_deg
                    joint.gravity_assist_deg = 0.0
                    self._send_hold(joint_id, joint.commanded_deg, joint.speed_dps)
            else:
                self._fault_hold_angles.clear()
            self.fault = "; ".join(active_faults) if active_faults else None
            self.sequence += 1

    def _trace_joint(self, joint_id: int) -> dict[str, float | int | bool]:
        joint = self.joints[joint_id]
        return {
            "actualDeg": round(joint.actual_deg, 4),
            "commandedDeg": round(joint.commanded_deg, 4),
            "targetDeg": round(joint.target_deg, 4),
            "velocityDps": round(joint.velocity_dps, 4),
            "torqueNm": round(joint.torque_nm, 4),
            "feedforwardNm": round(joint.gravity_feedforward_nm, 4),
            "enabled": joint.enabled,
            "statusCode": joint.status_code,
        }

    def _active_faults(self) -> list[str]:
        return [
            f"J{joint_id} {joint.fault or motor_fault_text(joint.status_code)}"
            for joint_id, joint in self.joints.items()
            if joint.fault or joint.status_code not in (0, 1)
        ]

    def _require_fault_free(self) -> None:
        active = self._active_faults()
        if self.fault and not active:
            active.append(self.fault)
        if active:
            raise ArmError("ACTIVE_FAULT_LOCK", "Resolve active motor faults before enabling or moving: " + "; ".join(active))

    @staticmethod
    def _mit_gains(joint_id: int) -> tuple[float, float]:
        return (120.0, 5.0) if joint_id in (2, 3) else (18.0, 2.0)

    def _send_hold(self, joint_id: int, position_deg: float, speed_dps: float) -> None:
        if joint_id in self._mit_axes or self._gravity_active:
            kp, kd = self._mit_gains(joint_id)
            self._motors[joint_id].send_mit(
                math.radians(position_deg), 0.0, kp, kd,
                self.joints[joint_id].gravity_feedforward_nm,
            )
        else:
            self._motors[joint_id].send_pos_vel(math.radians(position_deg), math.radians(max(0.2, speed_dps)))

    def _keepalive_enabled(self) -> None:
        # Command handlers run on the same thread as _tick. In particular,
        # mode checks and register writes can otherwise starve already enabled
        # joints of their position hold frames.
        for joint_id, joint in self.joints.items():
            if joint.enabled and joint.status_code == 1 and not joint.fault:
                self._send_hold(joint_id, joint.commanded_deg, joint.speed_dps)

    def _cmd_enable(self, joint_id: int, enabled: bool, target_degrees: float, confirm_supported: bool) -> None:
        del target_degrees
        if joint_id not in self.joints:
            raise ArmError("NOT_FOUND", "Unknown joint", 404)
        self._read_all()
        joint = self.joints[joint_id]
        if enabled:
            self._require_fault_free()
            if joint.status_code == 1:
                return  # Never change control mode underneath a loaded axis.
            if joint_id == 2:
                elbow = self.joints[3]
                if joint.actual_deg > -3.0 and not (elbow.enabled and elbow.actual_deg <= -5.0):
                    raise ArmError("STARTUP_SEQUENCE", "Move and hold J3 clear of the folded stop before enabling J2")
            if joint_id == 6:
                shoulder = self.joints[2]
                elbow = self.joints[3]
                if not (shoulder.enabled and elbow.enabled and shoulder.actual_deg <= -3.0 and elbow.actual_deg <= -5.0):
                    raise ArmError("J6_INTERLOCK", "J2/J3 must be enabled and clear of the folded stops before J6")
            if joint_id in (2, 3) and not (self.joints[4].enabled and self.joints[4].status_code == 1):
                # Keep the wrist rigid before an upstream joint takes load.
                # J4 must be in its own hold mode before J2/J3 can move.
                self._cmd_enable(4, True, self.joints[4].actual_deg, False)
            from motorbridge import Mode

            motor = self._motors[joint_id]
            use_mit = (joint_id in GRAVITY_FF_CAP_NM and self._gravity.available
                       and self.experimental_gravity_feedforward and not self._feedforward_inhibited)
            self._keepalive_enabled()
            if joint_id not in self._configured_timeout:
                # The previous 15 ms setting was shorter than a single enable
                # handshake and produced status 0xD (communication loss).
                # MotorBridge's documented normal range is 100-2000 ms.
                motor.set_can_timeout_ms(CONTROL_TIMEOUT_MS)
                self._configured_timeout.add(joint_id)
            self._keepalive_enabled()
            try:
                motor.ensure_mode(Mode.MIT if use_mit else Mode.POS_VEL, 500)
            except BaseException:
                motor.set_can_timeout_ms(0)
                self._configured_timeout.discard(joint_id)
                raise
            if use_mit:
                self._mit_axes.add(joint_id)
            else:
                self._mit_axes.discard(joint_id)
            self._keepalive_enabled()
            pre_enable = self._read(joint_id)
            if pre_enable.status_code not in (0, 1):
                joint.status_code = pre_enable.status_code
                joint.fault = motor_fault_text(pre_enable.status_code)
                raise ArmError("HARDWARE_FAULT", f"J{joint_id} has {joint.fault}")
            hold_position = pre_enable.pos
            hold_speed = math.radians(3.0)
            if use_mit:
                kp, kd = self._mit_gains(joint_id)
                motor.send_mit(hold_position, 0.0, kp, kd, 0.0)
            else:
                motor.send_pos_vel(hold_position, hold_speed)
            motor.enable()
            # Send the hold target immediately after enable and throughout
            # verification. Never leave a watchdog-sized command gap here.
            deadline = time.monotonic() + 0.35
            raw = None
            while time.monotonic() < deadline:
                if use_mit:
                    motor.send_mit(hold_position, 0.0, kp, kd, 0.0)
                else:
                    motor.send_pos_vel(hold_position, hold_speed)
                raw = self._read(joint_id)
                if raw.status_code == 1:
                    break
                if raw.status_code not in (0, 1):
                    break
                time.sleep(0.005)
            if raw is None:
                raise ArmError("SERIAL_TIMEOUT", f"J{joint_id} enable verification timed out", 504)
            if raw.status_code != 1:
                raise ArmError("HARDWARE_FAULT", f"J{joint_id} enable returned {motor_fault_text(raw.status_code) or f'status {raw.status_code}'}")
            with self._state_lock:
                joint.enabled = True
                joint.status_code = 1
                joint.actual_deg = math.degrees(raw.pos)
                joint.commanded_deg = math.degrees(hold_position)
                joint.target_deg = joint.commanded_deg
                joint.speed_dps = 3.0
                joint.gravity_assist_deg = 0.0
                joint.gravity_feedforward_nm = 0.0
                joint.fault = None
                self.sequence += 1
            self._keepalive_enabled()
            return
        if joint.fault or joint.status_code not in (0, 1):
            raise ArmError("HARDWARE_FAULT", f"J{joint_id} has {joint.fault or motor_fault_text(joint.status_code)}")
        if joint_id in (2, 3) and joint.actual_deg < (-5.0 if joint_id == 2 else -8.0) and not confirm_supported:
            raise ArmError("LOAD_AXIS_RELEASE_BLOCKED", f"J{joint_id} is carrying the raised arm")
        if joint_id == 4 and not confirm_supported and any(
            self.joints[axis].enabled and (
                self.joints[axis].actual_deg < (-5.0 if axis == 2 else -8.0)
                or abs(self.joints[axis].target_deg - self.joints[axis].actual_deg) > 0.5
            ) for axis in (2, 3)
        ):
            raise ArmError("WRIST_SUPPORT_RELEASE_BLOCKED", "Support the raised or moving arm before disabling J4")
        self._keepalive_enabled()
        self._motors[joint_id].disable()
        time.sleep(0.08)
        raw = self._read(joint_id)
        if raw.status_code != 0:
            raise ArmError("HARDWARE_FAULT", f"J{joint_id} disable returned status {raw.status_code}")
        # The communications watchdog also faults a disabled motor when the
        # host disconnects. Restore the arm's original 0 ms idle setting only
        # after feedback confirms the axis is unpowered.
        self._motors[joint_id].set_can_timeout_ms(0)
        self._configured_timeout.discard(joint_id)
        self._mit_axes.discard(joint_id)
        with self._state_lock:
            joint.enabled = False
            joint.status_code = 0
            joint.target_deg = joint.actual_deg
            joint.commanded_deg = joint.actual_deg
            joint.gravity_assist_deg = 0.0
            joint.gravity_feedforward_nm = 0.0
            self.sequence += 1

    def _cmd_clear_fault(self, joint_id: int) -> None:
        if joint_id not in self.joints:
            raise ArmError("NOT_FOUND", "Unknown joint", 404)
        self._read_all()
        joint = self.joints[joint_id]
        if joint.status_code in (0, 1):
            if not joint.fault:
                return
            hottest = max(joint.mos_temp_c or 0.0, joint.rotor_temp_c or 0.0)
            if hottest >= 75.0 or abs(joint.torque_nm) > SPEC_BY_ID[joint_id].max_torque_nm:
                raise ArmError("UNSAFE_TO_CLEAR", f"J{joint_id} temperature or torque remains above the safety limit")
            # A trajectory stop is a software latch. Sending clear_error to an
            # enabled, healthy motor could interrupt its load-bearing hold.
            with self._state_lock:
                joint.target_deg = joint.actual_deg
                joint.commanded_deg = joint.actual_deg
                joint.gravity_assist_deg = 0.0
                joint.fault = None
                self._following_since[joint_id] = None
                self._torque_strikes[joint_id] = 0
                self._velocity_strikes[joint_id] = 0
                remaining = self._active_faults()
                self.fault = "; ".join(remaining) if remaining else None
                if not remaining:
                    self._fault_hold_angles.clear()
                self.sequence += 1
            return
        motor = self._motors[joint_id]
        motor.clear_error()
        deadline = time.monotonic() + 0.5
        raw = None
        while time.monotonic() < deadline:
            time.sleep(0.02)
            raw = self._read(joint_id)
            if raw.status_code in (0, 1):
                break
        if raw is None or raw.status_code not in (0, 1):
            code = raw.status_code if raw is not None else joint.status_code
            raise ArmError("HARDWARE_FAULT", f"J{joint_id} fault clear returned {motor_fault_text(code) or f'status {code}'}")
        if raw.status_code == 0:
            motor.set_can_timeout_ms(0)
            self._configured_timeout.discard(joint_id)
            self._mit_axes.discard(joint_id)
        with self._state_lock:
            joint.enabled = raw.status_code == 1
            joint.status_code = raw.status_code
            joint.actual_deg = math.degrees(raw.pos)
            joint.target_deg = joint.actual_deg
            joint.commanded_deg = joint.actual_deg
            joint.gravity_assist_deg = 0.0
            if raw.status_code == 0:
                joint.gravity_feedforward_nm = 0.0
            joint.fault = None
            self._following_since[joint_id] = None
            self._torque_strikes[joint_id] = 0
            self._velocity_strikes[joint_id] = 0
            remaining = self._active_faults()
            self.fault = "; ".join(remaining) if remaining else None
            if not remaining:
                self._fault_hold_angles.clear()
            self.sequence += 1

    def _cmd_target(self, joint_id: int, degrees: float, speed_dps: float, high_speed_confirmed: bool = False, auto_enable: bool = False) -> None:
        if joint_id not in range(1, 7):
            raise ArmError("NOT_FOUND", "Unknown revolute joint", 404)
        target = checked_target(joint_id, degrees, hardware=True)
        speed = checked_speed(speed_dps, high_speed_confirmed)
        with self._state_lock:
            if self._gravity_active:
                raise ArmError("GRAVITY_ACTIVE", "Exit gravity compensation before sending a position target")
            self._require_fault_free()
            joint = self.joints[joint_id]
            if (not joint.enabled or joint.status_code != 1) and not auto_enable:
                raise ArmError("MOTOR_NOT_ENABLED", f"J{joint_id} is not enabled")
            if joint.fault:
                raise ArmError("HARDWARE_FAULT", f"J{joint_id}: {joint.fault}")
        if joint_id == 2 and auto_enable and not self.joints[2].enabled:
            elbow = self.joints[3]
            if not (elbow.enabled and elbow.status_code == 1 and elbow.actual_deg <= -5.0):
                raise ArmError("STARTUP_SEQUENCE", "Move and hold J3 clear of the folded stop before enabling J2")
        if joint_id in (2, 3) and not (self.joints[4].enabled and self.joints[4].status_code == 1):
            if not auto_enable:
                raise ArmError("WRIST_SUPPORT_REQUIRED", "Enable and hold J4 before moving J2/J3 with gravity feedforward")
            self._cmd_enable(4, True, self.joints[4].actual_deg, False)
        if auto_enable and (not self.joints[joint_id].enabled or self.joints[joint_id].status_code != 1):
            spec = SPEC_BY_ID[joint_id]
            hold_target = min(spec.safe_upper_deg, max(spec.safe_lower_deg, self.joints[joint_id].actual_deg))
            self._cmd_enable(joint_id, True, hold_target, False)
        with self._state_lock:
            joint = self.joints[joint_id]
            joint.target_deg = target
            joint.speed_dps = speed
            self.sequence += 1

    def _cmd_targets(self, targets: dict[int, float], speed_dps: float, high_speed_confirmed: bool = False, auto_enable: bool = False) -> None:
        if not isinstance(targets, dict) or not targets:
            raise ArmError("INVALID_TARGET", "At least one joint target is required", 400)
        speed = checked_speed(speed_dps, high_speed_confirmed)
        checked: dict[int, float] = {}
        with self._state_lock:
            if self._gravity_active:
                raise ArmError("GRAVITY_ACTIVE", "Exit gravity compensation before sending position targets")
            self._require_fault_free()
            for raw_id, raw_target in targets.items():
                joint_id = int(raw_id)
                if joint_id not in range(1, 7):
                    raise ArmError("NOT_FOUND", "Unknown revolute joint", 404)
                joint = self.joints[joint_id]
                if (not joint.enabled or joint.status_code != 1) and not auto_enable:
                    raise ArmError("MOTOR_NOT_ENABLED", f"J{joint_id} is not enabled")
                if joint.fault:
                    raise ArmError("HARDWARE_FAULT", f"J{joint_id}: {joint.fault}")
                checked[joint_id] = checked_target(joint_id, float(raw_target), hardware=True)
            if auto_enable:
                if 2 in checked and (not self.joints[2].enabled or self.joints[2].status_code != 1):
                    elbow = self.joints[3]
                    if not (elbow.enabled and elbow.status_code == 1 and elbow.actual_deg <= -5.0):
                        raise ArmError("STARTUP_SEQUENCE", "Auto-enable J3 and move it below -5 degrees before confirming J2")
                if 6 in checked and (not self.joints[6].enabled or self.joints[6].status_code != 1):
                    shoulder, elbow = self.joints[2], self.joints[3]
                    if not (shoulder.enabled and elbow.enabled and shoulder.actual_deg <= -3.0 and elbow.actual_deg <= -5.0):
                        raise ArmError("J6_INTERLOCK", "J2/J3 must already be enabled and clear before auto-enabling J6")
        if auto_enable:
            if any(joint_id in checked for joint_id in (2, 3)) and not (self.joints[4].enabled and self.joints[4].status_code == 1):
                self._cmd_enable(4, True, self.joints[4].actual_deg, False)
            for joint_id in (4, 3, 2, 1, 5, 6):
                if joint_id not in checked:
                    continue
                joint = self.joints[joint_id]
                if joint.enabled and joint.status_code == 1:
                    continue
                spec = SPEC_BY_ID[joint_id]
                hold_target = min(spec.safe_upper_deg, max(spec.safe_lower_deg, joint.actual_deg))
                self._cmd_enable(joint_id, True, hold_target, False)
                self._keepalive_enabled()
        elif any(joint_id in checked for joint_id in (2, 3)) and not (self.joints[4].enabled and self.joints[4].status_code == 1):
            raise ArmError("WRIST_SUPPORT_REQUIRED", "Enable and hold J4 before moving J2/J3 with gravity feedforward")
        with self._state_lock:
            for joint_id, target in checked.items():
                self.joints[joint_id].target_deg = target
                self.joints[joint_id].speed_dps = speed
            self.sequence += 1

    def _cmd_gravity_compensation(self, enabled: bool, confirmed: bool) -> None:
        if enabled == self._gravity_active:
            return
        if enabled:
            self._require_fault_free()
            if not self.experimental_gravity_feedforward or self._feedforward_inhibited:
                raise ArmError("GRAVITY_SUSPENDED", "MIT gravity control is suspended pending calibration")
            if not confirmed:
                raise ArmError("CONFIRMATION_REQUIRED", "Gravity compensation mode change requires confirmation")
            if not self._gravity.available:
                raise ArmError("GRAVITY_UNAVAILABLE", self._gravity.error or "Pinocchio gravity model is unavailable", 503)
            unavailable = [joint_id for joint_id in range(1, 7) if not self.joints[joint_id].enabled or self.joints[joint_id].status_code != 1]
            if unavailable:
                raise ArmError("GRAVITY_REQUIRES_ALL_AXES", f"Enable J1-J6 before gravity compensation: {unavailable}")
            if any(self.joints[joint_id].fault for joint_id in range(1, 7)):
                raise ArmError("HARDWARE_FAULT", "Clear all arm-axis faults before gravity compensation")
            from motorbridge import Mode

            self._cmd_stop()
            self._read_all()
            for joint_id in range(1, 7):
                joint = self.joints[joint_id]
                self._gravity_anchor[joint_id] = joint.actual_deg
                joint.gravity_feedforward_nm = 0.0
                stiff_kp = 120.0 if joint_id <= 3 else 18.0
                stiff_kd = 8.0 if joint_id <= 3 else 2.0
                self._motors[joint_id].ensure_mode(Mode.MIT, 1000)
                self._motors[joint_id].send_mit(math.radians(joint.actual_deg), 0.0, stiff_kp, stiff_kd, joint.gravity_torque_nm)
            self._gravity_started_at = time.monotonic()
            self._gravity_active = True
            self.sequence += 1
            return

        from motorbridge import Mode

        self._read_all()
        for joint_id in range(1, 7):
            joint = self.joints[joint_id]
            stiff_kp = 120.0 if joint_id <= 3 else 18.0
            stiff_kd = 8.0 if joint_id <= 3 else 2.0
            self._motors[joint_id].send_mit(math.radians(joint.actual_deg), 0.0, stiff_kp, stiff_kd, joint.gravity_torque_nm)
        time.sleep(0.06)
        for joint_id in range(1, 7):
            joint = self.joints[joint_id]
            self._motors[joint_id].ensure_mode(Mode.POS_VEL, 1000)
            self._motors[joint_id].send_pos_vel(math.radians(joint.actual_deg), math.radians(max(0.2, joint.speed_dps)))
            joint.target_deg = joint.actual_deg
            joint.commanded_deg = joint.actual_deg
            joint.gravity_feedforward_nm = 0.0
        self._mit_axes.clear()
        self._gravity_active = False
        self.sequence += 1

    def _cmd_gripper(self, width_mm: float, speed_mm_s: float) -> None:
        if self.gripper_closed_deg is None or self.gripper_open_deg is None:
            raise ArmError("GRIPPER_NOT_CALIBRATED", "Gripper motor mapping is not configured")
        if not math.isfinite(width_mm) or not 0.0 <= width_mm <= 100.0:
            raise ArmError("TARGET_OUT_OF_RANGE", "Gripper width must be 0..100 mm")
        if not math.isfinite(speed_mm_s) or not 1.0 <= speed_mm_s <= 50.0:
            raise ArmError("INVALID_SPEED", "Gripper speed must be 1..50 mm/s", 400)
        motor_target = self.gripper_closed_deg + (self.gripper_open_deg - self.gripper_closed_deg) * width_mm / 100.0
        with self._state_lock:
            self._require_fault_free()
            joint = self.joints[7]
            if not joint.enabled:
                raise ArmError("MOTOR_NOT_ENABLED", "J7 is not enabled")
            joint.target_deg = motor_target
            joint.speed_dps = max(0.2, abs(self.gripper_open_deg - self.gripper_closed_deg) * speed_mm_s / 100.0)
            self.sequence += 1

    def _cmd_stop(self) -> None:
        if self._gravity_active:
            self._cmd_gravity_compensation(False, True)
        with self._state_lock:
            for joint_id, joint in self.joints.items():
                if joint.enabled:
                    joint.target_deg = joint.actual_deg
                    joint.commanded_deg = joint.actual_deg
                    joint.gravity_assist_deg = 0.0
                    if joint.fault:
                        self._fault_hold_angles[joint_id] = joint.actual_deg
                    self._send_hold(joint_id, joint.actual_deg, joint.speed_dps)
            self.sequence += 1

    def _cmd_disable_all(self, confirm_supported: bool) -> None:
        raised = self.joints[2].actual_deg < -5.0 or self.joints[3].actual_deg < -8.0
        if raised and not confirm_supported:
            raise ArmError("LOAD_AXIS_RELEASE_BLOCKED", "The arm is raised and support has not been confirmed")
        self._cmd_stop()
        for joint_id, motor in self._motors.items():
            if self.joints[joint_id].enabled or self.joints[joint_id].status_code != 0:
                motor.disable()
        time.sleep(0.12)
        self._read_all()
        remaining = [joint.id for joint in self.joints.values() if joint.status_code != 0]
        if remaining:
            raise ArmError("HARDWARE_FAULT", f"Disable not verified for joints {remaining}")
        for joint_id in range(1, 8):
            if joint_id in self._configured_timeout:
                self._motors[joint_id].set_can_timeout_ms(0)
                self._configured_timeout.discard(joint_id)
            self.joints[joint_id].gravity_feedforward_nm = 0.0
        self._mit_axes.clear()
        self._fault_hold_angles.clear()

    def _cmd_refresh(self) -> None:
        self._read_all()

    def _cmd_disconnect(self) -> None:
        active = [joint.id for joint in self.joints.values() if joint.enabled or joint.status_code == 1]
        if active:
            raise ArmError("MOTORS_STILL_ENABLED", f"Disable joints before disconnecting: {active}")
        self._stop.set()

    def _cmd_force_disconnect(self) -> None:
        self._cmd_stop()
        self._stop.set()

    def _gripper_snapshot(self) -> dict[str, Any]:
        joint = self.joints[7]
        calibrated = self.gripper_closed_deg is not None and self.gripper_open_deg is not None and self.gripper_open_deg != self.gripper_closed_deg
        if calibrated:
            assert self.gripper_closed_deg is not None and self.gripper_open_deg is not None
            fraction = (joint.actual_deg - self.gripper_closed_deg) / (self.gripper_open_deg - self.gripper_closed_deg)
            target_fraction = (joint.target_deg - self.gripper_closed_deg) / (self.gripper_open_deg - self.gripper_closed_deg)
            width = min(100.0, max(0.0, fraction * 100.0))
            target = min(100.0, max(0.0, target_fraction * 100.0))
        else:
            width = target = 60.0
        return {
            "widthMm": round(width, 4),
            "targetMm": round(target, 4),
            "calibrated": calibrated,
            "motorDegrees": round(joint.actual_deg, 4),
        }

    def _close_bus(self) -> None:
        if self._controller is None:
            return
        try:
            self._controller.close_bus()
            for motor in self._motors.values():
                motor.close()
            self._controller.close()
        except Exception:
            pass


class ArmService:
    def __init__(
        self,
        hardware_allowed: bool = False,
        port: str = "COM6",
        baud: int = 921600,
        gripper_closed_deg: float | None = None,
        gripper_open_deg: float | None = None,
        experimental_gravity_feedforward: bool = False,
    ):
        self.hardware_allowed = hardware_allowed
        self.port = port
        self.baud = baud
        self.gripper_closed_deg = gripper_closed_deg
        self.gripper_open_deg = gripper_open_deg
        self.experimental_gravity_feedforward = experimental_gravity_feedforward
        self.driver: ArmDriver = SimArm()
        self._switch_lock = asyncio.Lock()

    async def start(self) -> None:
        await self.driver.start()

    async def close(self) -> None:
        try:
            await self.driver.close()
        except ArmError as exc:
            if exc.code != "MOTORS_STILL_ENABLED" or not isinstance(self.driver, HardwareArm):
                raise
            await self.driver.force_close_preserving_hold()

    def config(self) -> dict[str, Any]:
        return {
            "jointSpecs": [item.public() for item in JOINT_SPECS],
            "poses": POSES,
            "defaultSpeedDps": 3.0,
            "modelUrl": "/model/DM/urdf/ReBot_Arm_DM.urdf",
        }

    def snapshot(self) -> dict[str, Any]:
        value = self.driver.snapshot()
        value.update({"hardwareAllowed": self.hardware_allowed, "port": self.port, "baud": self.baud})
        return value

    async def connect_hardware(self, port: str | None = None) -> None:
        if not self.hardware_allowed:
            raise ArmError("HARDWARE_NOT_ALLOWED", "Start with --allow-hardware to permit serial writes", 403)
        async with self._switch_lock:
            if self.driver.mode == "hardware":
                return
            selected_port = port or self.port
            hardware = HardwareArm(
                selected_port, self.baud, self.gripper_closed_deg, self.gripper_open_deg,
                self.experimental_gravity_feedforward,
            )
            try:
                await hardware.start()
            except BaseException:
                try:
                    await hardware.close()
                except BaseException:
                    pass
                raise
            await self.driver.close()
            self.driver = hardware
            self.port = selected_port

    async def disconnect_hardware(self) -> None:
        async with self._switch_lock:
            if self.driver.mode != "hardware":
                return
            snapshot = self.driver.snapshot()
            initial = {joint["id"]: joint["actualDeg"] for joint in snapshot["joints"] if joint["id"] <= 6}
            await self.driver.close()
            simulation = SimArm(initial)
            await simulation.start()
            self.driver = simulation

    async def refresh(self) -> None:
        if isinstance(self.driver, HardwareArm):
            await self.driver.refresh()
