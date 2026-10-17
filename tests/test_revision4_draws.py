"""Seed placement checks for the revision 4 group and knockout draws."""

from __future__ import annotations

import copy
import unittest

from backend.logic import draw_groups, draw_knockout


def entrant(name: str, seed: int) -> dict:
    return {"participant": {"kind": "player", "members": [name], "team": "北大"}, "seed": seed}


def name(participant: dict) -> str:
    return participant["members"][0]


class SeededDrawTests(unittest.TestCase):
    def test_group_priority_uses_only_one_seed_per_group(self) -> None:
        entrants = [
            entrant("D", 60), entrant("I", 0), entrant("B", 80), entrant("G", 0),
            entrant("A", 90), entrant("E", 50), entrant("C", 70), entrant("F", 0),
            entrant("H", 0),
        ]
        original = copy.deepcopy(entrants)
        specs = [{"count": 3, "size": 3, "qualifiers": 1}]
        groups = draw_groups(entrants, specs, rng_seed=17)
        self.assertEqual(groups, draw_groups(entrants, specs, rng_seed=17))
        self.assertEqual(entrants, original)
        self.assertEqual([name(group["participants"][0]) for group in groups], ["A", "B", "C"])
        self.assertEqual([len(group["participants"]) for group in groups], [3, 3, 3])
        self.assertEqual(
            {name(person) for group in groups for person in group["participants"]},
            set("ABCDEFGHI"),
        )
        self.assertEqual([len(group["matches"]) for group in groups], [3, 3, 3])
        # Additional seeds have no protected group position; they join the
        # same random pool as zero-seed entrants.
        destinations = {
            next(i for i, group in enumerate(draw_groups(entrants, specs, rng_seed=rng_seed))
                 if "D" in [name(person) for person in group["participants"]])
            for rng_seed in range(16)
        }
        self.assertGreater(len(destinations), 1)

    def test_group_draw_accepts_zero_but_rejects_invalid_seed(self) -> None:
        specs = [{"count": 2, "size": 1, "qualifiers": 1}]
        self.assertEqual(len(draw_groups([entrant("A", 0), entrant("B", 0)], specs, rng_seed=5)), 2)
        for bad_seed in (-1, None, True, 1.5):
            with self.subTest(seed=bad_seed), self.assertRaises(ValueError):
                draw_groups([entrant("A", bad_seed), entrant("B", 0)], specs)

    def test_knockout_seeded_regions_and_remaining_slots(self) -> None:
        entrants = [entrant(str(index), index) for index in range(1, 13)]
        entrants.reverse()
        original = copy.deepcopy(entrants)
        stage = draw_knockout(entrants, 4, rng_seed=23)
        self.assertEqual(stage, draw_knockout(entrants, 4, rng_seed=23))
        self.assertEqual(entrants, original)
        slots = stage["slots"]
        positions = {name(slot): index for index, slot in enumerate(slots) if slot["kind"] == "player"}
        self.assertEqual(positions["12"], 0)
        self.assertEqual(positions["11"], 15)
        self.assertEqual({positions["10"], positions["9"]}, {7, 8})
        self.assertEqual({positions[str(seed)] for seed in range(5, 9)}, {3, 4, 11, 12})
        self.assertEqual({positions[str(seed)] // 4 for seed in (12, 11, 10, 9)}, set(range(4)))
        self.assertEqual({positions[str(seed)] // 2 for seed in range(5, 13)}, set(range(8)))
        byes = {index for index, slot in enumerate(slots) if slot["kind"] == "bye"}
        self.assertEqual(byes, {positions[str(seed)] ^ 1 for seed in (12, 11, 10, 9)})
        self.assertEqual({positions[str(seed)] ^ 1 for seed in range(5, 9)}, {positions[str(seed)] for seed in range(1, 5)})
        self.assertEqual(len(stage["matches"]), 15)

    def test_knockout_accepts_nonseed_level_and_small_brackets(self) -> None:
        entrants = [entrant(str(index), max(-1, index - 3)) for index in range(9)]
        stage = draw_knockout(entrants, 4, rng_seed=3)
        self.assertEqual(name(stage["slots"][0]), "8")
        self.assertEqual(name(stage["slots"][15]), "7")
        self.assertEqual(sum(slot["kind"] == "bye" for slot in stage["slots"]), 7)
        small = draw_knockout([entrant("A", 2), entrant("B", 1), entrant("C", 0)], 2, rng_seed=3)
        self.assertEqual(len(small["matches"]), 3)


if __name__ == "__main__":
    unittest.main()
