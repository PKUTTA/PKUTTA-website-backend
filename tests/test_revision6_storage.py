"""Walkovers, stable group standings, qualifying seeds, and referee plans."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend import logic
from backend.storage import Database, DataError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RevisionSixStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.db = Database(self.root)
        self.db.create_competition({
            "slug": "revision-six", "name": "修订六", "start_date": "2026-10-13",
            "end_date": "2026-10-13", "venue": "测试体育馆",
        })

    def create_groups(self, seeds: tuple[int, ...] = (2, 1, 0, 0, 0, 0)) -> str:
        event = self.db.create_event("revision-six", {
            "discipline": "men", "format": "single", "schedule": "group-knockout",
            "csv": "\n".join(f"选手{i},北大,{seed}" for i, seed in enumerate(seeds)),
            "group_spec": "2*3(2)", "knockout_rounds": 2,
        })["event"]["slug"]
        self.db.draw_stage("revision-six", event, "group")
        return event

    def groups(self, event: str) -> list[dict]:
        return self.db.get_event("revision-six", event)["stages"]["group"]["data"]["groups"]

    def test_standalone_knockout_imports_all_seed_categories_and_draws(self) -> None:
        for discipline, format_name in (("men", "single"), ("women", "double"), ("mixed", "team")):
            seeds = (3, 1, 0, -1, -1)
            if format_name == "single":
                rows = [f"选手{i},北大,{seed}" for i, seed in enumerate(seeds)]
            elif format_name == "double":
                rows = [f"选手{i}甲,选手{i}乙,北大,{seed}" for i, seed in enumerate(seeds)]
            else:
                rows = [f"队伍{i},{seed},选手{i}" for i, seed in enumerate(seeds)]
            with self.subTest(format_name=format_name):
                detail = self.db.create_event("revision-six", {
                    "discipline": discipline, "format": format_name, "schedule": "knockout",
                    "csv": "\n".join(rows), "knockout_rounds": 3,
                })
                event = detail["event"]["slug"]
                entrants = detail["stages"]["knockout"]["metadata"]["entrants"]
                self.assertEqual([item["seed"] for item in entrants], list(seeds))
                stage = self.db.draw_stage("revision-six", event, "knockout")["stages"]["knockout"]["data"]
                self.assertEqual(stage["slots"][0], entrants[0]["participant"])
                self.assertEqual(stage["slots"][7], entrants[1]["participant"])
                self.assertIn(stage["slots"].index(entrants[2]["participant"]), (2, 5))
                self.assertEqual(sum(slot["kind"] == "bye" for slot in stage["slots"]), 3)
                self.assertCountEqual(
                    [slot for slot in stage["slots"] if slot["kind"] != "bye"],
                    [item["participant"] for item in entrants],
                )

    def test_negative_seed_rejected_outside_standalone_knockout_category(self) -> None:
        for schedule, seed in (("knockout", -2), ("group-knockout", -1), ("round-robin", -1)):
            with self.subTest(schedule=schedule), self.assertRaisesRegex(DataError, "非负整数"):
                self.db.create_event("revision-six", {
                    "discipline": "men", "format": "single", "schedule": schedule,
                    "csv": f"甲,北大,{seed}\n乙,北大,0", "group_spec": "1*2(2)", "knockout_rounds": 1,
                })
        self.assertEqual(self.db.get_competition("revision-six")["competition"]["events"], [])

    def test_restart_migrates_qualifier_seeds_and_preserves_same_members_scores(self) -> None:
        event = self.create_groups()
        for group in self.groups(event):
            for game in group["matches"]:
                self.db.mutate_match("revision-six", event, "group", game["id"], "add_game", {"a": 11, "b": 5})
                self.db.mutate_match("revision-six", event, "group", game["id"], "edit_match", {"finished": True})
        drawn = self.db.draw_stage("revision-six", event, "knockout")["stages"]["knockout"]["data"]
        game_id = drawn["matches"][0]["id"]
        self.db.mutate_match("revision-six", event, "knockout", game_id, "add_game", {"a": 11, "b": 5})
        before = self.db.mutate_match("revision-six", event, "knockout", game_id, "edit_match", {"finished": True})
        stage = before["stages"]["knockout"]
        expected_entrants = stage["metadata"]["entrants"]
        event_path = self.root / "webpage" / "database" / "revision-six" / event
        legacy_metadata = json.loads((event_path / "knockout" / "metadata.json").read_text(encoding="utf-8"))
        for index, entry in enumerate(legacy_metadata["entrants"]):
            entry["seed"] = 100 - index
        (event_path / "knockout" / "metadata.json").write_text(json.dumps(legacy_metadata, ensure_ascii=False), encoding="utf-8")
        legacy_group = before["stages"]["group"]["data"]
        for group in legacy_group["groups"]:
            group.pop("standings")
            group.pop("ranking_draw_order")
        (event_path / "group" / "data.json").write_text(json.dumps(legacy_group, ensure_ascii=False), encoding="utf-8")
        migrated = Database(self.root).get_event("revision-six", event)["stages"]["knockout"]
        self.assertEqual(migrated["metadata"]["entrants"], expected_entrants)
        self.assertEqual(migrated["data"], stage["data"])
        saved = json.loads((event_path / "knockout" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["entrants"], expected_entrants)

    def test_restart_new_group_ranking_changes_qualifiers_and_clears_old_bracket(self) -> None:
        event = self.create_groups()
        groups = self.groups(event)
        for group_index, group in enumerate(groups):
            results = (
                [[[11, 5]] * 3 + [[5, 11]] * 2, [[0, 11]] * 3, [[11, 0]] * 3]
                if group_index == 0 else [[[11, 5]], [[11, 5]], [[11, 5]]]
            )
            for game, scores in zip(group["matches"], results):
                for score_a, score_b in scores:
                    self.db.mutate_match("revision-six", event, "group", game["id"], "add_game", {"a": score_a, "b": score_b})
                self.db.mutate_match("revision-six", event, "group", game["id"], "edit_match", {"finished": True})
        before = self.db.get_event("revision-six", event)
        expected_entrants = before["stages"]["knockout"]["metadata"]["entrants"]
        legacy_group = before["stages"]["group"]["data"]
        legacy_entrants = []
        for group in legacy_group["groups"]:
            old_order = logic.rank_round_robin(group["participants"], group["matches"])
            for index in old_order[:group["qualifiers"]]:
                legacy_entrants.append({"participant": group["participants"][index], "seed": 10 - len(legacy_entrants)})
            group.pop("standings")
            group.pop("ranking_draw_order")
        self.assertNotEqual(
            {item["participant"]["members"][0] for item in expected_entrants},
            {item["participant"]["members"][0] for item in legacy_entrants},
        )
        legacy_knockout = logic.draw_knockout(legacy_entrants, 2, rng_seed=10)
        legacy_knockout["matches"][0].update(finished=True, games=[[11, 5]])
        legacy_knockout = logic.refresh_knockout_bracket(legacy_knockout)
        event_path = self.root / "webpage" / "database" / "revision-six" / event
        (event_path / "group" / "data.json").write_text(json.dumps(legacy_group, ensure_ascii=False), encoding="utf-8")
        (event_path / "knockout" / "metadata.json").write_text(json.dumps({"type": "knockout", "entrants": legacy_entrants}, ensure_ascii=False), encoding="utf-8")
        (event_path / "knockout" / "data.json").write_text(json.dumps(legacy_knockout, ensure_ascii=False), encoding="utf-8")
        migrated = Database(self.root).get_event("revision-six", event)["stages"]["knockout"]
        self.assertEqual(migrated["metadata"]["entrants"], expected_entrants)
        self.assertFalse(migrated["data"]["drawn"])
        self.assertTrue(all(slot["kind"] == "undecided" for slot in migrated["data"]["slots"]))
        self.assertTrue(all(not game["finished"] and game["games"] == [] for game in migrated["data"]["matches"]))

    def finish_groups(self, event: str) -> None:
        for group in self.groups(event):
            for match, scores in zip(group["matches"], ((11, 5), (5, 11), (11, 5))):
                self.db.mutate_match("revision-six", event, "group", match["id"], "add_game", dict(zip(("a", "b"), scores)))
                self.db.mutate_match("revision-six", event, "group", match["id"], "edit_match", {"finished": True})

    def test_walkover_only_entry_and_double_walkover_finish(self) -> None:
        event = self.db.create_event("revision-six", {
            "discipline": "women", "format": "single", "schedule": "round-robin",
            "csv": "甲,北大,0\n乙,北大,0",
        })["event"]["slug"]
        match_id = self.db.get_event("revision-six", event)["stages"]["round_robin"]["data"]["matches"][0]["id"]

        def change(action: str, payload: dict, index: int | None = None) -> dict:
            return self.db.mutate_match("revision-six", event, "round_robin", match_id, action, payload, index)["stages"]["round_robin"]["data"]["matches"][0]

        change("add_game", {"a": None, "b": " WO "})
        change("add_game", {"a": 11, "b": "wo"})
        match = change("edit_match", {"finished": True})
        self.assertEqual(match["games"], [[0, "wo"], [11, "wo"]])
        self.assertEqual(logic.big_score(match), [2, "wo"])
        self.assertEqual(logic.match_winner_side(match), 0)
        change("edit_game", {"a": "wo", "b": "wo"}, 0)
        match = change("edit_match", {"finished": True})
        self.assertEqual(logic.big_score(match), ["wo", "wo"])
        self.assertIsNone(logic.match_winner_side(match))
        for value in ("forfeit", "11", -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(DataError):
                change("add_game", {"a": value, "b": 5})

    def test_group_draw_is_persisted_and_survives_metadata_edits(self) -> None:
        event = self.create_groups()
        self.finish_groups(event)
        first = self.groups(event)[0]
        self.assertTrue(all(row["draw_rank"] is not None for row in first["standings"]["rows"]))
        self.db.update_group("revision-six", event, 1, {"scheduled_referee": "裁判甲"})
        after = self.groups(event)[0]
        self.assertEqual(after["standings"], first["standings"])
        self.assertEqual(after["ranking_draw_order"], first["ranking_draw_order"])
        reopened = Database(self.root).get_event("revision-six", event)["stages"]["group"]["data"]["groups"][0]
        self.assertEqual(reopened["standings"], first["standings"])
        self.db.mutate_match("revision-six", event, "group", first["matches"][0]["id"], "edit_match", {"finished": False})
        self.assertIsNone(self.groups(event)[0]["standings"])
        self.db.mutate_match("revision-six", event, "group", first["matches"][0]["id"], "edit_match", {"finished": True})
        self.assertEqual(self.groups(event)[0]["standings"], first["standings"])

    def test_qualifying_seeds_distinguish_seeded_and_unseeded_group_winners(self) -> None:
        event = self.create_groups((1, 0, 0, 0, 0, 0))
        self.finish_groups(event)
        detail = self.db.get_event("revision-six", event)
        entrants = detail["stages"]["knockout"]["metadata"]["entrants"]
        by_name = {item["participant"]["members"][0]: item["seed"] for item in entrants}
        for group_index, group in enumerate(detail["stages"]["group"]["data"]["groups"]):
            order = group["standings"]["order"]
            self.assertEqual(by_name[group["participants"][order[0]]["members"][0]], 1 if group_index == 0 else 0)
            self.assertEqual(by_name[group["participants"][order[1]]["members"][0]], -1)
        self.assertEqual(sorted(by_name.values()), [-1, -1, 0, 1])
        drawn = self.db.draw_stage("revision-six", event, "knockout")["stages"]["knockout"]["data"]
        self.assertEqual(by_name[drawn["slots"][0]["members"][0]], 1)

    def test_referee_plan_propagates_without_reverse_updates_and_survives_redraw(self) -> None:
        event = self.create_groups()
        self.db.update_group("revision-six", event, 1, {"scheduled_referee": " 赵老师 "})
        first = self.groups(event)[0]
        self.assertTrue(all(match["referee"] == "赵老师" for match in first["matches"]))
        self.db.mutate_match("revision-six", event, "group", first["matches"][0]["id"], "edit_match", {"referee": "钱老师"})
        changed = self.groups(event)[0]
        self.assertEqual(changed["scheduled_referee"], "赵老师")
        self.assertEqual(changed["matches"][0]["referee"], "钱老师")
        self.db.draw_stage("revision-six", event, "group")
        self.assertTrue(all(match["referee"] == "赵老师" for match in self.groups(event)[0]["matches"]))


if __name__ == "__main__":
    unittest.main()
