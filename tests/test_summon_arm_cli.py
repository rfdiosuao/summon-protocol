"""Offline contract checks for the agent-facing arm CLI."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import summon_arm as cli  # noqa: E402


class ArmCliTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> tuple[int, dict]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(arguments))
        return code, json.loads(out.getvalue() if code == 0 else err.getvalue())

    def test_describe_works_without_server(self) -> None:
        with mock.patch.object(cli, "request", side_effect=AssertionError("network called")):
            code, result = self.invoke("describe")
        self.assertEqual(code, 0)
        self.assertEqual(len(result["joints"]), 7)
        self.assertEqual(result["safety"]["trajectoryStepDeg"], 1)

    def test_enable_orders_wrist_before_load_axes_without_writing(self) -> None:
        with mock.patch.object(cli, "request", side_effect=AssertionError("network called")):
            code, result = self.invoke("enable", "J2", "J3")
        self.assertEqual(code, 0)
        self.assertEqual(result["order"], ["J4", "J3", "J2"])
        self.assertFalse(result["executed"])

    def test_model_blocks_known_self_collision(self) -> None:
        try:
            safety = cli.ModelSafety()
        except cli.SafetyUnavailable:
            self.skipTest("Pinocchio mesh engine is not installed")
        report = safety.evaluate({1: 0, 2: -175, 3: -5, 4: -100, 5: -85, 6: 0})
        self.assertFalse(report["safe"])
        self.assertEqual(report["reason"], "SELF_COLLISION")

    def test_preview_uses_offline_preset_without_network(self) -> None:
        try:
            cli.ModelSafety()
        except cli.SafetyUnavailable:
            self.skipTest("Pinocchio mesh engine is not installed")
        with mock.patch.object(cli, "request", side_effect=AssertionError("network called")):
            code, result = self.invoke("preview", "J3=-6", "--from-pose", "folded")
        self.assertEqual(code, 0)
        self.assertTrue(result["model"]["safe"])

    def test_model_unavailable_blocks_preview(self) -> None:
        with mock.patch.object(cli, "ModelSafety", side_effect=cli.SafetyUnavailable("missing model")):
            code, result = self.invoke("preview", "J3=-6", "--from-pose", "folded")
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "MODEL_CHECK_UNAVAILABLE")

    def test_fast_move_requires_confirmation_before_any_write(self) -> None:
        state = {"mode": "hardware", "connected": True, "fault": None, "sampleAgeMs": 10,
                 "gripper": {"widthMm": 60}, "joints": [
                     {"id": id, "actualDeg": -5 if id == 3 else -3 if id == 2 else 0,
                      "statusCode": 1, "fault": None, "moving": False} for id in range(1, 8)]}
        fake_safety = mock.Mock()
        fake_safety.trajectory.return_value = {"safe": True, "severity": "safe", "closest": {}, "sampledSteps": 2}
        with mock.patch.object(cli, "live_state", return_value=state), \
             mock.patch.object(cli, "ModelSafety", return_value=fake_safety), \
             mock.patch.object(cli, "request", side_effect=AssertionError("write attempted")):
            code, result = self.invoke("move", "J3=-6", "--speed", "11", "--execute")
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "HIGH_SPEED_CONFIRMATION_REQUIRED")

    def test_self_collision_blocks_live_move_before_write(self) -> None:
        try:
            cli.ModelSafety()
        except cli.SafetyUnavailable:
            self.skipTest("Pinocchio mesh engine is not installed")
        angles = {1: 0, 2: -175, 3: -5, 4: -100, 5: -85, 6: 0}
        state = {"mode": "hardware", "connected": True, "fault": None, "sampleAgeMs": 10,
                 "gripper": {"widthMm": 60}, "joints": [
                     {"id": id, "actualDeg": angles.get(id, 0), "statusCode": 1,
                      "fault": None, "moving": False} for id in range(1, 8)]}
        with mock.patch.object(cli, "live_state", return_value=state), \
             mock.patch.object(cli, "request", side_effect=AssertionError("write attempted")):
            code, result = self.invoke("move", "J3=-5", "--execute")
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "MODEL_COLLISION")


if __name__ == "__main__":
    unittest.main()
