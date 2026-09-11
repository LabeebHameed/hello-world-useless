import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from world import MAX_SOCIAL_VALUE, World, starter_world


class DailyLifeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "world.json"
        self.world = World(self.path)

    def test_one_day_has_predictable_schedule_and_bounded_needs(self):
        self.world.advance(8)
        at_departure = self.world.snapshot()
        self.assertEqual(at_departure["clock"]["tick"], 8)
        self.assertTrue(all(
            citizen["location_id"] == "street" and citizen["activity"] == "travelling"
            for citizen in at_departure["citizens"].values()
        ))

        self.world.advance(1)
        at_destination = self.world.snapshot()
        self.assertTrue(all(
            citizen["location_id"] == "shop"
            and citizen["activity"] == "at_regular_destination"
            for citizen in at_destination["citizens"].values()
        ))

        self.world.advance(15)
        after_day = self.world.snapshot()
        self.assertEqual(after_day["clock"]["tick"], 24)
        for citizen in after_day["citizens"].values():
            self.assertEqual(citizen["location_id"], citizen["home_id"])
            self.assertEqual(citizen["activity"], "sleeping")
            self.assertEqual(citizen["needs"]["hunger"], 24)
            self.assertTrue(0 <= citizen["needs"]["energy"] <= 100)

    def test_move_uses_only_one_valid_directed_path(self):
        event = self.world.act("citizen-1", "move", "street")
        self.assertEqual(event["type"], "citizen_moved")
        self.assertEqual(
            event["details"],
            {"from_location_id": "home-1", "to_location_id": "street"},
        )
        self.world.act("citizen-1", "move", "shop")
        self.assertEqual(self.world.snapshot()["citizens"]["citizen-1"]["location_id"], "shop")

        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "not connected"):
            self.world.act("citizen-1", "move", "home-1")
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_move_write_does_not_commit(self):
        before = self.world.snapshot()
        before_file = self.path.read_bytes()
        with patch("world.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.world.act("citizen-1", "move", "street")
        self.assertEqual(self.world.snapshot(), before)
        self.assertEqual(self.path.read_bytes(), before_file)

    def test_move_respects_one_way_path_direction(self):
        state = starter_world()
        state["paths"]["path-home-1"]["bidirectional"] = False
        self.path.write_text(json.dumps(state), encoding="utf-8")
        world = World(self.path)
        with self.assertRaisesRegex(ValueError, "not connected"):
            world.act("citizen-1", "move", "street")

    def test_low_energy_citizen_returns_home_and_recovers(self):
        state = starter_world()
        citizen = state["citizens"]["citizen-1"]
        citizen["location_id"] = "shop"
        citizen["activity"] = "at_regular_destination"
        citizen["needs"]["energy"] = 20
        self.path.write_text(json.dumps(state), encoding="utf-8")
        world = World(self.path)

        world.advance(1)
        self.assertEqual(world.snapshot()["citizens"]["citizen-1"]["location_id"], "street")
        world.advance(1)
        home = world.snapshot()["citizens"]["citizen-1"]
        self.assertEqual(home["location_id"], home["home_id"])
        energy_at_home = home["needs"]["energy"]
        world.advance(1)
        resting = world.snapshot()["citizens"]["citizen-1"]
        self.assertEqual(resting["location_id"], resting["home_id"])
        self.assertGreater(resting["needs"]["energy"], energy_at_home)

    def test_unattended_day_runs_behavior_without_tick_events(self):
        self.world.start()
        for _ in range(24):
            self.world.tick_if_running()
        state = self.world.snapshot()
        self.assertEqual(state["clock"]["tick"], 24)
        self.assertTrue(any(event["type"] == "citizen_moved" for event in state["events"]))
        self.assertFalse(any(event["type"] == "time_advanced" for event in state["events"]))

    def test_rule_based_movement_calls_world_act(self):
        with patch.object(self.world, "act", wraps=self.world.act) as act:
            self.world.advance(8)
        self.assertEqual(act.call_count, 3)
        self.assertTrue(all(call.kwargs["action_name"] == "move" for call in act.call_args_list))

    def test_headless_server_runs_a_simulated_day(self):
        server_path = Path(self.directory.name) / "headless-day.json"
        root = Path(__file__).resolve().parents[1]
        process = subprocess.Popen(
            [
                sys.executable, str(root / "server.py"), "--save", str(server_path),
                "--tick-seconds", "0.005",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail(process.stderr.read().decode())
                try:
                    if json.loads(server_path.read_text())["clock"]["tick"] >= 24:
                        break
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
                time.sleep(0.01)
            else:
                self.fail("Headless server did not complete a simulated day")
        finally:
            process.terminate()
            _, error = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, error.decode())
        state = World(server_path).snapshot()
        self.assertGreaterEqual(state["clock"]["tick"], 24)
        self.assertTrue(all(
            0 <= need <= 100
            for citizen in state["citizens"].values()
            for need in citizen["needs"].values()
        ))

    def test_daily_state_and_private_social_state_survive_restart(self):
        for _ in range(MAX_SOCIAL_VALUE):
            self.world.act(
                "citizen-2", "talk", "citizen-1", "Stay away.", {"intent": "insult"}
            )
        self.world.advance(9)
        expected_public = self.world.snapshot()
        expected_private = self.world.citizen_context("citizen-1")

        restored = World(self.path)
        self.assertEqual(restored.snapshot(), expected_public)
        self.assertEqual(restored.citizen_context("citizen-1"), expected_private)
        self.assertNotIn("private_social", restored.snapshot())

    def test_schema_two_migration_preserves_private_social_state(self):
        self.world.act(
            "citizen-1", "talk", "citizen-2", "I trust you.", {"intent": "confide"}
        )
        old = json.loads(self.path.read_text())
        old["schema_version"] = 2
        for citizen in old["citizens"].values():
            citizen.pop("needs")
            citizen.pop("schedule")
            citizen.pop("activity")
        self.path.write_text(json.dumps(old), encoding="utf-8")

        migrated = World(self.path)
        self.assertEqual(migrated.snapshot()["schema_version"], 4)
        self.assertEqual(
            migrated.citizen_context("citizen-2")["relationships"]["citizen-1"]["trust"],
            1,
        )

    def test_strong_distrust_rule_is_explicit_and_private(self):
        state = self.world.snapshot()
        state["clock"]["tick"] = 9
        state["citizens"]["citizen-2"]["location_id"] = "shop"
        relationship = {
            "relationships": {
                "citizen-2": {"trust": 0, "friendship": 0, "anger": MAX_SOCIAL_VALUE}
            }
        }
        decision = World.choose_action(state, "citizen-1", relationship)
        self.assertIsNone(decision)
        self.assertNotIn("relationships", state["citizens"]["citizen-1"])


if __name__ == "__main__":
    unittest.main()
