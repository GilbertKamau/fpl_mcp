from collections import Counter

import pytest

from fpl_mcp import optimizer
from fpl_mcp.model import availability
from fpl_mcp.optimizer import SQUAD_SHAPE, XI_MAX, XI_MIN, _selling_price, estimate_free_transfers, solve


def test_selling_price_keeps_half_of_rises_rounded_down():
    assert _selling_price(5.0, 5.3) == 5.1
    assert _selling_price(5.0, 5.1) == 5.0
    assert _selling_price(5.0, 5.2) == 5.1
    assert _selling_price(5.0, 4.8) == 4.8  # falls are passed on in full


def test_free_transfers_bank_up_to_five():
    weeks = [{"event": g, "event_transfers": 0} for g in range(1, 10)]
    assert estimate_free_transfers({"current": weeks[:4], "chips": []}) == 4
    assert estimate_free_transfers({"current": weeks, "chips": []}) == 5


def test_free_transfers_used_and_kept_on_wildcard():
    weeks = [{"event": 1, "event_transfers": 0}, {"event": 2, "event_transfers": 1}, {"event": 3, "event_transfers": 9}]
    # GW2: had 1, used 1 -> 0, +1 = 1. GW3 wildcard: kept, +1 = 2.
    assert estimate_free_transfers({"current": weeks, "chips": [{"event": 3, "name": "wildcard"}]}) == 2


def test_availability():
    assert availability("a", None, 0) == 1.0
    assert availability("d", 75, 0) == 0.75
    assert availability("i", None, 0) == 0.0
    assert availability("i", None, 1) == 0.5
    assert availability("i", None, 3) == 1.0
    assert availability("u", None, 3) == 0.0


@pytest.fixture
def fake_pool(monkeypatch):
    """40 players over 10 clubs; expected points rise with id, price rises with id."""
    shape = ["GKP"] * 5 + ["DEF"] * 12 + ["MID"] * 13 + ["FWD"] * 10
    players = [
        {"id": i, "web_name": f"P{i}", "position": pos, "team_id": i % 10, "price": 4.0 + i * 0.1}
        for i, pos in enumerate(shape, start=1)
    ]
    ep = {p["id"]: {7: p["id"] / 10, 8: p["id"] / 10} for p in players}
    snap = {
        "players": players,
        "players_by_id": {p["id"]: p for p in players},
        "expected_points": {"by_player": ep, "gameweeks": [7, 8], "availability": {}, "created_at": None},
    }
    monkeypatch.setattr(optimizer, "snapshot", lambda: snap)
    return snap


def _check_rules(plan, snap, budget):
    by_id = snap["players_by_id"]
    assert len(plan.squad) == 15
    assert Counter(by_id[i]["position"] for i in plan.squad) == Counter(SQUAD_SHAPE)
    assert max(Counter(by_id[i]["team_id"] for i in plan.squad).values()) <= 3
    for gw, lu in plan.lineups.items():
        assert len(lu["xi"]) == 11 and len(lu["bench"]) == 4
        counts = Counter(by_id[i]["position"] for i in lu["xi"])
        for pos in SQUAD_SHAPE:
            assert XI_MIN[pos] <= counts[pos] <= XI_MAX[pos]
        assert lu["captain"] == max(lu["xi"], key=lambda i: snap["expected_points"]["by_player"][i][gw])


def test_optimal_squad_follows_fpl_rules(fake_pool):
    plan = solve([7, 8], budget=100.0)
    _check_rules(plan, fake_pool, 100.0)
    assert plan.cost <= 100.0


def test_transfers_take_the_free_one_and_avoid_bad_hits(fake_pool):
    first = solve([7, 8], budget=100.0)
    squad = {i: fake_pool["players_by_id"][i]["price"] for i in first.squad}
    # Make one outsider clearly better than anyone in the squad at a similar position.
    outsider = next(p for p in fake_pool["players"] if p["id"] not in squad and p["position"] == "MID")
    fake_pool["expected_points"]["by_player"][outsider["id"]] = {7: 20.0, 8: 20.0}
    bank = 100.0 - sum(squad.values())
    plan = solve([7, 8], budget=bank + sum(squad.values()), current=squad, free_transfers=1, max_transfers=3)
    _check_rules(plan, fake_pool, 100.0)
    assert outsider["id"] in plan.transfers_in
    assert len(plan.transfers_in) == len(plan.transfers_out)
    assert plan.hits == max(0, len(plan.transfers_in) - 1)
