"""CSV seed levels and group-to-knockout virtual seeding."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend import logic
from backend.storage import Database, DataError, parse_entrants


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SeedIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.database = Database(Path(self.temporary.name))
        self.database.create_competition({
            "slug": "revision-four", "name": "修订四", "start_date": "2026-10-12",
            "end_date": "2026-10-12", "venue": "测试场地",
        })

    def test_csv_accepts_zero_seed_for_each_format(self) -> None:
        samples = {
            "single": "甲,北大,0",
            "double": "甲,乙,北大,0",
            "team": "北大,0,甲,乙",
        }
        for format_name, csv_text in samples.items():
            with self.subTest(format_name=format_name):
                self.assertEqual(parse_entrants(csv_text, format_name)[0]["seed"], 0)
        for seed in ("-1", "", "1.5", "true"):
            with self.subTest(seed=seed), self.assertRaisesRegex(DataError, "非负整数"):
                parse_entrants(f"甲,北大,{seed}", "single")

    def test_group_standings_assign_virtual_seeds_to_knockout(self) -> None:
        csv_text = "\n".join(f"选手{i},北大,0" for i in range(1, 9))
        event = self.database.create_event("revision-four", {
            "discipline": "men", "format": "single", "schedule": "group-knockout",
            "csv": csv_text, "group_spec": "4*2(2)", "knockout_rounds": 3,
        })["event"]["slug"]
        groups = self.database.draw_stage("revision-four", event, "group")["stages"]["group"]["data"]["groups"]
        for group in groups:
            match_id = group["matches"][0]["id"]
            self.database.mutate_match("revision-four", event, "group", match_id, "add_game", {"a": 11, "b": 5})
            self.database.mutate_match("revision-four", event, "group", match_id, "edit_match", {"finished": True})

        state = self.database.get_event("revision-four", event)
        groups = state["stages"]["group"]["data"]["groups"]
        entrants = state["stages"]["knockout"]["metadata"]["entrants"]
        seed_by_name = {item["participant"]["members"][0]: item["seed"] for item in entrants}
        self.assertEqual(sorted(seed_by_name.values()), [-1] * 4 + [0] * 4)
        for group_index, group in enumerate(groups):
            ranks = group["standings"]["order"]
            self.assertIsNotNone(ranks)
            for rank_index, participant_index in enumerate(ranks):
                name = group["participants"][participant_index]["members"][0]
                self.assertEqual(seed_by_name[name], 0 if rank_index == 0 else -1)

        drawn = self.database.draw_stage("revision-four", event, "knockout")
        slots = drawn["stages"]["knockout"]["data"]["slots"]
        for position in range(0, len(slots), 2):
            pair = slots[position:position + 2]
            self.assertEqual(sorted(seed_by_name[slot["members"][0]] for slot in pair), [-1, 0])

    def test_unequal_qualifier_counts_keep_winner_and_other_categories(self) -> None:
        csv_text = "\n".join(f"选手{i},北大,0" for i in range(1, 7))
        event = self.database.create_event("revision-four", {
            "discipline": "women", "format": "single", "schedule": "group-knockout",
            "csv": csv_text, "group_spec": "1*2(1)+2*2(2)", "knockout_rounds": 3,
        })["event"]["slug"]
        groups = self.database.draw_stage("revision-four", event, "group")["stages"]["group"]["data"]["groups"]
        for group in groups:
            match_id = group["matches"][0]["id"]
            self.database.mutate_match("revision-four", event, "group", match_id, "add_game", {"a": 11, "b": 5})
            self.database.mutate_match("revision-four", event, "group", match_id, "edit_match", {"finished": True})
        entrants = self.database.get_event("revision-four", event)["stages"]["knockout"]["metadata"]["entrants"]
        self.assertEqual([item["seed"] for item in entrants], [0, 0, -1, 0, -1])


if __name__ == "__main__":
    unittest.main()
