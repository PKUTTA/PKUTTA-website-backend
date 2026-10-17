"""Walkovers, auditable group rankings, and balanced knockout seed draws."""

from __future__ import annotations

import copy
import unittest

from backend import logic


def player(name: str) -> dict:
    return {"kind": "player", "members": [name], "team": "北大"}


def entrant(name: str, seed: int) -> dict:
    return {"participant": player(name), "seed": seed}


def match(a: int, b: int, games: list) -> dict:
    return {"a": a, "b": b, "finished": True, "games": games}


class WalkoverLogicTests(unittest.TestCase):
    def test_single_walkover_overrides_numeric_game_majority(self) -> None:
        result = {"finished": True, "games": [[11, 5], [11, 5], ["wo", 0]]}
        self.assertEqual(logic.big_score(result), ["wo", 1])
        self.assertEqual(logic.match_winner_side(result), 1)
        self.assertEqual(logic.big_score({"games": [[0, "wo"], [None, "wo"]]}), [2, "wo"])
        self.assertEqual(logic.match_winner_side({"finished": True, "games": [[None, "wo"]]}), 0)

    def test_double_walkover_is_complete_without_a_winner(self) -> None:
        result = {"finished": True, "games": [["wo", "wo"]]}
        self.assertEqual(logic.big_score(result), ["wo", "wo"])
        self.assertIsNone(logic.match_winner_side(result))
        # A walkover on each side also marks both sides as forfeited.
        self.assertEqual(logic.big_score({"games": [[0, "wo"], ["wo", 11]]}), ["wo", "wo"])

    def test_partial_games_and_bad_scores(self) -> None:
        self.assertEqual(logic.big_score({"games": [[11, None], [None, None], [11, 8]]}), [1, 0])
        for games in ([[11, 11]], [[True, 11]], [["WO", 11]], [[-1, 11]], [["wo", "bad"]]):
            with self.subTest(games=games), self.assertRaises(ValueError):
                logic.big_score({"games": games})
        with self.assertRaises(ValueError):
            logic.match_winner_side({"finished": True, "games": [[11, None]]})

    def test_legacy_round_robin_accepts_walkovers(self) -> None:
        people = [player("A"), player("B")]
        self.assertEqual(logic.rank_round_robin(people, [match(0, 1, [[0, "wo"]])]), [0, 1])
        self.assertEqual(logic.rank_round_robin(people, [match(0, 1, [["wo", "wo"]])]), [0, 1])

    def test_double_walkover_propagates_bye_and_finishes_results(self) -> None:
        stage = logic.draw_knockout([entrant(name, seed) for name, seed in (("A", 4), ("B", 3), ("C", 2), ("D", 1))], 2, rng_seed=2)
        stage["matches"][0].update(finished=True, games=[["wo", "wo"]])
        stage["matches"][1].update(finished=True, games=[[11, 5]])
        refreshed = logic.refresh_knockout_bracket(stage)
        self.assertEqual(refreshed["matches"][-1]["a"]["kind"], "bye")
        self.assertEqual(refreshed["bronze_match"]["a"]["kind"], "bye")
        self.assertIsNotNone(refreshed["results"])
        self.assertEqual([row["rank"] for row in refreshed["results"]], [1, 3])
        self.assertIsNotNone(logic.knockout_champion(refreshed))
        stage["matches"][1].update(finished=True, games=[["wo", "wo"]])
        refreshed = logic.refresh_knockout_bracket(stage)
        self.assertEqual(refreshed["results"], [])
        self.assertIsNone(logic.knockout_champion(refreshed))

    def test_single_walkover_loser_can_still_play_bronze(self) -> None:
        stage = logic.draw_knockout([entrant(name, 0) for name in "ABCD"], 2, rng_seed=1)
        stage["matches"][0].update(finished=True, games=[[0, "wo"]])
        stage["matches"][1].update(finished=True, games=[[11, 4]])
        refreshed = logic.refresh_knockout_bracket(stage)
        self.assertEqual(refreshed["bronze_match"]["a"], stage["matches"][0]["b"])
        self.assertIsNone(refreshed["results"])

    def test_referee_defaults_and_preservation_after_input_change(self) -> None:
        groups = logic.draw_groups([entrant(name, 0) for name in "AB"], [{"count": 1, "size": 2, "qualifiers": 1}], rng_seed=1)
        self.assertEqual(groups[0]["scheduled_referee"], "")
        self.assertEqual(groups[0]["matches"][0]["referee"], "")
        stage = logic.draw_knockout([entrant(name, 0) for name in "ABCD"], 2, rng_seed=1)
        final = stage["matches"][-1]
        final.update(referee="张裁判", time="10:00", table="2")
        stage["bronze_match"]["referee"] = "李裁判"
        stage["matches"][0].update(finished=True, games=[[11, 5]])
        stage["matches"][1].update(finished=True, games=[[11, 5]])
        refreshed = logic.refresh_knockout_bracket(stage)
        self.assertEqual(refreshed["matches"][-1]["referee"], "张裁判")
        self.assertEqual(refreshed["bronze_match"]["referee"], "李裁判")
        self.assertEqual(refreshed["matches"][-1]["games"], [])


class GroupStandingsTests(unittest.TestCase):
    def test_waits_for_completion_and_records_only_needed_columns(self) -> None:
        people = [player(name) for name in "AB"]
        self.assertIsNone(logic.group_standings(people, [{"a": 0, "b": 1, "finished": False, "games": []}]))
        standings = logic.group_standings(people, [match(0, 1, [[11, 5]])])
        self.assertEqual(standings["order"], [0, 1])
        self.assertEqual([row["points"] for row in standings["rows"]], [1, 0])
        self.assertTrue(all(row[field] is None for row in standings["rows"] for field in ("tied_points", "game_ratio", "point_ratio", "draw_rank")))

    def test_mutual_points_precede_ratios(self) -> None:
        games = [match(0, 1, [[11, 5]]), match(0, 2, [[11, 5]]), match(0, 3, [[5, 11]]),
                 match(1, 2, [[11, 5]]), match(1, 3, [[11, 5]]), match(2, 3, [[11, 5]])]
        standings = logic.group_standings([player(name) for name in "ABCD"], games)
        self.assertEqual(standings["order"], [0, 1, 2, 3])
        self.assertEqual([row["tied_points"] for row in standings["rows"]], [1, 0, 1, 0])
        self.assertTrue(all(row["game_ratio"] is None for row in standings["rows"]))

    def test_mutual_game_ratio_uses_exact_fractions(self) -> None:
        games = [match(0, 1, [[11, 5], [11, 5]]), match(1, 2, [[11, 5]]), match(0, 2, [[5, 11]])]
        standings = logic.group_standings([player(name) for name in "ABC"], games)
        self.assertEqual(standings["order"], [0, 2, 1])
        self.assertEqual([row["game_ratio"] for row in standings["rows"]], [{"won": 2, "lost": 1}, {"won": 1, "lost": 1}, {"won": 1, "lost": 2}])
        self.assertTrue(all(row["point_ratio"] is None for row in standings["rows"]))

    def test_point_ratio_and_trace_exclude_untied_players(self) -> None:
        games = [match(0, 1, [[11, 1]]), match(1, 2, [[11, 5]]), match(0, 2, [[9, 11]])]
        games.extend(match(index, 3, [[11, index]]) for index in range(3))
        standings = logic.group_standings([player(name) for name in "ABCD"], games)
        self.assertEqual(standings["order"], [0, 2, 1, 3])
        self.assertEqual([row["point_ratio"] for row in standings["rows"][:3]], [{"won": 20, "lost": 12}, {"won": 16, "lost": 20}, {"won": 12, "lost": 16}])
        self.assertTrue(all(standings["rows"][-1][field] is None for field in ("tied_points", "game_ratio", "point_ratio", "draw_rank")))

    def test_persisted_draw_order_and_no_mutation(self) -> None:
        people = [player(name) for name in "ABC"]
        games = [match(0, 1, [[11, 5]]), match(1, 2, [[11, 5]]), match(0, 2, [[5, 11]])]
        original = copy.deepcopy(games)
        permutation = [2, 0, 1]
        standings = logic.group_standings(people, games, draw_order=permutation)
        self.assertEqual(standings["order"], permutation)
        self.assertEqual([row["draw_rank"] for row in standings["rows"]], [1, 2, 3])
        self.assertEqual(games, original)
        self.assertEqual(permutation, [2, 0, 1])
        self.assertEqual(logic.group_standings(people, games, rng_seed=10), logic.group_standings(people, games, rng_seed=10))
        with self.assertRaises(ValueError):
            logic.group_standings(people, games, draw_order=[0, 0, 1])

    def test_walkovers_score_match_points_and_zero_in_ratios(self) -> None:
        people = [player(name) for name in "ABC"]
        games = [match(0, 1, [[0, "wo"]]), match(1, 2, [[11, "wo"]]), match(0, 2, [[0, 11]])]
        standings = logic.group_standings(people, games)
        self.assertEqual(standings["order"], [1, 2, 0])
        self.assertEqual([row["points"] for row in standings["rows"]], [1, 1, 1])
        self.assertEqual([row["game_ratio"] for row in standings["rows"]], [{"won": 1, "lost": 0}, {"won": 1, "lost": 1}, {"won": 0, "lost": 1}])
        double = logic.group_standings(people[:2], [match(0, 1, [["wo", "wo"]])], draw_order=[1, 0])
        self.assertEqual([row["points"] for row in double["rows"]], [0, 0])
        self.assertEqual([row["game_ratio"] for row in double["rows"]], [{"won": 0, "lost": 0}, {"won": 0, "lost": 0}])
        self.assertEqual(double["order"], [1, 0])


class BalancedKnockoutDrawTests(unittest.TestCase):
    def test_seed_tiers_occupy_distinct_regions(self) -> None:
        entries = [entrant(str(index), 17 - index) for index in range(1, 17)]
        original = copy.deepcopy(entries)
        for rng_seed in range(12):
            stage = logic.draw_knockout(entries, 4, rng_seed=rng_seed)
            positions = {slot["members"][0]: position for position, slot in enumerate(stage["slots"])}
            self.assertEqual(positions["1"], 0)
            self.assertEqual(positions["2"], 15)
            self.assertEqual({positions["3"], positions["4"]}, {7, 8})
            self.assertEqual({positions[str(index)] for index in range(5, 9)}, {3, 4, 11, 12})
            self.assertEqual({positions[str(index)] // 4 for index in range(1, 5)}, {0, 1, 2, 3})
            self.assertEqual({positions[str(index)] // 2 for index in range(1, 9)}, set(range(8)))
        self.assertEqual(entries, original)
        self.assertEqual(logic.draw_knockout(entries, 4, rng_seed=4), logic.draw_knockout(entries, 4, rng_seed=4))

    def test_zero_group_winners_take_matches_without_seeds(self) -> None:
        entries = [entrant("seed1", 2), entrant("seed2", 1)]
        entries.extend(entrant(f"winner{index}", 0) for index in range(6))
        entries.extend(entrant(f"other{index}", -1) for index in range(4))
        stage = logic.draw_knockout(entries, 4, rng_seed=3)
        leading = lambda slot: slot["kind"] == "player" and slot["members"][0].startswith(("seed", "winner"))
        for position in range(0, 16, 2):
            self.assertEqual(sum(map(leading, stage["slots"][position:position + 2])), 1)
        # Group winners mirror across the halves: upper place on top, lower place below.
        for position, slot in enumerate(stage["slots"]):
            if slot["kind"] == "player" and slot["members"][0].startswith("winner"):
                self.assertEqual(position % 2, int(position >= 8))
        self.assertEqual(sum(slot["kind"] == "bye" for slot in stage["slots"]), 4)

    def test_small_brackets_seed_one_and_validate_categories(self) -> None:
        entries = [entrant("A", 1), entrant("B", 0), entrant("C", -1)]
        stage = logic.draw_knockout(entries, 2, rng_seed=3)
        self.assertEqual(stage["slots"][0], player("A"))
        self.assertEqual(stage["slots"][3], player("B"))
        two = logic.draw_knockout([entrant("A", 2), entrant("B", 1)], 1, rng_seed=2)
        self.assertEqual(two["slots"][0], player("A"))
        for seed in (-2, True, 1.5):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                logic.draw_knockout([entrant("A", seed), entrant("B", 0)], 1)


if __name__ == "__main__":
    unittest.main()
