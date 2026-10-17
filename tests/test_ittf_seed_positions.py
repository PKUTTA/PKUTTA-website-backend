"""Knockout seeds take ITTF positions; existing brackets are never redrawn."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend import logic
from backend.storage import Database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def player(name: str) -> dict:
    return {"kind": "player", "members": [name], "team": "北大"}


def entrant(name: str, seed: int) -> dict:
    return {"participant": player(name), "seed": seed}


def seeded_field(rounds: int, seed_count: int) -> list[dict]:
    """Seed ``S1`` highest; fill the bracket with nonseeded entrants."""
    entries = [entrant(f"S{rank}", seed_count + 1 - rank) for rank in range(1, seed_count + 1)]
    entries += [entrant(f"x{index}", -1) for index in range(2**rounds - seed_count)]
    return entries


def seed_positions(stage: dict) -> dict[int, int]:
    """Map seed rank to its 1-based draw position."""
    return {int(slot["members"][0][1:]): index + 1 for index, slot in enumerate(stage["slots"])
            if slot["kind"] == "player" and slot["members"][0].startswith("S")}


class IttfSeedPositionTests(unittest.TestCase):
    def test_sixty_four_draw_matches_the_ittf_table(self) -> None:
        # ITTF seeding by ranking, written out for a 64-place draw.
        expected = {
            (1, 1): {1},
            (2, 2): {64},
            (3, 4): {32, 33},
            (5, 8): {16, 17, 48, 49},
            (9, 16): {8, 9, 24, 25, 40, 41, 56, 57},
            (17, 32): {4, 5, 12, 13, 20, 21, 28, 29, 36, 37, 44, 45, 52, 53, 60, 61},
        }
        for rng_seed in range(20):
            positions = seed_positions(logic.draw_knockout(seeded_field(6, 32), 6, rng_seed=rng_seed))
            for (first, last), places in expected.items():
                with self.subTest(rng_seed=rng_seed, ranks=(first, last)):
                    self.assertEqual({positions[rank] for rank in range(first, last + 1)}, places)

    def test_seed_layout_is_mirror_symmetric_with_one_seed_per_match(self) -> None:
        for rounds in range(3, 8):
            capacity = 2**rounds
            stage = logic.draw_knockout(seeded_field(rounds, capacity // 2), rounds, rng_seed=rounds)
            places = set(seed_positions(stage).values())
            self.assertEqual(len(places), capacity // 2)
            self.assertEqual(places, {capacity + 1 - place for place in places})
            self.assertEqual(len({(place - 1) // 2 for place in places}), capacity // 2)

    def test_tier_members_are_drawn_among_their_positions(self) -> None:
        arrangements = {
            tuple(seed_positions(logic.draw_knockout(seeded_field(4, 8), 4, rng_seed=rng_seed))[rank]
                  for rank in (3, 4))
            for rng_seed in range(40)
        }
        self.assertEqual(arrangements, {(8, 9), (9, 8)})

    def test_partial_tier_uses_ittf_places_and_byes_face_seeds(self) -> None:
        entries = [entrant(f"S{rank}", 6 - rank) for rank in range(1, 6)]
        entries += [entrant(f"x{index}", -1) for index in range(4)]
        for rng_seed in range(30):
            stage = logic.draw_knockout(entries, 4, rng_seed=rng_seed)
            positions = seed_positions(stage)
            self.assertEqual((positions[1], positions[2]), (1, 16))
            self.assertEqual({positions[3], positions[4]}, {8, 9})
            self.assertIn(positions[5], {4, 5, 12, 13})
            slots = stage["slots"]
            for rank in range(1, 6):
                self.assertEqual(slots[(positions[rank] - 1) ^ 1]["kind"], "bye")


class ExistingBracketTests(unittest.TestCase):
    def test_restart_keeps_a_bracket_drawn_with_the_old_layout(self) -> None:
        scratch = PROJECT_ROOT / "tmp" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        database = Database(root)
        database.create_competition({
            "slug": "legacy", "name": "旧签表", "start_date": "2026-10-17",
            "end_date": "2026-10-18", "venue": "测试体育馆",
        })
        rows = [f"选手{index},北大,{seed}" for index, seed in enumerate((2, 1, 0, 0, -1, -1, -1, -1))]
        event = database.create_event("legacy", {
            "discipline": "men", "format": "single", "schedule": "knockout",
            "csv": "\n".join(rows), "knockout_rounds": 3,
        })["event"]["slug"]
        stage = database.draw_stage("legacy", event, "knockout")["stages"]["knockout"]["data"]
        # Rewrite it as the previous algorithm stored it: seed 2 on place 7.
        slots = stage["slots"]
        slots[6], slots[7] = slots[7], slots[6]
        legacy = logic.refresh_knockout_bracket({"drawn": True, "rounds": 3, "slots": slots, "matches": []})
        legacy["matches"][0]["games"] = [[11, 5], [11, 7], [11, 9]]
        legacy["matches"][0]["finished"] = True
        path = root / "webpage" / "database" / "legacy" / event / "knockout" / "data.json"
        path.write_text(json.dumps(logic.refresh_knockout_bracket(legacy), ensure_ascii=False), encoding="utf-8")

        reopened = Database(root).get_event("legacy", event)["stages"]["knockout"]["data"]
        self.assertEqual(reopened["slots"], slots)
        self.assertEqual(reopened["matches"][0]["games"], [[11, 5], [11, 7], [11, 9]])
        self.assertTrue(reopened["matches"][0]["finished"])


if __name__ == "__main__":
    unittest.main()
