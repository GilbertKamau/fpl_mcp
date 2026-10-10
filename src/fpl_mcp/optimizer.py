# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Gilbert Kamau
"""Squad, transfer, captain and chip decisions as integer programs (PuLP + HiGHS).

Everything is driven by the model's expected points (the predictions table). Points for
gameweek k ahead are weighted by DECAY**k because predictions further out are less reliable.
"""

from dataclasses import dataclass, field

import pulp

from . import api
from .queries import NotFound, availability_alerts, snapshot, team_map

SQUAD_SHAPE = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
XI_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
XI_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_CLUB = 3
HIT_COST = 4
MAX_BANKED_TRANSFERS = 5
BENCH_WEIGHT = 0.1  # bench points matter a little (auto-subs)
DECAY = 0.85


# --- Expected points ----------------------------------------------------------------------


def planning_gameweeks(count: int) -> list[int]:
    gws = sorted(snapshot()["expected_points"]["gameweeks"])
    if not gws:
        raise NotFound("No predictions yet. Run `fpl-model all` (train + predict) first.")
    return gws[: max(1, min(count, len(gws)))]


def expected_points(player_id: int, gw: int) -> float:
    return snapshot()["expected_points"]["by_player"].get(player_id, {}).get(gw, 0.0)


# --- Core solver -------------------------------------------------------------------------------


@dataclass
class Plan:
    gameweeks: list[int]
    squad: list[int]
    lineups: dict[int, dict]  # gw -> {"xi": [...], "bench": [...], "captain": id, "vice": id, "points": float}
    transfers_in: list[int] = field(default_factory=list)
    transfers_out: list[int] = field(default_factory=list)
    hits: int = 0
    cost: float = 0.0
    objective: float = 0.0


def solve(
    gameweeks: list[int],
    budget: float,
    current: dict[int, float] | None = None,  # player id -> selling price
    free_transfers: int = 1,
    max_transfers: int | None = None,
) -> Plan:
    snap = snapshot()
    pool = snap["players"]
    current = current or {}

    # Skip players the model expects nothing from, unless they are already in the squad.
    candidates = {
        p["id"]: p
        for p in pool
        if p["position"] in SQUAD_SHAPE
        and (p["id"] in current or sum(expected_points(p["id"], g) for g in gameweeks) > 0.2)
    }
    ids = list(candidates)
    weights = {g: DECAY**k for k, g in enumerate(gameweeks)}
    ep = {(i, g): expected_points(i, g) for i in ids for g in gameweeks}

    prob = pulp.LpProblem("fpl", pulp.LpMaximize)
    x = prob.add_variable_dict("squad", ids, cat="Binary")
    s = prob.add_variable_dict("start", (ids, gameweeks), cat="Binary")
    c = prob.add_variable_dict("captain", (ids, gameweeks), cat="Binary")

    for pos, n in SQUAD_SHAPE.items():
        prob += pulp.lpSum(x[i] for i in ids if candidates[i]["position"] == pos) == n
    for club in {p["team_id"] for p in candidates.values()}:
        prob += pulp.lpSum(x[i] for i in ids if candidates[i]["team_id"] == club) <= MAX_PER_CLUB
    for g in gameweeks:
        prob += pulp.lpSum(s[i, g] for i in ids) == 11
        prob += pulp.lpSum(c[i, g] for i in ids) == 1
        for pos in SQUAD_SHAPE:
            starters = pulp.lpSum(s[i, g] for i in ids if candidates[i]["position"] == pos)
            prob += starters >= XI_MIN[pos]
            prob += starters <= XI_MAX[pos]
        for i in ids:
            prob += s[i, g] <= x[i]
            prob += c[i, g] <= s[i, g]

    hits = 0
    if current:
        t_in = prob.add_variable_dict("in", [i for i in ids if i not in current], cat="Binary")
        t_out = prob.add_variable_dict("out", [i for i in ids if i in current], cat="Binary")
        for i in ids:
            # Written as a difference: in PuLP 4, `var == var` compares objects instead of building a constraint.
            if i in current:
                prob += x[i] + t_out[i] == 1
            else:
                prob += x[i] - t_in[i] == 0
        n_transfers = pulp.lpSum(t_in.values())
        hits = prob.add_variable("hits", lowBound=0, cat="Integer")
        prob += hits >= n_transfers - free_transfers
        if max_transfers is not None:
            prob += n_transfers <= max_transfers
        spend = pulp.lpSum(candidates[i]["price"] * t_in[i] for i in t_in) + pulp.lpSum(
            current[i] * (1 - t_out[i]) for i in t_out
        )
    else:
        spend = pulp.lpSum(candidates[i]["price"] * x[i] for i in ids)
    prob += spend <= budget + 1e-6

    prob += (
        pulp.lpSum(weights[g] * ep[i, g] * (s[i, g] + c[i, g]) for i in ids for g in gameweeks)
        + BENCH_WEIGHT * pulp.lpSum(weights[g] * ep[i, g] * (x[i] - s[i, g]) for i in ids for g in gameweeks)
        - HIT_COST * hits
    )

    result = prob.solve(pulp.HiGHS(msg=False, timeLimit=60))
    if result.status != pulp.LpSolveStatus.Optimal:
        raise NotFound(f"No valid squad found ({result.status.name}). Is the budget too low?")

    squad = [i for i in ids if x[i].value() > 0.5]
    lineups = {}
    for g in gameweeks:
        xi = sorted((i for i in squad if s[i, g].value() > 0.5), key=lambda i: -ep[i, g])
        captain = next(i for i in squad if c[i, g].value() > 0.5)
        vice = next(i for i in xi if i != captain)
        bench = [i for i in squad if i not in xi]
        # Bench order: goalkeeper last, outfielders by expected points.
        bench.sort(key=lambda i: (candidates[i]["position"] == "GKP", -ep[i, g]))
        points = sum(ep[i, g] for i in xi) + ep[captain, g]
        lineups[g] = {"xi": xi, "bench": bench, "captain": captain, "vice": vice, "points": round(points, 2)}

    plan = Plan(gameweeks=gameweeks, squad=squad, lineups=lineups, objective=round(result.objective, 2))
    if current:
        plan.transfers_in = [i for i in squad if i not in current]
        plan.transfers_out = [i for i in current if i not in squad]
        plan.hits = int(round(hits.value()))
        plan.cost = round(
            sum(candidates[i]["price"] for i in plan.transfers_in) - sum(current[i] for i in plan.transfers_out), 1
        )
    else:
        plan.cost = round(sum(candidates[i]["price"] for i in squad), 1)
    return plan


# --- Manager state (public endpoints, no login) ---------------------------------------------


@dataclass
class Manager:
    team_id: int
    name: str
    squad: dict[int, float]  # player id -> estimated selling price
    bank: float
    free_transfers: int
    chips_used: list[dict]
    gameweek: int  # the gameweek the picks are from
    notes: list[str]


def _selling_price(purchase: float, now: float) -> float:
    """FPL keeps half of any rise (rounded down to £0.1m); falls are passed on in full."""
    if now <= purchase:
        return now
    return round(purchase + ((round(now * 10) - round(purchase * 10)) // 2) / 10, 1)


def estimate_free_transfers(history: dict) -> int:
    chips = {c["event"]: c["name"] for c in history.get("chips", [])}
    weeks = sorted(history.get("current", []), key=lambda r: r["event"])
    if not weeks:
        return 1
    ft = 1  # available for the week after the manager's first gameweek
    for week in weeks[1:]:
        if chips.get(week["event"]) not in ("wildcard", "freehit"):
            ft = max(ft - week["event_transfers"], 0)
        ft = min(ft + 1, MAX_BANKED_TRANSFERS)
    return ft


def load_manager(team_id: int) -> Manager:
    profile = api.entry(team_id)
    gw = profile.get("current_event")
    if not gw:
        raise NotFound("This team has not played a gameweek yet.")
    notes = []
    picks = api.entry_picks(team_id, gw)
    if picks.get("active_chip") == "freehit" and gw > 1:
        notes.append(f"Free Hit was played in GW{gw}, so the squad reverts to the GW{gw - 1} team.")
        picks = api.entry_picks(team_id, gw - 1) | {"entry_history": picks["entry_history"]}
    history = api.entry_history(team_id)
    transfers = api.get_json(f"entry/{team_id}/transfers/")
    start_price = {e["id"]: (e["now_cost"] - e["cost_change_start"]) / 10 for e in api.bootstrap()["elements"]}

    by_id = snapshot()["players_by_id"]
    bought_at = {}
    for t in sorted(transfers, key=lambda t: t["time"]):
        bought_at[t["element_in"]] = t["element_in_cost"] / 10
    squad = {}
    for pick in picks["picks"]:
        pid = pick["element"]
        now = by_id[pid]["price"] if pid in by_id else start_price.get(pid, 0)
        squad[pid] = _selling_price(bought_at.get(pid, start_price.get(pid, now)), now)
    notes.append(
        "Selling prices are estimated from public transfer history; check them on the FPL site before big moves."
    )
    notes.append(f"Transfers made after the GW{gw} deadline are not public yet and are not included.")
    return Manager(
        team_id=team_id,
        name=profile.get("name", ""),
        squad=squad,
        bank=(picks["entry_history"].get("bank") or 0) / 10,
        free_transfers=estimate_free_transfers(history),
        chips_used=history.get("chips", []),
        gameweek=gw,
        notes=notes,
    )


# --- Presentation ------------------------------------------------------------------------------


def _name(pid: int) -> str:
    p = snapshot()["players_by_id"].get(pid)
    if not p:
        return f"#{pid}"
    return f"{p['web_name']} ({team_map().get(p['team_id'], {}).get('short_name', '?')})"


def _player_line(pid: int, gw: int) -> dict:
    p = snapshot()["players_by_id"][pid]
    return {
        "name": p["web_name"],
        "team": team_map().get(p["team_id"], {}).get("short_name"),
        "position": p["position"],
        "price": p["price"],
        "expected_points": round(expected_points(pid, gw), 2),
    }


def describe_lineup(plan: Plan, gw: int) -> dict:
    lu = plan.lineups[gw]
    return {
        "gameweek": gw,
        "expected_points": lu["points"],
        "captain": _name(lu["captain"]),
        "vice_captain": _name(lu["vice"]),
        "starting_xi": [_player_line(i, gw) for i in lu["xi"]],
        "bench": [_player_line(i, gw) for i in lu["bench"]],
    }


# --- Tool entry points --------------------------------------------------------------------------


def optimal_squad(budget: float = 100.0, gameweeks: int = 3) -> dict:
    gws = planning_gameweeks(gameweeks)
    plan = solve(gws, budget)
    return {
        "gameweeks": gws,
        "squad_cost": plan.cost,
        "money_left": round(budget - plan.cost, 1),
        "expected_points_by_gameweek": {g: plan.lineups[g]["points"] for g in gws},
        "first_gameweek": describe_lineup(plan, gws[0]),
    }


def suggest_transfers(
    team_id: int, free_transfers: int | None = None, max_transfers: int = 2, gameweeks: int = 3
) -> dict:
    m = load_manager(team_id)
    ft = m.free_transfers if free_transfers is None else free_transfers
    gws = planning_gameweeks(gameweeks)
    budget = m.bank + sum(m.squad.values())
    hold = solve(gws, budget, current=m.squad, free_transfers=ft, max_transfers=0)
    plan = solve(gws, budget, current=m.squad, free_transfers=ft, max_transfers=max_transfers)

    gain = round(plan.objective - hold.objective, 2)
    moves = [
        {
            "out": _name(o),
            "selling_price": m.squad[o],
            "in": _name(i),
            "price": snapshot()["players_by_id"][i]["price"],
        }
        for o, i in zip(
            sorted(plan.transfers_out, key=lambda i: snapshot()["players_by_id"][i]["position"]),
            sorted(plan.transfers_in, key=lambda i: snapshot()["players_by_id"][i]["position"]),
        )
    ]
    return {
        "team": m.name,
        "planning_gameweeks": gws,
        "free_transfers_used_in_plan": ft,
        "free_transfers_estimated": m.free_transfers,
        "bank": m.bank,
        "recommendation": "roll the transfer" if not moves else f"make {len(moves)} transfer(s)",
        "transfers": moves,
        "points_hits": plan.hits * HIT_COST,
        "bank_after": round(m.bank - plan.cost, 1),
        "expected_gain_over_holding": gain,
        "expected_points_by_gameweek": {g: plan.lineups[g]["points"] for g in gws},
        "next_gameweek_lineup": describe_lineup(plan, gws[0]),
        # Checked on the squad after the suggested transfers, with roles from the suggested lineup.
        "availability_alerts": (alerts := availability_alerts(plan.squad, set(plan.lineups[gws[0]]["xi"]))),
        "notes": m.notes
        + ["Gain is weighted points over the planning window, after hits (later weeks count a bit less)."]
        + (
            [
                "The plan keeps unavailable bench players because bench points count for little, so replacing "
                "them costs a transfer for a small expected gain. Worth doing with a spare free transfer, or "
                "fold it into a Wildcard."
            ]
            if any(a["severity"] == "out" and a.get("role") == "bench" for a in alerts)
            else []
        ),
    }


def pick_captain(team_id: int) -> dict:
    m = load_manager(team_id)
    gw = planning_gameweeks(1)[0]
    ranked = sorted(m.squad, key=lambda i: -expected_points(i, gw))
    plan = solve([gw], m.bank + sum(m.squad.values()), current=m.squad, max_transfers=0)
    lu = plan.lineups[gw]
    return {
        "gameweek": gw,
        "captain": _name(lu["captain"]),
        "vice_captain": _name(lu["vice"]),
        "options": [{"name": _name(i), "expected_points": round(expected_points(i, gw), 2)} for i in ranked[:5]],
        "note": "Assumes your current squad (no transfers). Use suggest_transfers first if you plan moves.",
    }


CHIP_LABELS = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}
# Rules of thumb for when a chip is worth playing now rather than saving it.
CHIP_THRESHOLDS = {"3xc": 9.0, "bboost": 14.0, "freehit": 15.0, "wildcard": 20.0}


def chip_advice(team_id: int, gameweeks: int = 5) -> dict:
    m = load_manager(team_id)
    gws = planning_gameweeks(gameweeks)
    next_gw = gws[0]
    budget = m.bank + sum(m.squad.values())

    # Which chips can still be played this half of the season?
    windows = [c for c in api.bootstrap()["chips"] if c["start_event"] <= next_gw <= c["stop_event"]]
    used = {(c["name"], c["event"]) for c in m.chips_used}
    available = {}
    for w in windows:
        spent = any(name == w["name"] and w["start_event"] <= ev <= w["stop_event"] for name, ev in used)
        if not spent:
            available[w["name"]] = w["stop_event"]

    hold = solve(gws, budget, current=m.squad, free_transfers=m.free_transfers, max_transfers=0)
    per_week = {}
    for g in gws:
        lu = hold.lineups[g]
        per_week[g] = {
            "3xc": round(expected_points(lu["captain"], g), 2),
            "bboost": round(sum(expected_points(i, g) for i in lu["bench"]), 2),
        }

    advice = []
    for chip, stop in available.items():
        entry = {"chip": CHIP_LABELS.get(chip, chip), "must_use_by": f"GW{stop}"}
        if chip in ("3xc", "bboost"):
            values = {g: per_week[g][chip] for g in gws}
            best = max(values, key=values.get)
            entry["expected_gain_next_gw"] = values[next_gw]
            entry["best_gameweek_in_window"] = f"GW{best} (+{values[best]})"
            play_now = best == next_gw and values[next_gw] >= CHIP_THRESHOLDS[chip]
        elif chip == "freehit":
            fh = solve([next_gw], budget)
            gain = round(fh.lineups[next_gw]["points"] - hold.lineups[next_gw]["points"], 2)
            entry["expected_gain_next_gw"] = gain
            play_now = gain >= CHIP_THRESHOLDS[chip]
        else:  # wildcard: best unrestricted squad vs best plan with normal transfers
            wc = solve(gws, budget)
            normal = solve(gws, budget, current=m.squad, free_transfers=m.free_transfers, max_transfers=3)
            gain = round(wc.objective - normal.objective, 2)
            entry["expected_gain_over_window"] = gain
            play_now = gain >= CHIP_THRESHOLDS[chip]
        if stop - next_gw <= 1 and not play_now:
            entry["warning"] = f"Expires after GW{stop}; play it before then or lose it."
        entry["recommendation"] = "play now" if play_now else "save"
        advice.append(entry)

    return {
        "team": m.name,
        "next_gameweek": next_gw,
        "available_chips": advice,
        "used_chips": [{"chip": CHIP_LABELS.get(c["name"], c["name"]), "gameweek": c["event"]} for c in m.chips_used],
        "notes": m.notes
        + [
            "Gains are model expected points. 'Play now' uses rules of thumb "
            f"(Triple Captain >= {CHIP_THRESHOLDS['3xc']}, Bench Boost >= {CHIP_THRESHOLDS['bboost']}, "
            f"Free Hit >= {CHIP_THRESHOLDS['freehit']}, Wildcard >= {CHIP_THRESHOLDS['wildcard']} over the window)."
        ],
    }
