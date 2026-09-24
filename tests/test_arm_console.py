import asyncio
import math
import sys
import time
import unittest
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "arm-console"))

from backend.arm import ArmError, CONTROL_TIMEOUT_MS, HardwareArm, JOINT_SPECS, SimArm, checked_speed, checked_target, gravity_feedforward_target, motion_gravity_bias, motor_fault_text  # noqa: E402


class ArmModelTests(unittest.TestCase):
    def test_official_joint_limits_and_hardware_margins(self):
        specs = {item.id: item for item in JOINT_SPECS}
        self.assertEqual((specs[2].lower_deg, specs[2].upper_deg), (-179.9, 0.0))
        self.assertEqual((specs[3].lower_deg, specs[3].upper_deg), (-179.9, 0.0))
        self.assertEqual((specs[4].lower_deg, specs[4].upper_deg), (-107.1, 90.0))
        self.assertEqual((specs[6].lower_deg, specs[6].upper_deg), (-179.9, 179.9))
        self.assertLess(specs[3].safe_upper_deg, specs[3].upper_deg)

    def test_hardware_target_cannot_touch_j3_fold_stop(self):
        with self.assertRaises(ArmError) as caught:
            checked_target(3, 0.0, hardware=True)
        self.assertEqual(caught.exception.code, "TARGET_OUT_OF_RANGE")
        self.assertEqual(checked_target(3, -5.0, hardware=True), -5.0)

    def test_high_speed_requires_per_action_confirmation(self):
        self.assertEqual(checked_speed(10.0), 10.0)
        with self.assertRaises(ArmError) as caught:
            checked_speed(10.1)
        self.assertEqual(caught.exception.code, "HIGH_SPEED_CONFIRMATION_REQUIRED")
        self.assertEqual(checked_speed(25.0, True), 25.0)
        with self.assertRaises(ArmError):
            checked_speed(25.1, True)

    def test_damiao_status_13_is_reported_as_communication_loss(self):
        self.assertIsNone(motor_fault_text(0))
        self.assertIsNone(motor_fault_text(1))
        self.assertEqual(motor_fault_text(13), "通讯丢失 (CODE 13)")

    def test_gravity_assist_requires_clearance_and_measured_load(self):
        arm = HardwareArm("TEST", 921600)
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.target_deg = -40.0
        elbow.commanded_deg = -20.0
        elbow.actual_deg = -12.0
        elbow.gravity_torque_nm = -7.0
        elbow.torque_nm = -5.0
        self.assertEqual(motion_gravity_bias(elbow, arm.joints), 0.0)
        elbow.actual_deg = -20.0
        self.assertLess(motion_gravity_bias(elbow, arm.joints), 0.0)
        elbow.torque_nm = 0.0
        self.assertEqual(motion_gravity_bias(elbow, arm.joints), 0.0)
        elbow.torque_nm = -11.0
        self.assertEqual(motion_gravity_bias(elbow, arm.joints), 0.0)

    def test_torque_feedforward_can_start_lift_from_fold_only_with_matching_load(self):
        arm = HardwareArm("TEST", 921600)
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.actual_deg = -0.7
        elbow.commanded_deg = -0.7
        elbow.target_deg = -17.0
        elbow.gravity_torque_nm = -7.16
        elbow.torque_nm = -2.4
        arm.joints[4].enabled = True
        arm.joints[4].status_code = 1
        self.assertAlmostEqual(gravity_feedforward_target(elbow, arm.joints), -2.5)
        arm.joints[4].enabled = False
        self.assertEqual(gravity_feedforward_target(elbow, arm.joints), 0.0)
        arm.joints[4].enabled = True
        elbow.torque_nm = 0.0
        self.assertEqual(gravity_feedforward_target(elbow, arm.joints), 0.0)
        elbow.torque_nm = -2.4
        elbow.target_deg = elbow.actual_deg
        self.assertEqual(gravity_feedforward_target(elbow, arm.joints), 0.0)

    def test_j4_support_feedforward_follows_elbow_lift_without_j2(self):
        arm = HardwareArm("TEST", 921600)
        wrist = arm.joints[4]
        wrist.enabled = True
        wrist.status_code = 1
        wrist.actual_deg = 0.5
        wrist.target_deg = 0.5
        wrist.gravity_torque_nm = -2.0
        wrist.torque_nm = -0.8
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -7.0
        self.assertEqual(gravity_feedforward_target(wrist, arm.joints), 0.0)
        elbow.actual_deg = -9.0
        self.assertAlmostEqual(gravity_feedforward_target(wrist, arm.joints), -0.8)


class SimulationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.arm = SimArm()
        await self.arm.start()

    async def asyncTearDown(self):
        await self.arm.close()

    async def test_joint_requires_enable_and_moves_at_bounded_speed(self):
        with self.assertRaises(ArmError) as caught:
            await self.arm.set_target(3, -90.0, 10.0)
        self.assertEqual(caught.exception.code, "MOTOR_NOT_ENABLED")

        start = self.arm.snapshot()["joints"][2]["actualDeg"]
        await self.arm.enable(3, True, -90.0, False)
        await self.arm.set_target(3, -90.0, 10.0)
        await asyncio.sleep(0.16)
        current = self.arm.snapshot()["joints"][2]["actualDeg"]
        self.assertLess(current, start)
        self.assertGreater(current, -90.0)

    async def test_gripper_and_disable_all(self):
        await self.arm.enable(7, True, 0.0, False)
        await self.arm.set_gripper(90.0, 12.0)
        await asyncio.sleep(0.12)
        moving = self.arm.snapshot()
        self.assertGreater(moving["gripper"]["widthMm"], 60.0)
        await self.arm.disable_all(False)
        stopped = self.arm.snapshot()
        self.assertFalse(any(item["enabled"] for item in stopped["joints"]))

    async def test_batch_targets_are_validated_before_any_target_changes(self):
        await self.arm.enable(2, True, -35.0, False)
        before = self.arm.snapshot()["joints"][1]["targetDeg"]
        with self.assertRaises(ArmError):
            await self.arm.set_targets({2: -50.0, 3: -70.0}, 3.0)
        self.assertEqual(self.arm.snapshot()["joints"][1]["targetDeg"], before)

        await self.arm.enable(3, True, -70.0, False)
        await self.arm.set_targets({2: -50.0, 3: -80.0}, 4.0)
        targets = {item["id"]: item["targetDeg"] for item in self.arm.snapshot()["joints"]}
        self.assertEqual(targets[2], -50.0)
        self.assertEqual(targets[3], -80.0)

    async def test_state_exposes_feedback_stress_and_gravity_capability(self):
        snapshot = self.arm.snapshot()
        self.assertGreater(snapshot["feedbackHz"], 0)
        self.assertIn("gravityCompensation", snapshot)
        self.assertIn("gravityTorqueNm", snapshot["joints"][0])
        self.assertIn("stressRatio", snapshot["joints"][0])

    async def test_confirmed_target_auto_enables_and_holds_after_arrival(self):
        await self.arm.set_target(4, -10.0, 25.0, True, True)
        started = self.arm.snapshot()["joints"][3]
        self.assertTrue(started["enabled"])
        await asyncio.sleep(0.5)
        finished = self.arm.snapshot()["joints"][3]
        self.assertAlmostEqual(finished["actualDeg"], -10.0, delta=0.25)
        self.assertTrue(finished["enabled"])
        self.assertEqual(finished["statusCode"], 1)


class HardwareCommandTests(unittest.TestCase):
    def setUp(self):
        class FakeMode(IntEnum):
            MIT = 1
            POS_VEL = 2

        fake_driver = SimpleNamespace(Mode=FakeMode)
        mocked_module = patch.dict(sys.modules, {"motorbridge": fake_driver})
        mocked_module.start()
        self.addCleanup(mocked_module.stop)

    def make_arm(self):
        arm = HardwareArm("TEST", 921600, experimental_gravity_feedforward=True)
        events = []

        class FakeMotor:
            def set_can_timeout_ms(self, value):
                events.append(("timeout", value))

            def ensure_mode(self, mode, timeout):
                events.append(("mode", int(mode), timeout))

            def send_pos_vel(self, position, speed):
                events.append(("hold", position, speed))

            def send_mit(self, position, velocity, kp, kd, torque):
                events.append(("mit", position, velocity, kp, kd, torque))

            def enable(self):
                events.append(("enable",))

            def disable(self):
                events.append(("disable",))

            def clear_error(self):
                events.append(("clear",))

        arm._motors = {joint_id: FakeMotor() for joint_id in range(1, 8)}
        arm._read_all = lambda: None
        def read(joint_id):
            events.append(("read", joint_id))
            enabled = ("enable",) in events
            return SimpleNamespace(status_code=1 if enabled else 0, pos=math.radians(-10.0))
        arm._read = read
        arm.joints[3].actual_deg = -10.0
        return arm, events

    def test_enable_configures_watchdog_and_holds_before_feedback(self):
        arm, events = self.make_arm()
        arm._gravity.available = True
        arm._cmd_enable(3, True, -10.0, False)
        self.assertIn(("timeout", CONTROL_TIMEOUT_MS), events)
        enable_index = events.index(("enable",))
        self.assertEqual(events[enable_index + 1][0], "mit")
        self.assertEqual(events[enable_index + 1][-1], 0.0)
        self.assertEqual(events[enable_index + 2], ("read", 4))
        self.assertEqual([event[0] for event in events].count("enable"), 2)
        self.assertIn(("read", 3), events)
        self.assertIn(3, arm._mit_axes)
        self.assertTrue(arm.joints[3].enabled)
        self.assertEqual(arm.joints[3].target_deg, -10.0)

    def test_mit_trajectory_ramps_real_torque_without_switching_enabled_mode(self):
        arm, events = self.make_arm()
        arm._gravity.available = True
        arm._mit_axes.add(3)
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -0.7
        elbow.commanded_deg = -0.7
        elbow.target_deg = -17.0
        elbow.torque_nm = -2.4
        elbow.gravity_torque_nm = -7.16
        elbow.speed_dps = 3.0
        arm.joints[4].enabled = True
        arm.joints[4].status_code = 1
        arm.joints[4].actual_deg = 0.0
        arm.joints[4].target_deg = 0.0
        arm.joints[4].commanded_deg = 0.0
        arm._last_tick = time.monotonic() - 0.1
        arm._tick()
        mit_commands = [event for event in events if event[0] == "mit"]
        self.assertTrue(mit_commands)
        self.assertLess(mit_commands[-1][-1], 0.0)
        self.assertGreater(mit_commands[-1][-1], -1.0)
        self.assertFalse(any(event[0] == "mode" for event in events))

    def test_j3_target_auto_enables_wrist_support(self):
        arm, events = self.make_arm()
        arm._gravity.available = True
        arm._cmd_target(3, -17.0, 1.0, auto_enable=True)
        self.assertTrue(arm.joints[4].enabled)
        self.assertTrue(arm.joints[3].enabled)
        self.assertIn(4, arm._mit_axes)
        self.assertIn(3, arm._mit_axes)
        self.assertEqual(arm.joints[4].target_deg, arm.joints[4].actual_deg)

    def test_manual_j3_enable_prepares_j4_and_target_requires_it(self):
        arm, events = self.make_arm()
        arm._gravity.available = True
        with self.assertRaises(ArmError) as error:
            arm._cmd_target(3, -17.0, 1.0, auto_enable=False)
        self.assertEqual(error.exception.code, "MOTOR_NOT_ENABLED")
        arm._cmd_enable(3, True, -10.0, False)
        self.assertTrue(arm.joints[4].enabled)
        self.assertTrue(arm.joints[3].enabled)
        self.assertEqual([event[0] for event in events].count("enable"), 2)
        self.assertIn(4, arm._mit_axes)
        self.assertIn(3, arm._mit_axes)

    def test_j3_target_rejects_disabled_j4_without_auto_enable(self):
        arm, events = self.make_arm()
        arm.joints[3].enabled = True
        arm.joints[3].status_code = 1
        with self.assertRaises(ArmError) as error:
            arm._cmd_target(3, -17.0, 1.0, auto_enable=False)
        self.assertEqual(error.exception.code, "WRIST_SUPPORT_REQUIRED")
        self.assertEqual(events, [])

    def test_j4_cannot_be_released_under_raised_arm_without_support(self):
        arm, events = self.make_arm()
        arm.joints[4].enabled = True
        arm.joints[4].status_code = 1
        arm.joints[3].enabled = True
        arm.joints[3].status_code = 1
        arm.joints[3].actual_deg = -30.0
        with self.assertRaises(ArmError) as error:
            arm._cmd_enable(4, False, 0.0, False)
        self.assertEqual(error.exception.code, "WRIST_SUPPORT_RELEASE_BLOCKED")
        self.assertNotIn(("disable",), events)

    def test_experimental_feedforward_is_off_by_default(self):
        arm = HardwareArm("TEST", 921600)
        self.assertFalse(arm.snapshot()["motionGravityAssist"]["enabled"])

    def test_default_hardware_mode_does_not_prepare_mit(self):
        arm, events = self.make_arm()
        arm.experimental_gravity_feedforward = False
        arm._gravity.available = True
        arm._cmd_enable(3, True, -10.0, False)
        self.assertTrue(arm.joints[4].enabled)
        self.assertEqual(arm._mit_axes, set())
        self.assertTrue(all(event[1] == 2 for event in events if event[0] == "mode"))
        with self.assertRaises(ArmError) as error:
            arm._cmd_gravity_compensation(True, True)
        self.assertEqual(error.exception.code, "GRAVITY_SUSPENDED")

    def test_mit_feedforward_releases_when_axis_outpaces_target_speed(self):
        arm, events = self.make_arm()
        arm._gravity.available = True
        arm._mit_axes.add(3)
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -5.0
        elbow.commanded_deg = -5.0
        elbow.target_deg = -17.0
        elbow.torque_nm = -2.0
        elbow.gravity_torque_nm = -7.0
        elbow.gravity_feedforward_nm = -1.0
        elbow.velocity_dps = -3.0
        elbow.speed_dps = 1.0
        arm.joints[4].enabled = True
        arm.joints[4].status_code = 1
        arm._last_tick = time.monotonic() - 0.1
        arm._tick()
        self.assertAlmostEqual(elbow.commanded_deg, -5.0)
        self.assertAlmostEqual(elbow.gravity_feedforward_nm, 0.0)
        self.assertTrue(any(event[0] == "mit" and event[-1] == 0.0 for event in events))

    def test_mit_overspeed_inhibits_future_feedforward_and_records_samples(self):
        arm, _ = self.make_arm()
        arm._mit_axes.add(3)
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -1.0
        elbow.commanded_deg = -1.0
        elbow.target_deg = -5.0
        elbow.velocity_dps = -7.7
        elbow.speed_dps = 0.5
        arm._last_tick = time.monotonic() - 0.02
        arm._tick()
        arm._last_tick = time.monotonic() - 0.02
        arm._tick()
        diagnostics = arm.snapshot()["faultDiagnostics"]
        self.assertIn("excessive velocity", elbow.fault)
        self.assertTrue(diagnostics["feedforwardInhibited"])
        self.assertFalse(arm.snapshot()["motionGravityAssist"]["enabled"])
        self.assertEqual(diagnostics["samples"][-1]["j3"]["velocityDps"], -7.7)

    def test_loaded_axis_is_not_mode_switched_and_stop_keeps_mit_hold(self):
        arm, events = self.make_arm()
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -30.0
        elbow.commanded_deg = -30.0
        elbow.target_deg = -35.0
        elbow.gravity_feedforward_nm = -3.0
        arm._mit_axes.add(3)
        arm._cmd_enable(3, True, -30.0, False)
        self.assertFalse(any(event[0] == "mode" for event in events))
        arm._cmd_stop()
        self.assertTrue(any(event[0] == "mit" and event[-1] == -3.0 for event in events))
        self.assertFalse(any(event[0] == "hold" for event in events))

    def test_folded_stall_releases_feedforward_gradually(self):
        arm, events = self.make_arm()
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -1.0
        elbow.commanded_deg = -1.0
        elbow.target_deg = -1.0
        elbow.gravity_feedforward_nm = -4.0
        elbow.fault = "motion stopped: no position progress (2.0 deg lag)"
        arm._mit_axes.add(3)
        arm._last_tick = time.monotonic() - 0.1
        arm._tick()
        self.assertGreater(elbow.gravity_feedforward_nm, -4.0)
        self.assertLess(elbow.gravity_feedforward_nm, 0.0)
        self.assertTrue(any(event[0] == "mit" for event in events))

    def test_fault_on_one_axis_blocks_other_axis_enable_and_motion(self):
        arm, events = self.make_arm()
        arm.joints[3].status_code = 13
        arm.joints[3].fault = motor_fault_text(13)
        with self.assertRaises(ArmError) as enable_error:
            arm._cmd_enable(4, True, 0.0, False)
        with self.assertRaises(ArmError) as target_error:
            arm._cmd_target(4, -5.0, 3.0, auto_enable=True)
        self.assertEqual(enable_error.exception.code, "ACTIVE_FAULT_LOCK")
        self.assertEqual(target_error.exception.code, "ACTIVE_FAULT_LOCK")
        self.assertEqual(events, [])

    def test_disable_restores_zero_idle_timeout(self):
        arm, events = self.make_arm()
        arm.joints[3].actual_deg = -0.38
        arm.joints[3].enabled = True
        arm.joints[3].status_code = 1
        arm._configured_timeout.add(3)
        arm._cmd_enable(3, False, -0.38, False)
        self.assertIn(("disable",), events)
        self.assertIn(("timeout", 0), events)
        self.assertNotIn(3, arm._configured_timeout)
        self.assertFalse(arm.joints[3].enabled)

    def test_trajectory_assist_stays_within_previewed_segment(self):
        arm, events = self.make_arm()
        arm._gravity.available = True
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -40.0
        elbow.commanded_deg = -40.0
        elbow.target_deg = -40.8
        elbow.gravity_torque_nm = -7.0
        elbow.torque_nm = -5.0
        elbow.speed_dps = 3.0
        arm._last_tick = time.monotonic() - 0.1
        arm._tick()
        positions = [math.degrees(event[1]) for event in events if event[0] == "hold"]
        self.assertTrue(positions)
        self.assertGreaterEqual(positions[-1], -40.8)
        self.assertLessEqual(positions[-1], -40.0)
        self.assertLess(elbow.gravity_assist_deg, 0.0)

    def test_following_error_freezes_all_axes(self):
        arm, events = self.make_arm()
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -40.0
        elbow.commanded_deg = -45.0
        elbow.target_deg = -70.0
        elbow.speed_dps = 3.0
        arm._following_since[3] = time.monotonic() - 0.7
        arm._following_anchor[3] = -40.0
        arm._last_tick = time.monotonic() - 0.02
        arm._tick()
        self.assertIn("no position progress", elbow.fault)
        self.assertEqual(elbow.target_deg, elbow.actual_deg)
        self.assertLessEqual(abs(math.degrees(next(event[1] for event in events if event[0] == "hold")) + 40.0), 2.0)

    def test_fault_hold_stays_at_captured_pose_when_feedback_drifts(self):
        arm, events = self.make_arm()
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -40.0
        elbow.commanded_deg = -45.0
        elbow.target_deg = -70.0
        arm._following_since[3] = time.monotonic() - 0.7
        arm._following_anchor[3] = -40.0
        arm._last_tick = time.monotonic() - 0.02
        arm._tick()
        self.assertIn("no position progress", elbow.fault)
        self.assertEqual(arm._fault_hold_angles[3], -40.0)
        elbow.actual_deg = -41.0
        arm._last_tick = time.monotonic() - 0.02
        arm._tick()
        self.assertEqual(elbow.commanded_deg, -40.0)
        self.assertEqual(elbow.target_deg, -40.0)
        self.assertAlmostEqual(math.degrees([event for event in events if event[0] == "hold"][-1][1]), -40.0)

    def test_position_loop_waits_for_progress_with_bounded_lead(self):
        arm, events = self.make_arm()
        arm._gravity.available = True
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -40.0
        elbow.commanded_deg = -40.0
        elbow.target_deg = -70.0
        elbow.speed_dps = 25.0
        elbow.gravity_torque_nm = -7.0
        elbow.torque_nm = -5.0
        for _ in range(5):
            arm._last_tick = time.monotonic() - 0.02
            arm._tick()
        self.assertIsNone(elbow.fault)
        self.assertLessEqual(abs(elbow.commanded_deg - elbow.actual_deg), 2.0)
        self.assertTrue(all(math.degrees(event[1]) >= -42.0 for event in events if event[0] == "hold"))

    def test_software_stop_clear_does_not_reset_enabled_motor(self):
        arm, events = self.make_arm()
        elbow = arm.joints[3]
        elbow.enabled = True
        elbow.status_code = 1
        elbow.actual_deg = -15.0
        elbow.fault = "motion stopped: no position progress (2.0 deg lag)"
        arm.fault = "J3 " + elbow.fault
        arm._cmd_clear_fault(3)
        self.assertIsNone(elbow.fault)
        self.assertTrue(elbow.enabled)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
