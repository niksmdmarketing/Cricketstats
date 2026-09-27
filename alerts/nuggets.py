"""Stat nuggets: social-first context for performances in major international matches.

Every fact is tagged with an ANGLE. Angle weights come from a TypeSafe (Jev) ranking of
which kinds of stat get shared and talked about (run 27 Sep 2026):
  star milestone 22% · star slump 20% · passes a legend 17% · star duck 15%   (round 1)
  only-Kth-from-nation 23% · most v opponent 14% · maiden ton/five-for 9%       (round 2)
  venue records, highest total/chase, small milestones, bare career counts ~1-2%
A fact with weight >= LEAD_MIN can lead its own alert; lower ones are supporting context,
and performances with no lead-worthy fact go into a quiet end-of-match digest.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"

WEIGHT = {
    "star_milestone": 100, "star_slump": 95, "passes_legend": 90, "star_duck": 88,
    "only_kth_from_nation": 85, "most_vs_opponent": 75, "maiden": 72,
    "best_by_nation_vs_opp": 62, "star_expensive": 62, "drought_ended": 60, "team_collapse": 60,
    "win_streak": 58, "prematch_watch": 58, "vs_opponent_count": 55, "career_best": 50,
    "wicketless_streak": 50, "t20_strike_rate": 40, "nth_career_count": 35,
    "venue_record": 25, "highest_total_vs_opp": 25, "highest_chase_vs_opp": 25,
    "heaviest_defeat": 30, "biggest_win": 45, "small_milestone": 20,
}
LEAD_MIN = 70
COVER = {"male": 2006, "female": 2011}

DEMONYM = {"India": "Indian", "Australia": "Australian", "England": "England player",
           "South Africa": "South African", "New Zealand": "New Zealander", "Pakistan": "Pakistani",
           "Sri Lanka": "Sri Lankan", "West Indies": "West Indian", "Bangladesh": "Bangladeshi",
           "Afghanistan": "Afghan", "Ireland": "Ireland player", "Zimbabwe": "Zimbabwean"}
FMT_PLURAL = {"ODI": "ODIs", "T20I": "T20Is", "Test": "Tests"}
NUM = {1: "first", 2: "second", 3: "third"}
LOW = {"ODI": 20, "T20I": 15, "Test": 20}           # "low score" for slump detection
EXPENSIVE = {"ODI": 70, "T20I": 45, "Test": 130}    # runs conceded worth a look
STAR = {  # established player in the format (history since coverage start)
    "ODI": dict(matches=40, runs=1500, wkts=50),
    "T20I": dict(matches=40, runs=1000, wkts=40),
    "Test": dict(matches=25, runs=1500, wkts=70),
}


# Milestones big enough to post (TypeSafe: star milestones high, small milestones ~1%)
BIG_MILESTONE = {"runs": 2000, "wickets": 100}
STAR_BONUS = 15   # a hundred / wicket haul by an established star is worth more


def ordn(n):
    if n in NUM:
        return NUM[n]
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def rank_word(n):
    return "" if n == 1 else f"{ordn(n)}-"


def load_history():
    try:
        return (pd.read_parquet(STATE / "hist_bat.parquet"), pd.read_parquet(STATE / "hist_bowl.parquet"),
                pd.read_parquet(STATE / "hist_team.parquet"))
    except FileNotFoundError:
        return None


def _scope(h, fmt, gender, match_id):
    h = h[(h.format == fmt) & (h.gender == gender) & (h.match_id.astype(str) != str(match_id))]
    return h[pd.to_datetime(h.date) >= pd.Timestamp(f"{COVER[gender]}-01-01")]


def is_star(pid, fmt, gender, hb, hw):
    s = STAR[fmt]
    b = hb[(hb.player_id == pid) & (hb.format == fmt) & (hb.gender == gender)]
    w = hw[(hw.player_id == pid) & (hw.format == fmt) & (hw.gender == gender)]
    matches = len(set(b.match_id) | set(w.match_id))
    return matches >= s["matches"] and (b.runs.sum() >= s["runs"] or w.wkts.sum() >= s["wkts"])


def fact(angle, text, bonus=0):
    return (WEIGHT[angle] + bonus, angle, text)


# ------------------------------------------------------------------ bowling
def bowling_facts(pid, name, team, opp, fmt, gender, city, wkts, conceded, balls, match_id, hb, hw, star):
    subject, facts = _bowling_facts(pid, name, team, opp, fmt, gender, city, wkts, conceded, balls, match_id, hb, hw, star)
    if star and wkts >= (5 if fmt == "Test" else 4):
        facts = [(s + STAR_BONUS, a, t) for s, a, t in facts]
    return subject, facts


def _bowling_facts(pid, name, team, opp, fmt, gender, city, wkts, conceded, balls, match_id, hb, hw, star):
    h = _scope(hw, fmt, gender, match_id)
    since = COVER[gender]
    dem = DEMONYM.get(team, team)
    P = FMT_PLURAL[fmt]
    facts, subject = [], f"{name} {wkts}/{conceded}"
    haul = 5 if fmt == "Test" else 4
    if wkts >= haul:
        lvl = 5 if wkts >= 5 else 4
        feat = "five-wicket haul" if lvl == 5 else "four-wicket haul"
        hit = h[h.wkts >= lvl]
        mine = hit[hit.player_id == pid]
        n = len(mine) + 1
        facts.append(fact("maiden", f"his maiden {fmt} {'five-for' if lvl == 5 else 'four-for'}") if n == 1
                     else fact("nth_career_count", f"his {ordn(n)} {feat} in {P}"))
        n_opp = len(mine[mine.opp == opp]) + 1
        per_opp = hit[hit.opp == opp].groupby("player_id").size()
        best_other = per_opp.drop(pid, errors="ignore").max() if len(per_opp) else 0
        if n_opp > 1 and n_opp > (best_other or 0):
            facts.append(fact("most_vs_opponent", f"his {ordn(n_opp)} against {opp}, the most by anyone since {since}"))
        elif n_opp == 1:
            facts.append(fact("vs_opponent_count", f"his first against {opp}"))
        else:
            facts.append(fact("vs_opponent_count", f"his {ordn(n_opp)} against {opp}"))
        nat = hit[(hit.team == team) & (hit.opp == opp)]
        others = nat[nat.player_id != pid].player_id.nunique()
        if pid not in set(nat.player_id) and others + 1 <= 10:
            facts.append(fact("only_kth_from_nation",
                              f"only the {ordn(others + 1)} {dem} to take a {feat} against {opp} in {P} since {since}",
                              bonus=10 - others))
        tv = h[(h.team == team) & (h.opp == opp)]
        better = ((tv.wkts > wkts) | ((tv.wkts == wkts) & (tv.runs < conceded))).sum()
        if better < 3:
            facts.append(fact("best_by_nation_vs_opp",
                              f"the {rank_word(better + 1)}best figures by a {dem} against {opp} in {P} since {since}",
                              bonus=10 if better == 0 else 0))
        if len(mine) and (pd.Timestamp.now() - pd.Timestamp(mine.date.max())).days >= 365:
            facts.append(fact("drought_ended", f"his first since {pd.Timestamp(mine.date.max()):%B %Y}"))
        me = h[h.player_id == pid]
        if len(me):
            b = me.sort_values(["wkts", "runs"], ascending=[False, True]).iloc[0]
            if wkts > b.wkts or (wkts == b.wkts and conceded < b.runs):
                facts.append(fact("career_best", f"his career-best (previous {b.wkts}/{b.runs})"))
        at = h[h.city == city]
        if city and at.match_id.nunique() >= 8 and not ((at.wkts > wkts) | ((at.wkts == wkts) & (at.runs < conceded))).any():
            facts.append(fact("venue_record", f"the best {fmt} figures at {city} since {since}"))
    # ---- bad day for a star bowler
    if star and conceded >= EXPENSIVE[fmt]:
        me = h[h.player_id == pid]
        if len(me) and conceded > me.runs.max():
            facts.append(fact("star_expensive", f"the most runs he's ever conceded in {'an ODI' if fmt == 'ODI' else 'a T20I' if fmt == 'T20I' else 'a Test innings'} (previous worst {int(me.runs.max())})"))
        nat = h[h.team == team]
        if len(nat) and conceded > nat.runs.max():
            facts.append(fact("star_expensive", f"the most runs conceded by a {dem} in {'an' if fmt == 'ODI' else 'a'} {fmt} innings since {since}", bonus=15))
    if star and wkts == 0 and fmt != "Test":
        me = h[h.player_id == pid].sort_values("date")
        streak = 0
        for w in reversed(me.wkts.tolist()):
            if w == 0:
                streak += 1
            else:
                break
        if streak >= 2:
            facts.append(fact("wicketless_streak", f"wicketless in {streak + 1} {P} in a row"))
    return subject, facts


# ------------------------------------------------------------------ batting
def batting_facts(pid, name, team, opp, fmt, gender, city, runs, balls, out, match_id, hb, star):
    subject, facts = _batting_facts(pid, name, team, opp, fmt, gender, city, runs, balls, out, match_id, hb, star)
    if star and runs >= 100:
        facts = [(s + STAR_BONUS, a, t) for s, a, t in facts]
    return subject, facts


def _batting_facts(pid, name, team, opp, fmt, gender, city, runs, balls, out, match_id, hb, star):
    h = _scope(hb, fmt, gender, match_id)
    since = COVER[gender]
    dem = DEMONYM.get(team, team)
    P = FMT_PLURAL[fmt]
    no = "" if out else "*"
    facts, subject = [], f"{name} {runs}{no} ({balls})"
    me = h[h.player_id == pid].sort_values(["date", "innings"])
    if runs >= 50:
        lvl = 200 if runs >= 200 else 150 if runs >= 150 else 100 if runs >= 100 else 50
        feat = {200: "double hundred", 150: "150-plus score", 100: "hundred", 50: "fifty-plus score"}[lvl]
        hit = h[h.runs >= lvl]
        mine = hit[hit.player_id == pid]
        n = len(mine) + 1
        if lvl >= 100:
            facts.append(fact("maiden", f"his maiden {fmt} {feat}", bonus=10 if lvl >= 150 else 0) if n == 1
                         else fact("nth_career_count", f"his {ordn(n)} {feat} in {P}"))
            n_opp = len(mine[mine.opp == opp]) + 1
            per_opp = hit[hit.opp == opp].groupby("player_id").size()
            best_other = per_opp.drop(pid, errors="ignore").max() if len(per_opp) else 0
            if n_opp > 1 and n_opp > (best_other or 0):
                facts.append(fact("most_vs_opponent", f"his {ordn(n_opp)} against {opp}, the most by anyone since {since}"))
            else:
                facts.append(fact("vs_opponent_count", "his first against " + opp if n_opp == 1 else f"his {ordn(n_opp)} against {opp}"))
            nat = hit[(hit.team == team) & (hit.opp == opp)]
            others = nat[nat.player_id != pid].player_id.nunique()
            if pid not in set(nat.player_id) and others + 1 <= 10:
                facts.append(fact("only_kth_from_nation",
                                  f"only the {ordn(others + 1)} {dem} to score a {feat} against {opp} in {P} since {since}",
                                  bonus=10 - others))
            if len(mine) and (pd.Timestamp.now() - pd.Timestamp(mine.date.max())).days >= 365:
                facts.append(fact("drought_ended", f"his first since {pd.Timestamp(mine.date.max()):%B %Y}", bonus=10 if star else 0))
        else:
            facts.append(fact("nth_career_count", f"his {ordn(n)} {feat} in {P}"))
        tv = h[(h.team == team) & (h.opp == opp)]
        better = (tv.runs > runs).sum()
        if better < 3:
            facts.append(fact("best_by_nation_vs_opp",
                              f"the {rank_word(better + 1)}highest score by a {dem} against {opp} in {P} since {since}",
                              bonus=10 if better == 0 else 0))
        if len(me) and runs > me.runs.max():
            facts.append(fact("career_best", f"a new career-best (previous {int(me.runs.max())})"))
        at = h[h.city == city]
        if city and at.match_id.nunique() >= 8 and not (at.runs > runs).any():
            facts.append(fact("venue_record", f"the highest {fmt} score at {city} since {since}"))
        if fmt == "T20I" and balls and runs / balls >= 2.0:
            facts.append(fact("t20_strike_rate", f"at a strike rate of {100 * runs / balls:.0f}"))
    # ---- bad day for a star batter (only once dismissed)
    if star and out:
        if runs == 0:
            ducks = int(((me.runs == 0) & (me.is_out == 1)).sum()) + 1
            golden = " first ball" if balls <= 1 else ""
            prev_duck = len(me) and me.iloc[-1].runs == 0 and me.iloc[-1].is_out == 1
            txt = f"out for a{' golden' if golden else ''} duck, his {ordn(ducks)} in {P} since {since}"
            if prev_duck:
                txt += ", and his second in a row"
            facts.append(fact("star_duck", txt, bonus=10 if prev_duck else 0))
        if runs < LOW[fmt]:
            last = me[me.is_out == 1].runs.tolist()[-7:] + [runs]
            streak = []
            for r in reversed(last):
                if r < LOW[fmt] + 5:
                    streak.append(r)
                else:
                    break
            if len(streak) >= 4:
                seq = ", ".join(str(x) for x in reversed(streak))
                facts.append(fact("star_slump", f"his last {len(streak)} {fmt} innings: {seq}", bonus=len(streak)))
    return subject, facts


# ------------------------------------------------------------------ team (match end)
def team_facts(team, opp, fmt, gender, city, score, wkts, won, chased, margin_runs, match_id, ht, all_out):
    h = _scope(ht, fmt, gender, match_id)
    since = COVER[gender]
    P = FMT_PLURAL[fmt]
    facts = []
    tv = h[(h.team == team) & (h.opp == opp)]
    if len(tv) >= 5 and not (tv.score > score).any():
        facts.append(fact("highest_total_vs_opp", f"{team}'s highest total against {opp} in {P} since {since}"))
    if all_out and len(tv) >= 5 and not (tv[tv.wkts >= 10].score < score).any():
        facts.append(fact("team_collapse", f"{team}'s lowest all-out total against {opp} in {P} since {since}", bonus=10))
    if won and chased:
        ch = tv[(tv.won == True) & (tv.target > 0)]  # noqa: E712
        if len(ch) >= 3 and not (ch.target > score).any():
            facts.append(fact("highest_chase_vs_opp", f"{team}'s highest successful chase against {opp} in {P} since {since}"))
    # head-to-head streaks
    res = h[(h.team == team) & (h.opp == opp)].drop_duplicates("match_id").sort_values("date")
    seq = [bool(x) for x in res.won.fillna(False)]
    if won:
        streak = 0
        for x in reversed(seq):
            if x:
                streak += 1
            else:
                break
        if streak + 1 >= 5:
            facts.append(fact("win_streak", f"{team}'s {ordn(streak + 1)} straight {fmt} win over {opp}"))
        losses = 0
        for x in reversed(seq):
            if not x:
                losses += 1
            else:
                break
        if losses >= 5:
            facts.append(fact("win_streak", f"{team}'s first {fmt} win over {opp} after {losses} straight defeats", bonus=15))
        if margin_runs:
            wins = res[res.won == True]  # noqa: E712
            if len(wins) >= 3 and not (wins.win_by_runs.fillna(0) > margin_runs).any():
                facts.append(fact("biggest_win", f"{team}'s biggest win by runs over {opp} in {P} since {since}"))
    return facts


HOOKS = ["star_slump", "star_duck", "passes_legend", "only_kth_from_nation", "most_vs_opponent",
         "best_by_nation_vs_opp", "star_expensive", "team_collapse", "win_streak", "drought_ended"]
FLOW = ["maiden", "nth_career_count", "vs_opponent_count", "career_best", "wicketless_streak",
        "t20_strike_rate", "venue_record", "highest_total_vs_opp", "highest_chase_vs_opp", "heaviest_defeat"]


def caption(subject, facts, tag):
    """Social-ready post from the best 2-3 facts: punchy hook first, then natural order."""
    top = sorted(facts, key=lambda f: -f[0])[:3]
    if not top:
        return None
    hooks = [f for f in top if f[1] in HOOKS]
    flow = sorted([f for f in top if f[1] not in HOOKS], key=lambda f: FLOW.index(f[1]) if f[1] in FLOW else 99)
    parts = [t for _, _, t in hooks + flow]
    if len(parts) == 1:
        body = parts[0]
    elif len(parts) == 2:
        body = f"{parts[0]} and {parts[1]}"
    else:
        body = f"{parts[0]}, {parts[1]} and {parts[2]}"
    return f"{subject}: {body}.\n\n{tag}"


def passes(pid, name, team, fmt, gender, stat, before, now, h):
    """Players of the same nation (careers fully inside the data) passed on the format list."""
    col = "runs" if stat == "runs" else "wkts"
    hh = h[(h.format == fmt) & (h.gender == gender) & (h.team == team)]
    first = hh.groupby("player_id").date.min()
    tot = hh.groupby("player_id")[col].sum()
    names = hh.groupby("player_id").player.last()
    full = first[pd.to_datetime(first) >= pd.Timestamp(f"{COVER[gender] + 1}-01-01")].index
    out = []
    for other in full:
        if other == pid:
            continue
        t = int(tot.get(other, 0))
        if before <= t < now and t >= STAR[fmt]["runs" if stat == "runs" else "wkts"]:
            out.append((t, names.get(other)))
    out.sort(reverse=True)
    if not out:
        return []
    t, who = out[0]
    word = "run-scorers" if stat == "runs" else "wicket-takers"
    return [fact("passes_legend", f"moves past {who} ({t:,}) on {team}'s list of {fmt} {word}")]
