"""Pure tournament draw, result, and progression rules.

Every public function accepts and returns JSON-compatible values. No function
reads the database or changes its input. ``rng_seed`` controls reproducibility
for the random portions of each draw.
"""

from __future__ import annotations

import copy
import random
import re
from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any


Participant = dict[str, Any]
Match = dict[str, Any]

_GROUP_TERM = re.compile(r"\s*(\d+)\s*\*\s*(\d+)\s*\(\s*(\d+)\s*\)\s*")
_COMPETITOR_KINDS = {"player", "pair", "team"}


def _undecided() -> Participant:
    """Create an independent placeholder for an unknown bracket position."""
    return {"kind": "undecided", "members": [], "team": ""}


def _bye() -> Participant:
    """Create a bye with the required sentinel seed level."""
    return {"kind": "bye", "members": [], "team": "", "seed": -1}


def _competitors(entrants: Sequence[Mapping[str, Any]]) -> list[Participant]:
    """Copy participant displays out of entrants."""
    participants: list[Participant] = []
    for entrant in entrants:
        if not isinstance(entrant, Mapping):
            raise ValueError("Each entrant must be an object.")
        participant = entrant.get("participant")
        if not isinstance(participant, Mapping):
            raise ValueError("Each entrant must contain a participant object.")
        if participant.get("kind") not in _COMPETITOR_KINDS:
            raise ValueError("Only players, pairs, and teams can enter a draw.")
        participants.append(copy.deepcopy(dict(participant)))
    return participants


def _seed_levels(entrants: Sequence[Mapping[str, Any]], *, allow_negative: bool = False) -> list[int]:
    """Read nonnegative group seeds or knockout seed categories down to -1."""
    seeds: list[int] = []
    for entrant in entrants:
        seed = entrant.get("seed", 0)
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < (-1 if allow_negative else 0):
            raise ValueError("Entrant seeds must be integers of at least " + ("-1." if allow_negative else "zero."))
        seeds.append(seed)
    return seeds


def parse_group_spec(expression: str) -> list[dict[str, int]]:
    """Parse ``count*size(qualifiers) + ...`` into group specifications.

    Counts and sizes must be positive, and each group must qualify between one
    and ``size`` competitors. Spaces around numbers and punctuation are allowed.
    ``ValueError`` is raised for malformed or impossible terms.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("Group specification cannot be empty.")
    specs: list[dict[str, int]] = []
    for raw_term in expression.split("+"):
        match = _GROUP_TERM.fullmatch(raw_term)
        if match is None:
            raise ValueError(f"Invalid group term: {raw_term.strip()!r}.")
        count, size, qualifiers = map(int, match.groups())
        if count < 1 or size < 1 or not 1 <= qualifiers <= size:
            raise ValueError("Group count, size, and qualifiers must be valid positive values.")
        specs.append({"count": count, "size": size, "qualifiers": qualifiers})
    return specs


def round_robin_matches(
    participants: Sequence[Mapping[str, Any]], *, id_prefix: str = "m"
) -> list[Match]:
    """Create one match per unordered pair of participant indices.

    ``a`` and ``b`` are zero-based positions in ``participants``. Match IDs are
    ``{id_prefix}-1``, ``{id_prefix}-2``, etc. The scores are added later.
    """
    if not id_prefix or not isinstance(id_prefix, str):
        raise ValueError("Match ID prefix must be a nonempty string.")
    matches: list[Match] = []
    for a in range(len(participants)):
        for b in range(a + 1, len(participants)):
            matches.append(
                {
                    "id": f"{id_prefix}-{len(matches) + 1}",
                    "a": a,
                    "b": b,
                    "finished": False,
                    "games": [],
                    "table": None,
                    "time": None,
                    "referee": "",
                }
            )
    return matches


def big_score(match: Mapping[str, Any]) -> list[int | str]:
    """Count completed games, marking a side with any walkover as ``wo``.

    A single walkover grants the other side a game win even when its numeric
    score is zero. A double walkover grants neither side a win. Incomplete
    games do not contribute; malformed or tied numeric games raise ValueError.
    """
    games = match.get("games", [])
    if not isinstance(games, list):
        raise ValueError("Match games must be a list.")
    wins = [0, 0]
    walkovers = [False, False]
    for game in games:
        if not isinstance(game, (list, tuple)) or len(game) != 2:
            raise ValueError("Each game must be a two-element score.")
        a, b = game
        if any(
            value is not None and value != "wo"
            and (not isinstance(value, int) or isinstance(value, bool) or value < 0)
            for value in (a, b)
        ):
            raise ValueError("Game scores must be nonnegative integers, wo, or null.")
        if a == "wo" and b is None:
            b = 0
        if b == "wo" and a is None:
            a = 0
        if a is None or b is None:
            continue
        if a == "wo" or b == "wo":
            walkovers[0] |= a == "wo"
            walkovers[1] |= b == "wo"
            if a != b:
                wins[0 if b == "wo" else 1] += 1
            continue
        if a == b:
            raise ValueError("Each completed game needs unequal nonnegative scores.")
        wins[0 if a > b else 1] += 1
    return ["wo" if walkovers[index] else wins[index] for index in range(2)]


def match_winner_side(match: Mapping[str, Any]) -> int | None:
    """Return a finished winner, or None for unfinished/double walkover.

    Any single-side walkover overrides the numeric game majority. A completed
    double walkover is a valid match without a winner.
    """
    if not match.get("finished", False):
        return None
    wins = big_score(match)
    if wins == ["wo", "wo"]:
        return None
    if wins[0] == "wo":
        return 1
    if wins[1] == "wo":
        return 0
    if not any(wins):
        raise ValueError("A finished match needs at least one completed game.")
    if wins[0] == wins[1]:
        raise ValueError("A finished match must have a game-win majority.")
    return 0 if wins[0] > wins[1] else 1


def rank_round_robin(
    participants: Sequence[Mapping[str, Any]], matches: Sequence[Mapping[str, Any]]
) -> list[int] | None:
    """Rank by match wins, game wins, and points scored, then head-to-head.

    Return ``None`` until every supplied match is finished. Competitors tied
    on all three totals are compared again using only matches among that tied
    set. A remaining tie is settled by display name, then input position.
    Incomplete games do not contribute games or points.
    """
    if any(not match.get("finished", False) for match in matches):
        return None
    # Store each match's contribution so exact-tie groups can be ranked from
    # their own matches without changing the overall totals.
    records: list[tuple[int, int, int | None, int, int, int, int]] = []
    for match in matches:
        a, b = match.get("a"), match.get("b")
        if (
            not isinstance(a, int)
            or isinstance(a, bool)
            or not isinstance(b, int)
            or isinstance(b, bool)
            or not 0 <= a < len(participants)
            or not 0 <= b < len(participants)
            or a == b
        ):
            raise ValueError("Round-robin matches must reference distinct participant indices.")
        side = match_winner_side(match)
        games_a = games_b = points_a = points_b = 0
        for score_a, score_b in match["games"]:
            if score_a == "wo" and score_b is None:
                score_b = 0
            if score_b == "wo" and score_a is None:
                score_a = 0
            if score_a is None or score_b is None:
                continue
            score_a = 0 if score_a == "wo" else score_a
            score_b = 0 if score_b == "wo" else score_b
            games_a += score_a > score_b
            games_b += score_b > score_a
            points_a += score_a
            points_b += score_b
        winner = None if side is None else (a if side == 0 else b)
        records.append((a, b, winner, games_a, games_b, points_a, points_b))

    def totals(eligible: set[int]) -> dict[int, tuple[int, int, int]]:
        scores = {index: [0, 0, 0] for index in eligible}
        for a, b, winner, games_a, games_b, points_a, points_b in records:
            if a not in eligible or b not in eligible:
                continue
            if winner is not None:
                scores[winner][0] += 1
            scores[a][1] += games_a
            scores[b][1] += games_b
            scores[a][2] += points_a
            scores[b][2] += points_b
        return {index: tuple(score) for index, score in scores.items()}

    overall = totals(set(range(len(participants))))
    tied: dict[tuple[int, int, int], list[int]] = {}
    for index in range(len(participants)):
        tied.setdefault(overall[index], []).append(index)

    ranking: list[int] = []
    for score in sorted(tied, reverse=True):
        members = tied[score]
        if len(members) > 1:
            direct = totals(set(members))
            members.sort(
                key=lambda index: (
                    -direct[index][0],
                    -direct[index][1],
                    -direct[index][2],
                    _display_name(participants[index]),
                    index,
                )
            )
        ranking.extend(members)
    return ranking


def group_standings(
    participants: Sequence[Mapping[str, Any]],
    matches: Sequence[Mapping[str, Any]],
    *,
    rng_seed: int | None = None,
    draw_order: Sequence[int] | None = None,
) -> dict[str, Any] | None:
    """Rank complete groups by points, mutual results, ratios, then a draw.

    A match win earns one point; a loss or double walkover earns none. A set
    tied on overall points compares mutual points, game ratio, and point ratio
    using only matches within that set. As soon as one metric separates
    anyone, every still-tied subset restarts from mutual points with only its
    own matches (ITTF 3.7.5.3). Trace fields appear only for players who
    reached that comparison and hold the innermost values computed for them.

    ``draw_order`` is a persisted permutation of all participant indices. If
    absent, a shuffled permutation is generated with ``rng_seed``. Only final
    ties use that permutation. The result is JSON-compatible and does not
    mutate either the source data or the draw permutation.
    """
    if any(not match.get("finished", False) for match in matches):
        return None
    count = len(participants)
    indices = list(range(count))
    if draw_order is None:
        permutation = indices[:]
        random.Random(rng_seed).shuffle(permutation)
    else:
        permutation = list(draw_order)
        if (
            len(permutation) != count
            or any(not isinstance(index, int) or isinstance(index, bool) for index in permutation)
            or set(permutation) != set(indices)
        ):
            raise ValueError("Ranking draw order must be a permutation of participant indices.")
    draw_positions = {index: position for position, index in enumerate(permutation)}
    rows = {
        index: {
            "index": index,
            "rank": None,
            "points": 0,
            "tied_points": None,
            "game_ratio": None,
            "point_ratio": None,
            "draw_rank": None,
        }
        for index in indices
    }
    records: list[tuple[int, int, int | None, int, int, int, int]] = []
    for match in matches:
        a, b = match.get("a"), match.get("b")
        if (
            not isinstance(a, int) or isinstance(a, bool)
            or not isinstance(b, int) or isinstance(b, bool)
            or not 0 <= a < count or not 0 <= b < count or a == b
        ):
            raise ValueError("Group matches must reference distinct participant indices.")
        side = match_winner_side(match)
        winner = None if side is None else (a if side == 0 else b)
        if winner is not None:
            rows[winner]["points"] += 1
        game_a = game_b = points_a = points_b = 0
        for score_a, score_b in match.get("games", []):
            if score_a == "wo" and score_b is None:
                score_b = 0
            if score_b == "wo" and score_a is None:
                score_a = 0
            if score_a is None or score_b is None:
                continue
            score_a = 0 if score_a == "wo" else score_a
            score_b = 0 if score_b == "wo" else score_b
            game_a += score_a > score_b
            game_b += score_b > score_a
            points_a += score_a
            points_b += score_b
        records.append((a, b, winner, game_a, game_b, points_a, points_b))

    def mutual_totals(members: list[int]) -> dict[int, list[int]]:
        # points, won games, lost games, points for, points against
        totals = {index: [0, 0, 0, 0, 0] for index in members}
        for a, b, winner, game_a, game_b, points_a, points_b in records:
            if a not in totals or b not in totals:
                continue
            if winner is not None:
                totals[winner][0] += 1
            for index, won, lost, scored, conceded in (
                (a, game_a, game_b, points_a, points_b),
                (b, game_b, game_a, points_b, points_a),
            ):
                totals[index][1] += won
                totals[index][2] += lost
                totals[index][3] += scored
                totals[index][4] += conceded
        return totals

    def ratio_key(won: int, lost: int) -> tuple[bool, Fraction]:
        # Positive / zero is infinity; zero / zero is zero.
        return (lost == 0 and won > 0, Fraction(won, lost) if lost else Fraction(0))

    def partition(members: list[int], values: Mapping[int, Any]) -> list[list[int]]:
        buckets: dict[Any, list[int]] = {}
        for index in members:
            buckets.setdefault(values[index], []).append(index)
        return [buckets[value] for value in sorted(buckets, reverse=True)]

    def resolve(members: list[int]) -> list[int]:
        # ITTF 3.7.5.3: once a criterion separates anyone, each still-tied
        # subset restarts from mutual points using only its own matches.
        if len(members) == 1:
            return members
        totals = mutual_totals(members)
        for field, won_position, lost_position in (
            ("tied_points", 0, None), ("game_ratio", 1, 2), ("point_ratio", 3, 4)
        ):
            values: dict[int, Any] = {}
            for index in members:
                if lost_position is None:
                    rows[index][field] = values[index] = totals[index][0]
                    continue
                won, lost = totals[index][won_position], totals[index][lost_position]
                rows[index][field] = {"won": won, "lost": lost}
                values[index] = ratio_key(won, lost)
            parts = partition(members, values)
            if len(parts) > 1:
                return [index for part in parts for index in resolve(part)]
        tied = sorted(members, key=draw_positions.__getitem__)
        for rank, index in enumerate(tied, 1):
            rows[index]["draw_rank"] = rank
        return tied

    global_groups = partition(indices, {index: rows[index]["points"] for index in indices})
    order: list[int] = []
    for members in global_groups:
        order.extend(resolve(members))
    for rank, index in enumerate(order, 1):
        rows[index]["rank"] = rank
    return {"order": order, "rows": [rows[index] for index in order]}


def draw_groups(
    entrants: Sequence[Mapping[str, Any]],
    group_specs: Sequence[Mapping[str, Any]],
    *,
    rng_seed: int | None = None,
) -> list[dict[str, Any]]:
    """Place the highest nonzero seeds in distinct groups, then fill randomly.

    Only the first seed for each group gets priority. Any additional seeds join
    the unassigned entrants in a fresh uniform shuffle over the free places.
    Equal seed levels are ordered randomly; ``rng_seed`` permits reproducible
    tests.
    """
    participants = _competitors(entrants)
    seeds = _seed_levels(entrants)
    total = 0
    checked_specs: list[tuple[int, int, int]] = []
    for spec in group_specs:
        try:
            count = spec["count"]
            size = spec["size"]
            qualifiers = spec["qualifiers"]
        except (KeyError, TypeError) as exc:
            raise ValueError("Each group specification needs count, size, and qualifiers.") from exc
        if (
            not all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in (count, size, qualifiers)
            )
            or count < 1
            or size < 1
            or not 1 <= qualifiers <= size
        ):
            raise ValueError("Invalid group count, size, or qualifier count.")
        checked_specs.append((count, size, qualifiers))
        total += count * size
    if total != len(participants):
        raise ValueError("Group sizes must equal the number of entrants.")
    rng = random.Random(rng_seed)
    randomized_indices = list(range(len(participants)))
    rng.shuffle(randomized_indices)
    groups: list[dict[str, Any]] = []
    for count, size, qualifiers in checked_specs:
        for _ in range(count):
            group_id = len(groups) + 1
            groups.append(
                {
                    "id": group_id,
                    "size": size,
                    "qualifiers": qualifiers,
                    "scheduled_time": "",
                    "scheduled_table": "",
                    "scheduled_referee": "",
                    "participants": [],
                }
            )
    seeded_indices = sorted(
        (index for index in randomized_indices if seeds[index] > 0),
        key=lambda index: -seeds[index],
    )
    priority_indices = seeded_indices[: len(groups)]
    priority_set = set(priority_indices)
    for group, index in zip(groups, priority_indices):
        group["participants"].append(participants[index])
    remaining_indices = [index for index in randomized_indices if index not in priority_set]
    # Reshuffle: when equal seeds straddle the priority cut, the leftover ones
    # sit late in ``randomized_indices`` and would otherwise favor later groups.
    rng.shuffle(remaining_indices)
    next_remaining = 0
    for group in groups:
        needed = group["size"] - len(group["participants"])
        group["participants"].extend(
            participants[index] for index in remaining_indices[next_remaining : next_remaining + needed]
        )
        next_remaining += needed
        group["matches"] = round_robin_matches(group["participants"], id_prefix=f"g{group['id']}-m")
    return groups


def _kind(participant: Mapping[str, Any]) -> str | None:
    """Read a bracket participant's role."""
    return participant.get("kind")


def knockout_winner(match: Mapping[str, Any]) -> Participant | None:
    """Return a knockout match winner, including automatic bye advancement.

    Unknown inputs and matches awaiting scores return ``None``. Both sides
    must be known competitors before a scored result can select a winner.
    """
    a, b = match.get("a"), match.get("b")
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        raise ValueError("Knockout matches need participant objects on both sides.")
    a_kind, b_kind = _kind(a), _kind(b)
    if a_kind == b_kind == "bye":
        return _bye()
    if a_kind == "bye" and b_kind in _COMPETITOR_KINDS:
        return copy.deepcopy(dict(b))
    if b_kind == "bye" and a_kind in _COMPETITOR_KINDS:
        return copy.deepcopy(dict(a))
    if a_kind not in _COMPETITOR_KINDS or b_kind not in _COMPETITOR_KINDS:
        return None
    side = match_winner_side(match)
    if side is not None:
        return copy.deepcopy(dict(a if side == 0 else b))
    return _bye() if match.get("finished") and big_score(match) == ["wo", "wo"] else None


def _knockout_loser(match: Mapping[str, Any]) -> Participant | None:
    """Return a decided loser's slot, including a bye in a semifinal."""
    a, b = match["a"], match["b"]
    if a.get("kind") == b.get("kind") == "bye":
        return _bye()
    if a.get("kind") == "bye" and b.get("kind") in _COMPETITOR_KINDS:
        return copy.deepcopy(dict(a))
    if b.get("kind") == "bye" and a.get("kind") in _COMPETITOR_KINDS:
        return copy.deepcopy(dict(b))
    if a.get("kind") not in _COMPETITOR_KINDS or b.get("kind") not in _COMPETITOR_KINDS:
        return None
    side = match_winner_side(match)
    if side is not None:
        return copy.deepcopy(dict(b if side == 0 else a))
    return _bye() if match.get("finished") and big_score(match) == ["wo", "wo"] else None


def _display_name(participant: Mapping[str, Any]) -> str:
    """Build the plain display value used to order tied fifth-place finishers."""
    if participant.get("kind") == "team":
        return str(participant.get("team", ""))
    return " / ".join(str(member) for member in participant.get("members", []))


def _knockout_results(refreshed: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Compute the top eight only after every playable knockout match resolves."""
    if not refreshed.get("drawn"):
        return None
    matches = refreshed["matches"]
    if any(knockout_winner(match) is None for match in matches):
        return None
    bronze = refreshed.get("bronze_match")
    if bronze is not None and knockout_winner(bronze) is None:
        return None

    results: list[dict[str, Any]] = []
    final = matches[-1]
    medalists = [(1, knockout_winner(final)), (2, _knockout_loser(final))]
    if bronze is not None:
        medalists.extend(((3, knockout_winner(bronze)), (4, _knockout_loser(bronze))))
    for rank, participant in medalists:
        if participant is not None and participant.get("kind") in _COMPETITOR_KINDS:
            results.append({"rank": rank, "participant": participant})

    if refreshed["rounds"] >= 3:
        quarterfinals = [match for match in matches if match["round"] == refreshed["rounds"] - 3]
        fifth_place = []
        for match in quarterfinals:
            loser = _knockout_loser(match)
            if loser is not None and loser.get("kind") in _COMPETITOR_KINDS:
                fifth_place.append(loser)
        for participant in sorted(fifth_place, key=_display_name):
            results.append({"rank": 5, "participant": participant})
    return results


def _new_knockout_match(
    round_index: int, match_index: int, a: Participant, b: Participant
) -> Match:
    """Create the canonical JSON structure for one bracket edge."""
    return {
        "id": f"r{round_index + 1}-m{match_index + 1}",
        "round": round_index,
        "index": match_index,
        "a": copy.deepcopy(a),
        "b": copy.deepcopy(b),
        "finished": False,
        "games": [],
        "table": None,
        "time": None,
        "referee": "",
    }


def _new_bronze_match(a: Participant, b: Participant) -> Match:
    return {
        "id": "bronze",
        "a": copy.deepcopy(a),
        "b": copy.deepcopy(b),
        "finished": False,
        "games": [],
        "table": None,
        "time": None,
        "referee": "",
    }


def refresh_knockout_bracket(stage: Mapping[str, Any]) -> dict[str, Any]:
    """Recalculate all knockout inputs and winners from the opening slots.

    Existing scores and match schedules survive when both inputs are the same.
    If an earlier result changes an input, that downstream result is cleared
    so it cannot award a win to a different participant. The original stage
    and its nested values remain unchanged.
    """
    try:
        rounds = stage["rounds"]
        slots = stage["slots"]
        old_matches = stage.get("matches", [])
    except (KeyError, TypeError) as exc:
        raise ValueError("Knockout stage needs rounds and slots.") from exc
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
        raise ValueError("Knockout rounds must be a positive integer.")
    if not isinstance(slots, list) or len(slots) != 2**rounds:
        raise ValueError("Knockout slots must fill the bracket capacity.")
    if any(not isinstance(slot, Mapping) for slot in slots):
        raise ValueError("Each knockout slot must be a participant object.")
    if not isinstance(old_matches, list):
        raise ValueError("Knockout matches must be a list.")
    previous: dict[tuple[int, int], Mapping[str, Any]] = {}
    for match in old_matches:
        if not isinstance(match, Mapping):
            raise ValueError("Each knockout match must be an object.")
        key = (match.get("round"), match.get("index"))
        if key in previous:
            raise ValueError("Knockout match positions must be unique.")
        previous[key] = match

    rebuilt: list[Match] = []
    prior_round: list[Match] = []
    for round_index in range(rounds):
        current_round: list[Match] = []
        count = 2 ** (rounds - round_index - 1)
        for match_index in range(count):
            if round_index == 0:
                a = dict(slots[2 * match_index])
                b = dict(slots[2 * match_index + 1])
            else:
                left = knockout_winner(prior_round[2 * match_index])
                right = knockout_winner(prior_round[2 * match_index + 1])
                a = left if left is not None else _undecided()
                b = right if right is not None else _undecided()
            match = _new_knockout_match(round_index, match_index, a, b)
            old = previous.get((round_index, match_index))
            if old is not None:
                match["table"] = copy.deepcopy(old.get("table"))
                match["time"] = copy.deepcopy(old.get("time"))
                match["referee"] = copy.deepcopy(old.get("referee", ""))
                if old.get("a") == a and old.get("b") == b:
                    match["finished"] = bool(old.get("finished", False))
                    match["games"] = copy.deepcopy(old.get("games", []))
            current_round.append(match)
            rebuilt.append(match)
        prior_round = current_round

    result = copy.deepcopy(dict(stage))
    result["matches"] = rebuilt
    if rounds >= 2:
        semifinals = [match for match in rebuilt if match["round"] == rounds - 2]
        a = _knockout_loser(semifinals[0]) or _undecided()
        b = _knockout_loser(semifinals[1]) or _undecided()
        bronze = _new_bronze_match(a, b)
        old_bronze = stage.get("bronze_match")
        if isinstance(old_bronze, Mapping):
            bronze["table"] = copy.deepcopy(old_bronze.get("table"))
            bronze["time"] = copy.deepcopy(old_bronze.get("time"))
            bronze["referee"] = copy.deepcopy(old_bronze.get("referee", ""))
            if old_bronze.get("a") == a and old_bronze.get("b") == b:
                bronze["finished"] = bool(old_bronze.get("finished", False))
                bronze["games"] = copy.deepcopy(old_bronze.get("games", []))
        result["bronze_match"] = bronze
    else:
        result["bronze_match"] = None
    result["results"] = _knockout_results(result)
    return result


def knockout_champion(stage: Mapping[str, Any]) -> Participant | None:
    """Return the final winner after bracket propagation, if known."""
    refreshed = refresh_knockout_bracket(stage)
    winner = knockout_winner(refreshed["matches"][-1])
    return winner if winner is not None and winner.get("kind") in _COMPETITOR_KINDS else None


def knockout_results(stage: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Return completed medal and fifth-place results, or ``None``."""
    return refresh_knockout_bracket(stage)["results"]


def _separated(
    entrant_index: int,
    slot: int,
    slot_indices: Sequence[int | None],
    groups: Sequence[Any],
    group_ranks: Sequence[int],
    capacity: int,
    max_level: int,
) -> bool:
    """Whether ``slot`` keeps an entrant out of its group-mates' regions."""
    group = groups[entrant_index]
    level = min(group_ranks[entrant_index].bit_length(), max_level)
    if group is None or level <= 0:
        return True
    span = capacity >> level
    return all(
        other is None or groups[other] != group or position // span != slot // span
        for position, other in enumerate(slot_indices)
    )


def draw_knockout(
    entrants: Sequence[Mapping[str, Any]], rounds: int, *, rng_seed: int | None = None
) -> dict[str, Any]:
    """Place seeds at ITTF positions, group winners in free matches, then others.

    Positive seed values rank descending, with equal values drawn randomly.
    Following ITTF seeding by ranking, rank 1 takes the top of the first half
    and rank 2 the bottom of the second half. Ranks 3-4 are drawn between the
    bottom of the first half and the top of the second half, ranks 5-8
    between the bottom places of the odd-numbered quarters and the top places
    of the even-numbered quarters, ranks 9-16 likewise for eighths, and so on
    while each opening match can hold at most one seed. Zero seeds are nonseed
    group winners and draw opening matches without a seed, taking the upper
    place in the first half and the lower place in the second half so the
    bracket stays mirror-symmetric. Byes are at most one per opening match and
    face seeds in order, then group winners, then random entrants. Everyone
    else draws from the remaining positions; entrants with ``group`` and
    ``group_rank`` keep away from group-mates (2nd in the other half, 3rd-4th
    in other quarters, and so on), relaxing one level at a time only if no
    arrangement exists. The same placement works for brackets smaller than
    eight positions.
    """
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 1:
        raise ValueError("Knockout rounds must be a positive integer.")
    participants = _competitors(entrants)
    seeds = _seed_levels(entrants, allow_negative=True)
    capacity = 2**rounds
    if not capacity // 2 < len(participants) <= capacity:
        raise ValueError("Entrant count must exceed half the bracket capacity and fit in it.")
    rng = random.Random(rng_seed)
    slot_indices: list[int | None] = [None] * capacity
    assigned: set[int] = set()
    randomized = list(range(len(participants)))
    rng.shuffle(randomized)
    seed_indices = sorted((index for index in randomized if seeds[index] > 0), key=lambda index: -seeds[index])

    def place(entrant_index: int, slot_index: int) -> None:
        slot_indices[slot_index] = entrant_index
        assigned.add(entrant_index)

    def open_matches() -> list[int]:
        # Mirror-symmetric "first" place of each opening match nobody occupies.
        return [
            position if position < capacity // 2 else position + 1
            for position in range(0, capacity, 2)
            if slot_indices[position] is None and slot_indices[position + 1] is None
        ]

    if seed_indices:
        place(seed_indices[0], 0)
    if len(seed_indices) > 1:
        place(seed_indices[1], capacity - 1)
    # Ranks 2**tier+1 .. 2**(tier+1) are drawn among the bottom places of the
    # odd-numbered and the top places of the even-numbered 1/2**tier regions.
    for tier in range(1, rounds - 1):
        tier_seeds = seed_indices[2**tier : 2 ** (tier + 1)]
        if not tier_seeds:
            break
        span = capacity >> tier
        positions = [slot for boundary in range(span, capacity, 2 * span) for slot in (boundary - 1, boundary)]
        for entrant_index, slot_index in zip(tier_seeds, rng.sample(positions, len(tier_seeds))):
            place(entrant_index, slot_index)
    first_slots = open_matches()
    rng.shuffle(first_slots)
    group_winners = [index for index in randomized if seeds[index] == 0]
    for entrant_index, slot_index in zip(group_winners, first_slots):
        place(entrant_index, slot_index)

    # Byes: at most one per opening match, given to seeds in order, then to
    # group winners in their drawn order, then in random empty matches.
    bye_count = capacity - len(participants)
    priority = [index for index in seed_indices + group_winners if index in assigned]
    holders = [slot_indices.index(index) for index in priority[:bye_count]]
    holders += rng.sample(open_matches(), bye_count - len(holders))
    bye_slots = {position ^ 1 for position in holders}

    # Remaining entrants avoid group-mates already in the bracket: the k-th
    # qualifier of a group (0-based) avoids earlier ones within the same
    # 1/2**ceil(log2(k + 1)) region, so 1st and 2nd sit in opposite halves.
    groups = [entrant.get("group") for entrant in entrants]
    group_ranks = [entrant.get("group_rank", 0) for entrant in entrants]
    remaining = [index for index in randomized if index not in assigned]
    remaining.sort(key=lambda index: (groups[index] is None, group_ranks[index]))
    open_slots = [
        position for position, index in enumerate(slot_indices)
        if index is None and position not in bye_slots
    ]
    base = slot_indices[:]
    for relax in range(rounds):
        for _ in range(200):
            slot_indices[:] = base
            free = open_slots[:]
            for entrant_index in remaining:
                allowed = [slot for slot in free if _separated(
                    entrant_index, slot, slot_indices, groups, group_ranks, capacity, rounds - 1 - relax
                )]
                if not allowed:
                    break
                choice = rng.choice(allowed)
                slot_indices[choice] = entrant_index
                free.remove(choice)
            else:
                break
        else:
            continue
        break
    assigned.update(remaining)
    slots = [_bye() if index is None else participants[index] for index in slot_indices]
    stage: dict[str, Any] = {"drawn": True, "rounds": rounds, "slots": slots, "matches": []}
    return refresh_knockout_bracket(stage)
