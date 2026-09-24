"""Recorder tests with model feedback stub; no motor or serial connection."""
import copy
import unittest
from unittest.mock import patch

from tools.arm_gesture import build_profile
from tools.arm102_record import map_leader_sample


class SafeModel:
    def trajectory(self, start, target, width):
        return {"safe": True, "closest": {"clearanceMm": 2}}


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "name": "wave_short", "description": "小幅招手", "speed_dps": 8,
            "home": {str(axis): value for axis, value in
                     {1: 0, 2: -35, 3: -70, 4: -12, 5: 18, 6: 0}.items()},
            "gripper_width_mm": 60,
            "samples": [
                {str(axis): value for axis, value in
                 {1: 0, 2: -35, 3: -70, 4: -15, 5: 18, 6: 0}.items()},
                {str(axis): value for axis, value in
                 {1: 0, 2: -35, 3: -70, 4: -12, 5: 18, 6: 0}.items()},
            ],
        }

    def test_measured_offsets_are_stored_as_returning_profile(self):
        with patch("tools.arm_gesture.ModelSafety", return_value=SafeModel()):
            result = build_profile(self.record, clearance_mm=0.5)
        self.assertEqual(result["waypoints"], [{"J4": -3.0}, {"J4": 0.0}])

    def test_recording_must_end_at_home(self):
        record = copy.deepcopy(self.record)
        record["samples"][-1]["4"] = -11
        with self.assertRaisesRegex(ValueError, "return"):
            build_profile(record, clearance_mm=0.5)

    def test_model_boundary_blocks_recording(self):
        with patch("tools.arm_gesture.ModelSafety", return_value=SafeModel()):
            with self.assertRaisesRegex(ValueError, "safety boundary"):
                build_profile(self.record, clearance_mm=3)

    def test_arm102_relative_deltas_use_official_joint_directions(self):
        record = copy.deepcopy(self.record)
        record["leader_home"] = {str(axis): 0.0 for axis in range(7)}
        mapped = map_leader_sample(record, {0: 2, 1: 3, 2: -4, 3: -5,
                                            4: 6, 5: 7, 6: 0})
        self.assertEqual(mapped, {"1": -2.0, "2": -38.0, "3": -74.0,
                                  "4": -17.0, "5": 24.0, "6": -7.0})
