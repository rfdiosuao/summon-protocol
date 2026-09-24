"""Offline URDF collision preview shared by agent-facing command line tools.

This is an advisory model boundary, not a certified physical safety system. It
uses the same collision_runtime meshes and non-adjacent-link rule as scene.ts.
"""
from __future__ import annotations

import math
import re
import shutil
import struct
import tempfile
from pathlib import Path
from typing import Any


MODEL_ROOT = Path(__file__).resolve().parents[1] / "public" / "model" / "DM"
URDF = MODEL_ROOT / "urdf" / "ReBot_Arm_DM.urdf"
FLOOR_Z_M = 0.006
WARNING_CLEARANCE_M = 0.020
MOVING_LINKS = {"link2", "link3", "link4", "link5", "link6", "end_link", "finger_left_link", "finger_right_link"}


class SafetyUnavailable(RuntimeError):
    """Model collision checking cannot run; motion must fail closed."""


def _link_name(geometry_name: str) -> str:
    return re.sub(r"_\d+$", "", geometry_name)


def _order(name: str) -> int:
    if name == "base_link":
        return 0
    if name.startswith("link") and name[4:].isdigit():
        return int(name[4:])
    if name == "end_link":
        return 7
    if name in ("finger_left_link", "finger_right_link"):
        return 8
    return 99


def _check_pair(left: str, right: str) -> bool:
    a, b = _order(left), _order(right)
    return left != right and 99 not in (a, b) and not (a == b == 8) and abs(a - b) > 1


def _stl_vertices(path: Path, np: Any) -> Any:
    content = path.read_bytes()
    if len(content) < 84:
        raise SafetyUnavailable(f"Invalid collision mesh: {path.name}")
    count = struct.unpack_from("<I", content, 80)[0]
    if 84 + count * 50 != len(content):
        raise SafetyUnavailable(f"Expected binary STL collision mesh: {path.name}")
    records = np.frombuffer(content, dtype=np.dtype([
        ("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")
    ]), count=count, offset=84)
    vertices = records["vertices"].reshape(-1, 3).astype(float)
    if not np.isfinite(vertices).all():
        raise SafetyUnavailable(f"Non-finite collision mesh: {path.name}")
    return vertices


class ModelSafety:
    def __init__(self) -> None:
        try:
            import numpy as np
            import pinocchio as pin
        except ImportError as exc:
            raise SafetyUnavailable("Offline mesh checking requires numpy and pinocchio") from exc
        self.np, self.pin = np, pin
        # urdfdom on Windows cannot open a Unicode workspace path. A temporary
        # ASCII copy contains only the URDF and its collision meshes; no visuals.
        with tempfile.TemporaryDirectory(prefix="summon-arm-model-") as temp:
            root = Path(temp)
            (root / "urdf").mkdir()
            destination = root / "meshes" / "collision_runtime"
            destination.mkdir(parents=True)
            shutil.copy2(URDF, root / "urdf" / URDF.name)
            for mesh in (MODEL_ROOT / "meshes" / "collision_runtime").iterdir():
                if mesh.is_file():
                    shutil.copy2(mesh, destination / mesh.name)
            path = str(root / "urdf" / URDF.name)
            try:
                self.model = pin.buildModelFromUrdf(path)
                self.geometry = pin.buildGeomFromUrdf(self.model, path, pin.GeometryType.COLLISION, package_dirs=[str(root / "urdf")])
                self.names = [_link_name(obj.name) for obj in self.geometry.geometryObjects]
                self.vertices = {
                    index: _stl_vertices(destination / Path(obj.meshPath).name, np)
                    for index, obj in enumerate(self.geometry.geometryObjects)
                    if self.names[index] in MOVING_LINKS
                }
            except SafetyUnavailable:
                raise
            except Exception as exc:
                raise SafetyUnavailable(f"Could not load URDF collision model: {exc}") from exc
        if len(self.names) < 9 or not self.vertices:
            raise SafetyUnavailable("URDF collision model is incomplete")
        self.geometry.removeAllCollisionPairs()
        for left in range(len(self.names)):
            for right in range(left + 1, len(self.names)):
                if _check_pair(self.names[left], self.names[right]):
                    self.geometry.addCollisionPair(pin.CollisionPair(left, right))
        if not self.geometry.collisionPairs:
            raise SafetyUnavailable("No non-adjacent model collision pairs")
        self.data = self.model.createData()
        self.geometry_data = self.geometry.createData()
        self.q_indices = {id: self.model.joints[self.model.getJointId(f"joint{id}")].idx_q for id in range(1, 7)}
        self.finger_indices = {
            name: self.model.joints[self.model.getJointId(name)].idx_q
            for name in ("finger_left", "finger_right")
        }

    def evaluate(self, joints: dict[int, float], gripper_mm: float = 60.0) -> dict[str, Any]:
        np, pin = self.np, self.pin
        q = np.zeros(self.model.nq)
        for id in range(1, 7):
            value = float(joints[id])
            if not math.isfinite(value):
                raise ValueError(f"J{id} is not finite")
            q[self.q_indices[id]] = math.radians(value)
        if not math.isfinite(gripper_mm) or not 0 <= gripper_mm <= 100:
            raise ValueError("Gripper width must be 0..100 mm")
        half_width = gripper_mm / 2000
        q[self.finger_indices["finger_left"]] = half_width
        q[self.finger_indices["finger_right"]] = -half_width
        try:
            pin.updateGeometryPlacements(self.model, self.data, self.geometry, self.geometry_data, q)
            pin.computeDistances(self.geometry, self.geometry_data)
            pin.computeCollisions(self.geometry, self.geometry_data, False)
        except Exception as exc:
            raise SafetyUnavailable(f"Collision calculation failed: {exc}") from exc
        closest_m = math.inf
        closest_links: list[str] = []
        overlap = False
        for index, pair in enumerate(self.geometry.collisionPairs):
            distance = float(self.geometry_data.distanceResults[index].min_distance)
            if not math.isfinite(distance):
                raise SafetyUnavailable("Collision distance was non-finite")
            collided = bool(self.geometry_data.collisionResults[index].isCollision())
            if collided or distance < closest_m:
                closest_m = min(distance, 0.0) if collided else distance
                closest_links = [self.names[pair.first], self.names[pair.second]]
                overlap = collided
            if collided:
                break
        min_floor = math.inf
        floor_link = ""
        for index, vertices in self.vertices.items():
            placement = self.geometry_data.oMg[index]
            rotation = np.asarray(placement.rotation)
            height = float(np.min(vertices[:, 0] * rotation[2, 0] + vertices[:, 1] * rotation[2, 1]
                                  + vertices[:, 2] * rotation[2, 2] + placement.translation[2]))
            if height < min_floor:
                min_floor, floor_link = height, self.names[index]
        floor_clearance = min_floor - FLOOR_Z_M
        if floor_clearance < closest_m and not overlap:
            closest_m, closest_links = floor_clearance, [floor_link]
        blocked = overlap or floor_clearance <= 0
        reason = "SELF_COLLISION" if overlap else "TABLE_CONTACT" if floor_clearance <= 0 else "NEAR_COLLISION" if closest_m < WARNING_CLEARANCE_M else None
        return {
            "safe": not blocked,
            "severity": "blocked" if blocked else "warning" if reason else "safe",
            "reason": reason,
            "clearanceMm": round(closest_m * 1000, 2),
            "links": closest_links,
        }

    def end_effector_pose(self, joints: dict[int, float]) -> dict[str, list[float]]:
        """Measured joint pose -> URDF end-link position and orientation."""
        q = self.np.zeros(self.model.nq)
        for axis in range(1, 7):
            angle = float(joints[axis])
            if not math.isfinite(angle):
                raise ValueError(f"J{axis} is not finite")
            q[self.q_indices[axis]] = math.radians(angle)
        self.pin.framesForwardKinematics(self.model, self.data, q)
        frame = self.model.getFrameId("end_link")
        transform = self.data.oMf[frame]
        return {
            "xyz_mm": [round(float(value) * 1000, 1) for value in transform.translation],
            "rpy_deg": [round(math.degrees(float(value)), 1)
                        for value in self.pin.rpy.matrixToRpy(transform.rotation)],
        }

    def end_effector_mm(self, joints: dict[int, float]) -> list[float]:
        """Measured joint pose -> URDF end-link origin in base coordinates."""
        return self.end_effector_pose(joints)["xyz_mm"]

    def trajectory(self, start: dict[int, float], target: dict[int, float], gripper_mm: float = 60.0) -> dict[str, Any]:
        # The controller advances each axis at the same configured angular
        # speed. Shorter-travel axes arrive first; straight-line joint-space
        # interpolation would miss those intermediate combinations.
        travel = {axis: target[axis] - start[axis] for axis in range(1, 7)}
        longest = max(abs(value) for value in travel.values())
        steps = max(1, math.ceil(longest))
        worst: dict[str, Any] | None = None
        for step in range(steps + 1):
            fraction = step / steps
            distance = longest * fraction
            pose = {axis: start[axis] + math.copysign(min(abs(travel[axis]), distance), travel[axis])
                    for axis in range(1, 7)}
            report = self.evaluate(pose, gripper_mm)
            report["atFraction"] = round(fraction, 5)
            if worst is None or report["clearanceMm"] < worst["clearanceMm"]:
                worst = report
            if report["severity"] == "blocked":
                break
        assert worst is not None
        return {"safe": worst["safe"], "severity": worst["severity"], "closest": worst,
                "sampledSteps": step + 1, "plannedSteps": steps + 1, "maxStepDeg": 1.0}
