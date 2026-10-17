"""Fairness checks: leftover group seeds, knockout byes, group-mate separation, and tie restarts."""

from __future__ import annotations

import unittest
from collections import Counter

from backend import logic
from backend.storage import Database


def player(name: str) -> dict:
    return {"kind": "player", "members": [name], "team": "北大"}


def entrant(name: str, seed: int, group: int | None = None, rank: int = 0) -> dict:
    item = {"participant": player(name), "seed": seed}
    if group is not None:
        item.update(group=group, group_rank=rank)
    return item


def name(participant: dict) -> str:
    return participant["members"][0]


class GroupDrawFairnessTests(unittest.TestCase):
    def test_leftover_equal_seeds_spread_evenly_over_groups(self) -> None:
        # One top seed plus five equal seeds for four groups: two equal seeds
        # are left over each draw and must not drift toward the last groups.
        entries = [entrant("S", 2)] + [entrant(f"T{i}", 1) for i in range(5)]
        entries += [entrant(f"x{i}", 0) for i in range(10)]
        specs = [{"count": 4, "size": 4, "qualifiers": 2}]
        counts: Counter[int] = Counter()
        for rng_seed in range(2000):
            for group_index, group in enumerate(logic.draw_groups(entries, specs, rng_seed=rng_seed)):
                counts[group_index] += sum(name(p).startswith("T") for p in group["participants"][1:])
        self.assertEqual(sum(counts.values()), 4000)
        self.assertTrue(all(850 < counts[index] < 1150 for index in range(4)), counts)


class KnockoutDrawFairnessTests(unittest.TestCase):
    def test_byes_never_pair_and_go_to_seeds_first(self) -> None:
        entries = [entrant("S1", 3), entrant("S2", 2), entrant("S3", 1), entrant("W1", 0), entrant("W2", 0)]
        entries += [entrant(f"x{i}", -1) for i in range(4)]
        for rng_seed in range(200):
            stage = logic.draw_knockout(entries, 4, rng_seed=rng_seed)
            opening = [match for match in stage["matches"] if match["round"] == 0]
            self.assertFalse(any(m["a"]["kind"] == m["b"]["kind"] == "bye" for m in opening))
            with_bye = {name(m["b"] if m["a"]["kind"] == "bye" else m["a"])
                        for m in opening if "bye" in (m["a"]["kind"], m["b"]["kind"])}
            self.assertTrue({"S1", "S2", "S3", "W1", "W2"} <= with_bye)

    def test_unseeded_bracket_has_one_bye_per_match_at_most(self) -> None:
        entries = [entrant(f"x{i}", -1) for i in range(9)]
        for rng_seed in range(200):
            slots = logic.draw_knockout(entries, 4, rng_seed=rng_seed)["slots"]
            pairs = [(slots[i]["kind"], slots[i + 1]["kind"]) for i in range(0, 16, 2)]
            self.assertNotIn(("bye", "bye"), pairs)
            self.assertEqual(sum(pair.count("bye") for pair in pairs), 7)

    def test_group_runner_up_sits_in_the_other_half(self) -> None:
        entries = []
        for group in range(8):
            entries.append(entrant(f"G{group}A", 4 - group if group < 4 else 0, group, 0))
            entries.append(entrant(f"G{group}B", -1, group, 1))
        for rng_seed in range(300):
            slots = logic.draw_knockout(entries, 4, rng_seed=rng_seed)["slots"]
            positions = {name(slot): index for index, slot in enumerate(slots)}
            for group in range(8):
                self.assertNotEqual(positions[f"G{group}A"] // 8, positions[f"G{group}B"] // 8)

    def test_third_qualifier_avoids_both_group_mates_quarters(self) -> None:
        entries = []
        for group in range(6):
            entries.append(entrant(f"G{group}_0", 3 - group if group < 3 else 0, group, 0))
            entries.extend(entrant(f"G{group}_{rank}", -1, group, rank) for rank in (1, 2))
        for rng_seed in range(200):
            slots = logic.draw_knockout(entries, 5, rng_seed=rng_seed)["slots"]
            positions = {name(slot): index for index, slot in enumerate(slots) if slot["kind"] != "bye"}
            for group in range(6):
                first, second, third = (positions[f"G{group}_{rank}"] for rank in range(3))
                self.assertNotEqual(first // 16, second // 16)
                self.assertNotIn(third // 8, (first // 8, second // 8))

    def test_qualified_entrants_carry_group_and_rank(self) -> None:
        people = [player(name) for name in "ABC"]
        games = [
            {"a": 0, "b": 1, "finished": True, "games": [[11, 5]]},
            {"a": 0, "b": 2, "finished": True, "games": [[11, 5]]},
            {"a": 1, "b": 2, "finished": True, "games": [[11, 5]]},
        ]
        group_data = {"groups": [{"id": 3, "qualifiers": 2, "participants": people, "matches": games}]}
        selected = Database._qualified_entrants({"entrants": []}, group_data)
        self.assertEqual([(item["group"], item["group_rank"]) for item in selected], [(3, 0), (3, 1)])


class TieRestartTests(unittest.TestCase):
    def test_remaining_pair_restarts_with_their_own_match(self) -> None:
        # A, B, C beat each other in a cycle; points ratio separates A, and
        # B, C stay tied on three-way totals. C beat B, so C ranks second.
        games = [
            {"a": 0, "b": 1, "finished": True, "games": [[11, 1], [9, 11], [0, 11]]},
            {"a": 0, "b": 2, "finished": True, "games": [[11, 1], [6, 11], [11, 3]]},
            {"a": 1, "b": 2, "finished": True, "games": [[11, 7], [9, 11], [1, 11]]},
        ]
        standings = logic.group_standings([player(name) for name in "ABC"], games, draw_order=[0, 1, 2])
        self.assertEqual(standings["order"], [0, 2, 1])
        self.assertTrue(all(row["draw_rank"] is None for row in standings["rows"]))


if __name__ == "__main__":
    unittest.main()
