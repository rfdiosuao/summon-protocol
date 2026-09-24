"""Six-axis teleoperation guards, without opening a serial port."""
import unittest

from tools.arm102_teleop import checked_step, fresh_leader, mapped_pose, ready_for_six_axes
from backend.safety import ModelSafety


class FakeModel:
    def evaluate(self, pose, width):
        return {"safe": pose[4] >= -10, "clearanceMm": 5 if pose[4] >= -10 else -1,
                "severity": "safe" if pose[4] >= -10 else "blocked",
                "reason": None if pose[4] >= -10 else "SELF_COLLISION", "links": []}

    def trajectory(self, start, target, width):
        return ModelSafety.trajectory(self, start, target, width)


class TeleopTests(unittest.TestCase):
    def test_stale_leader_axis_blocks_motion(self):
        class Sample:
            def __init__(self, reliable):
                self.reliable = reliable
                self.angle_deg = 0.0

        class Bus:
            def sync_monitor(self, ids):
                return {axis: Sample(axis != 2) for axis in ids}

        with self.assertRaisesRegex(RuntimeError, r"servo IDs \[2\]"):
            fresh_leader(Bus())

    def test_all_six_axes_use_official_leader_signs(self):
        home = {axis: -20 for axis in range(1, 7)}
        mapped = mapped_pose(home, [0] * 7, [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(mapped, {1: -21, 2: -22, 3: -17, 4: -16, 5: -15, 6: -26})

    def test_folded_start_blocks_six_axis_enable(self):
        state = {"joints": [{"id": i, "moving": False} for i in range(1, 7)]}
        with self.assertRaisesRegex(RuntimeError, "Follower is folded"):
            ready_for_six_axes(state, {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0})

    def test_mixed_axis_collision_blocks_target(self):
        current = {1: 0, 2: -35, 3: -70, 4: -9, 5: 0, 6: 0}
        desired = {**current, 4: -11}
        with self.assertRaisesRegex(RuntimeError, "Model boundary"):
            checked_step(FakeModel(), current, desired, 60, 0.5, 2)

    def test_model_samples_independent_joint_arrival(self):
        class Spy(FakeModel):
            def __init__(self):
                self.seen = []

            def evaluate(self, pose, width):
                self.seen.append(dict(pose))
                return super().evaluate(pose, width)

        model = Spy()
        start = {axis: 0 for axis in range(1, 7)}
        ModelSafety.trajectory(model, start, {**start, 1: 2, 2: 1}, 60)
        self.assertEqual([(pose[1], pose[2]) for pose in model.seen], [(0, 0), (1, 1), (2, 1)])


if __name__ == "__main__":
    unittest.main()
