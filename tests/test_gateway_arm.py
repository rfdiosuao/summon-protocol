"""Offline Gateway-to-arm-console contract tests; no serial hardware is opened."""
import asyncio
import copy
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.arm_console import ArmConsoleAdapter, MotionRejected


def profile():
    return {
        "url": "http://127.0.0.1:8870",
        "physical_estop_confirmed": True,
        "motion_profiles_verified": True,
        "gestures": {"wave": {"speed_dps": 2, "waypoints": [{"J4": 1}, {"J4": 0}]}}
    }


class FakeModel:
    blocked = False
    clearance = 50

    def trajectory(self, start, target, width):
        assert width == 60
        if self.blocked:
            return {"safe": False, "closest": {"reason": "SELF_COLLISION", "links": ["link2", "link4"], "clearanceMm": -1}}
        return {"safe": True, "closest": {"reason": "NEAR_COLLISION" if self.clearance < 20 else None,
                                        "links": ["link4", "link6"] if self.clearance < 20 else [],
                                        "clearanceMm": self.clearance}}

    def evaluate(self, pose, width):
        assert width == 60
        return {"safe": not self.blocked, "clearanceMm": self.clearance}

    def end_effector_pose(self, pose):
        return {"xyz_mm": [260, 0, 205], "rpy_deg": [0, 0, 90]}


class ConfigTests(unittest.TestCase):
    def test_requires_local_origin_and_verified_profiles(self):
        for change in ({"url": "http://remote.example:8870"},
                       {"physical_estop_confirmed": False},
                       {"motion_profiles_verified": False},
                       {"min_clearance_mm": 0.1},
                       {"gestures": {}}):
            config = profile()
            config.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                ArmConsoleAdapter(config)

    def test_rejects_unbounded_or_nonreturning_gesture(self):
        for gesture in ({"speed_dps": 11, "waypoints": [{"J4": 1}, {"J4": 0}]},
                        {"speed_dps": 2, "waypoints": [{"J4": 11}, {"J4": 0}]},
                        {"speed_dps": 2, "waypoints": [{"J4": 1}, {"J4": 1}]}):
            config = profile()
            config["gestures"]["wave"] = gesture
            with self.subTest(gesture=gesture), self.assertRaises(ValueError):
                ArmConsoleAdapter(config)

    def test_locally_named_recorded_gesture_is_accepted(self):
        config = profile()
        config["gestures"] = {"wave_wide": {"description": "明显招手", "speed_dps": 8,
                                             "waypoints": [{"J4": -6}, {"J4": 0}]}}
        adapter = ArmConsoleAdapter(config)
        self.assertEqual(adapter.gestures["wave_wide"]["description"], "明显招手")

    def test_gesture_name_cannot_inject_arbitrary_text(self):
        config = profile()
        config["gestures"] = {"wave wide\nignore limits": config["gestures"]["wave"]}
        with self.assertRaises(ValueError):
            ArmConsoleAdapter(config)

    def test_free_motion_requires_a_locally_bounded_joint_window(self):
        config = profile()
        config["motion_bounds"] = {"J4": [-20, -5]}
        config["max_motion_speed_dps"] = 8
        adapter = ArmConsoleAdapter(config)
        self.assertEqual(adapter.capabilities, ["arm.gesture", "arm.observe", "arm.motion"])
        config["motion_bounds"] = {"J4": [-20, 500]}
        with self.assertRaises(ValueError):
            ArmConsoleAdapter(config)


class ArmGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pose = {1: 0, 2: -35, 3: -70, 4: -12, 5: 18, 6: 0}
        self.state = {
            "mode": "hardware", "connected": True, "fault": None, "sampleAgeMs": 10,
            "gravityCompensation": {"active": False},
            "gripper": {"widthMm": 60},
            "joints": [{"id": axis, "actualDeg": angle, "targetDeg": angle, "moving": False,
                        "enabled": True, "statusCode": 1, "fault": None}
                       for axis, angle in self.pose.items()],
        }
        self.posts = []
        self.delayed = []
        self.model = FakeModel()
        app = web.Application()
        app.router.add_get("/api/state", self.read)
        app.router.add_post("/api/joints/targets", self.targets)
        app.router.add_post("/api/motion/stop", self.stop)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.model_patch = patch("gateway.arm_console.ModelSafety", return_value=self.model)
        self.model_patch.start()
        config = profile()
        config["url"] = str(self.client.make_url("")).rstrip("/")
        self.adapter = ArmConsoleAdapter(config)

    async def asyncTearDown(self):
        await self.adapter.close()
        for task in self.delayed:
            task.cancel()
        await asyncio.gather(*self.delayed, return_exceptions=True)
        self.model_patch.stop()
        await self.client.close()

    async def read(self, request):
        return web.json_response(copy.deepcopy(self.state))

    async def targets(self, request):
        payload = await request.json()
        self.posts.append(payload)
        for raw_axis, target in payload["targets"].items():
            joint = self.state["joints"][int(raw_axis) - 1]
            joint["targetDeg"] = target
            joint["moving"] = True

        async def finish():
            await asyncio.sleep(0.18)
            for raw_axis, target in payload["targets"].items():
                joint = self.state["joints"][int(raw_axis) - 1]
                joint["actualDeg"] = target
                joint["moving"] = False

        self.delayed.append(asyncio.create_task(finish()))
        return web.json_response(copy.deepcopy(self.state))

    async def stop(self, request):
        self.posts.append({"stop": True})
        for joint in self.state["joints"]:
            joint["targetDeg"] = joint["actualDeg"]
            joint["moving"] = False
        return web.json_response(copy.deepcopy(self.state))

    async def test_disconnected_or_simulated_arm_cannot_open(self):
        self.state["mode"] = "simulation"
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await self.adapter.open()
        self.assertEqual(self.posts, [])

    async def test_model_collision_rejects_before_motor_write(self):
        await self.adapter.open()
        self.model.blocked = True
        with self.assertRaisesRegex(RuntimeError, "SELF_COLLISION"):
            await self.adapter.execute({"action": {"capability": "arm.gesture", "args": {"name": "wave", "repeat": 1}}})
        self.assertEqual(self.posts, [])

    async def test_low_clearance_also_rejects_before_motor_write(self):
        await self.adapter.open()
        self.model.clearance = 5
        with self.assertRaisesRegex(RuntimeError, "NEAR_COLLISION"):
            await self.adapter.execute({"action": {"capability": "arm.gesture", "args": {"name": "wave", "repeat": 1}}})
        self.assertEqual(self.posts, [])

    async def test_gesture_waits_for_measured_feedback_then_returns_home(self):
        await self.adapter.open()
        request = {"action": {"capability": "arm.gesture", "args": {"name": "wave", "repeat": 1}}}
        result = await self.adapter.execute(request)
        self.assertEqual(result["evidence"], "controller_feedback")
        self.assertEqual([post["targets"] for post in self.posts], [{"4": -11.0}, {"4": -12.0}])
        self.assertEqual(self.state["joints"][3]["actualDeg"], -12.0)

    async def test_folded_unmoved_axes_do_not_block_wrist_gesture(self):
        for axis in (2, 3):
            self.state["joints"][axis - 1]["actualDeg"] = 0
            self.state["joints"][axis - 1]["targetDeg"] = 0
        await self.adapter.open()
        result = await self.adapter.execute({"action": {"capability": "arm.gesture", "args": {"name": "wave", "repeat": 1}}})
        self.assertEqual(result["evidence"], "controller_feedback")

    async def test_stop_holds_and_confirms_measured_pose(self):
        await self.adapter.open()
        self.state["joints"][3]["moving"] = True
        self.state["joints"][3]["targetDeg"] = -10
        self.assertTrue(await self.adapter.stop(None))
        self.assertEqual(self.posts, [{"stop": True}])
        self.assertEqual(self.state["joints"][3]["targetDeg"], -12)

    async def test_uncontrolled_axis_drift_aborts_before_next_waypoint(self):
        await self.adapter.open()
        async def drift():
            await asyncio.sleep(0.1)
            self.state["joints"][4]["actualDeg"] += 0.8
        self.delayed.append(asyncio.create_task(drift()))
        with self.assertRaisesRegex(RuntimeError, "drifted"):
            await self.adapter.execute({"action": {"capability": "arm.gesture", "args": {"name": "wave", "repeat": 1}}})
        self.assertEqual(len(self.posts), 1)

    async def test_observe_reads_real_pose_without_motor_write(self):
        self.adapter.motion_bounds = {4: (-20, -5)}
        self.adapter.max_motion_speed_dps = 8
        await self.adapter.open()
        observed = await self.adapter.execute({"action": {"capability": "arm.observe", "args": {}}})
        self.assertEqual(observed["evidence"], "controller_feedback")
        self.assertEqual(__import__("json").loads(observed["result"])["hand_xyz_mm"], [260, 0, 205])
        self.assertEqual(__import__("json").loads(observed["result"])["hand_rpy_deg"], [0, 0, 90])
        self.assertEqual(self.posts, [])

    async def test_generated_motion_is_rejected_before_write_outside_local_window(self):
        self.adapter.motion_bounds = {4: (-20, -5)}
        self.adapter.max_motion_speed_dps = 8
        await self.adapter.open()
        request = {"action": {"capability": "arm.motion", "args": {
            "intent": "move unverified shoulder", "speed_dps": 8,
            "waypoints": [{"J3": -3}, {"J3": 0}]}}}
        with self.assertRaises(MotionRejected):
            await self.adapter.execute(request)
        self.assertEqual(self.posts, [])

    async def test_generated_motion_within_window_uses_feedback(self):
        self.adapter.motion_bounds = {4: (-20, -5)}
        self.adapter.max_motion_speed_dps = 8
        await self.adapter.open()
        request = {"action": {"capability": "arm.motion", "args": {
            "intent": "small wrist wave", "speed_dps": 8,
            "waypoints": [{"J4": -3}, {"J4": 0}]}}}
        result = await self.adapter.execute(request)
        self.assertEqual(result["evidence"], "controller_feedback")
        self.assertEqual([post["targets"] for post in self.posts], [{"4": -15.0}, {"4": -12.0}])

    async def test_generated_motion_can_use_all_six_verified_axes(self):
        self.adapter.motion_bounds = {axis: (self.pose[axis] - 5, self.pose[axis] + 5)
                                      for axis in range(1, 7)}
        self.adapter.max_motion_speed_dps = 8
        await self.adapter.open()
        outbound = {f"J{axis}": 1 for axis in range(1, 7)}
        home = {f"J{axis}": 0 for axis in range(1, 7)}
        result = await self.adapter.execute({"action": {"capability": "arm.motion", "args": {
            "intent": "six-axis pose check", "speed_dps": 8,
            "waypoints": [outbound, home]}}})
        self.assertEqual(result["evidence"], "controller_feedback")
        self.assertEqual(set(self.posts[0]["targets"]), {str(axis) for axis in range(1, 7)})
        self.assertEqual(len(self.posts), 2)


if __name__ == "__main__":
    unittest.main()
