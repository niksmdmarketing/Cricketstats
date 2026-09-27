"""Stat nuggets: social-first context for performances in major international matches.

Framing and weights come from TypeSafe (Jev) rankings run on 27 Sep 2026:
  * Recency beats counting. "First West Indian to score an ODI hundred in India since
    October 2018" (83%) crushed "only the 6th West Indian ... since 2006" (1%).
  * Bowling: first by the nation vs this opponent since <date> 43%, first at home since 26%,
    best by the nation vs opponent 19%, career counts 1%.
  * Team: first win in that country since <date> 74%, first home defeat since 13%,
    streak ended 12%, head-to-head counts 0%.
  * Slumps: longest run without a fifty of his career 37%, "no fifty since <date>: N
    innings" 21%, duck counts 16%, raw score sequence 12%.
  * Overall angles: star milestone, star slump, passing a big name, star duck lead; venue
    records, highest totals, small milestones and bare career counts barely register.
Facts scoring >= LEAD_MIN get their own alert; the rest go into an end-of-match digest.
"""
from pathlib import Path

import pandas as pd

from . import config as C

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"

WEIGHT = {
    # recency ("first since") framings
    "first_in_country": 97, "first_vs_opp": 92, "first_at_home": 88, "first_for_nation": 82,
    "first_player": 72, "first_player_vs_opp": 55,
    # team results
    "team_first_win_in_country": 97, "team_first_win_vs_opp": 90, "team_first_home_defeat": 86,
    "team_streak_ended": 82, "team_win_streak": 58,
    # stars
    "star_milestone": 100, "slump_career_worst": 96, "passes_legend": 90, "slump_no_fifty_since": 88,
    "star_duck": 85, "slump_sequence": 72, "duck_first_since": 70, "star_expensive": 70,
    "wicketless_streak": 60,
    # supporting / quiet
    "best_by_nation_vs_opp": 75, "maiden": 72, "most_vs_opponent": 50, "only_kth_from_nation": 45,
    "career_best": 50, "vs_opponent_count": 35, "t20_strike_rate": 40, "nth_career_count": 30,
    "first_in_records": 40, "team_collapse": 55, "venue_record": 20, "highest_total_vs_opp": 20,
    "highest_chase_vs_opp": 25, "biggest_win": 40, "small_milestone": 15,
}
LEAD_MIN = 70
MIN_GAP_DAYS = 330          # "first since" needs roughly a year's gap
COVER = {"male": 2006, "female": 2011}
BIG_MILESTONE = {"runs": 2000, "wickets": 100}
STAR_BONUS = 10

DEMONYM = {"India": "Indian", "Australia": "Australian", "England": "England player",
           "South Africa": "South African", "New Zealand": "New Zealander", "Pakistan": "Pakistani",
           "Sri Lanka": "Sri Lankan", "West Indies": "West Indian", "Bangladesh": "Bangladeshi",
           "Afghanistan": "Afghan", "Ireland": "Ireland player", "Zimbabwe": "Zimbabwean"}
FMT_PLURAL = {"ODI": "ODIs", "T20I": "T20Is", "Test": "Tests"}
ART = {"ODI": "an ODI", "T20I": "a T20I", "Test": "a Test"}
NUM = {1: "first", 2: "second", 3: "third"}
LOW = {"ODI": 20, "T20I": 15, "Test": 20}
EXPENSIVE = {"ODI": 70, "T20I": 45, "Test": 130}
NO_FIFTY_MIN = {"ODI": 8, "T20I": 10, "Test": 8}
STAR = {"ODI": dict(matches=40, runs=1500, wkts=50),
        "T20I": dict(matches=40, runs=1000, wkts=40),
        "Test": dict(matches=25, runs=1500, wkts=70)}


def ordn(n):
    if n in NUM:
        return NUM[n]
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def rank_word(n):
    return "" if n == 1 else f"{ordn(n)}-"


def when(d):
    return f"{pd.Timestamp(d):%B %Y}"


def fact(angle, text, bonus=0):
    return (WEIGHT[angle] + bonus, angle, text)


def load_history():
    try:
        return (pd.read_parquet(STATE / "hist_bat.parquet"), pd.read_parquet(STATE / "hist_bowl.parquet"),
                pd.read_parquet(STATE / "hist_team.parquet"))
    except FileNotFoundError:
        return None


def host_for(city, home, hist_team):
    """Host country for the current match: known city, else history, else the listed home side."""
    if city in C.CITY_COUNTRY:
        return C.CITY_COUNTRY[city]
    m = hist_team[hist_team.city == city].host.dropna()
    if len(m):
        return m.mode().iloc[0]
    return home


def _scope(h, fmt, gender, match_id):
    h = h[(h.format == fmt) & (h.gender == gender) & (h.match_id.astype(str) != str(match_id))]
    return h[pd.to_datetime(h.date) >= pd.Timestamp(f"{COVER[gender]}-01-01")]


def is_star(pid, fmt, gender, hb, hw):
    s = STAR[fmt]
    b = hb[(hb.player_id == pid) & (hb.format == fmt) & (hb.gender == gender)]
    w = hw[(hw.player_id == pid) & (hw.format == fmt) & (hw.gender == gender)]
    matches = len(set(b.match_id) | set(w.match_id))
    return matches >= s["matches"] and (b.runs.sum() >= s["runs"] or w.wkts.sum() >= s["wkts"])


def _run(values, cond):
    """Length of the current run (from the end) of values meeting cond."""
    n = 0
    for v in reversed(values):
        if cond(v):
            n += 1
        else:
            break
    return n


# ------------------------------------------------------------------ "first since" lenses
def first_since(hit, pid, team, opp, host, verb, noun, gender):
    """hit: previous rows with the same feat. Returns the best nation-level lens + a player lens."""
    now = pd.Timestamp.now()
    dem = DEMONYM.get(team, team)
    out = []
    lenses = []
    if host and host != team:
        lenses.append(("first_in_country", hit[(hit.team == team) & (hit.host == host)],
                       f"the first {dem} to {verb} in {host} since {{d}}"))
    lenses.append(("first_vs_opp", hit[(hit.team == team) & (hit.opp == opp)],
                   f"the first {dem} to {verb} against {opp} since {{d}}"))
    if host == team:
        lenses.append(("first_at_home", hit[(hit.team == team) & (hit.host == team)],
                       f"the first {dem} to {verb} at home since {{d}}"))
    lenses.append(("first_for_nation", hit[hit.team == team], f"{team}'s first {noun} since {{d}}"))
    for angle, rows, tmpl in lenses:
        if len(rows):
            last = pd.Timestamp(rows.date.max())
            if (now - last).days >= MIN_GAP_DAYS:
                out.append(fact(angle, tmpl.format(d=when(last))))
                break
        elif angle in ("first_in_country", "first_vs_opp"):
            out.append(fact("first_in_records", tmpl.replace(" since {d}", f" in records going back to {COVER[gender]}")))
            break
    mine = hit[hit.player_id == pid]
    if len(mine):
        last = pd.Timestamp(mine.date.max())
        if (now - last).days >= MIN_GAP_DAYS:
            out.append(fact("first_player", f"his first {noun} since {when(last)}"))
        elif not any(a == "first_vs_opp" for _, a, _ in out):
            vs = mine[mine.opp == opp]
            if len(vs) and (now - pd.Timestamp(vs.date.max())).days >= 2 * MIN_GAP_DAYS:
                out.append(fact("first_player_vs_opp", f"his first against {opp} since {when(vs.date.max())}"))
    return out


# ------------------------------------------------------------------ bowling
def bowling_facts(pid, name, team, opp, fmt, gender, host, city, wkts, conceded, match_id, hb, hw, star):
    h = _scope(hw, fmt, gender, match_id)
    dem = DEMONYM.get(team, team)
    P = FMT_PLURAL[fmt]
    facts = []
    if wkts >= (5 if fmt == "Test" else 4):
        lvl = 5 if wkts >= 5 else 4
        short = "five-for" if lvl == 5 else "four-wicket haul"
        hit = h[h.wkts >= lvl]
        facts += first_since(hit, pid, team, opp, host, f"take {ART[fmt]} {short}", f"{fmt} {short}", gender)
        mine = hit[hit.player_id == pid]
        facts.append(fact("maiden", f"his maiden {fmt} {'five-for' if lvl == 5 else 'four-for'}") if not len(mine)
                     else fact("nth_career_count", f"his {ordn(len(mine) + 1)} {fmt} {short}"))
        tv = h[(h.team == team) & (h.opp == opp)]
        better = ((tv.wkts > wkts) | ((tv.wkts == wkts) & (tv.runs < conceded))).sum()
        if better < 3:
            facts.append(fact("best_by_nation_vs_opp",
                              f"the {rank_word(better + 1)}best figures by a {dem} against {opp} since {COVER[gender]}",
                              bonus=5 if better == 0 else -10))
        me = h[h.player_id == pid]
        if len(me):
            b = me.sort_values(["wkts", "runs"], ascending=[False, True]).iloc[0]
            if wkts > b.wkts or (wkts == b.wkts and conceded < b.runs):
                facts.append(fact("career_best", f"his career-best (previous {b.wkts}/{b.runs})"))
        if star:
            facts = [(s + STAR_BONUS, a, t) for s, a, t in facts]
    # bad day for a star bowler
    if star and conceded >= EXPENSIVE[fmt]:
        inn = " innings" if fmt == "Test" else ""
        me = h[h.player_id == pid]
        if len(me) and conceded > me.runs.max():
            facts.append(fact("star_expensive", f"the most runs he's ever conceded in {ART[fmt]}{inn} (previous worst {int(me.runs.max())})"))
        nat = h[h.team == team]
        if len(nat) and conceded > nat.runs.max():
            facts.append(fact("star_expensive", f"the most runs conceded by a {dem} in {ART[fmt]}{inn} since {COVER[gender]}", bonus=15))
        elif len(nat):
            worse = nat[nat.runs >= conceded]
            last = pd.Timestamp(worse.date.max())
            if (pd.Timestamp.now() - last).days >= 3 * 365:
                facts.append(fact("star_expensive", f"the most runs conceded by a {dem} in {ART[fmt]}{inn} since {when(last)}", bonus=5))
    if star and wkts == 0 and fmt != "Test":
        streak = _run(h[h.player_id == pid].sort_values("date").wkts.tolist(), lambda w: w == 0)
        if streak >= 2:
            facts.append(fact("wicketless_streak", f"wicketless in {streak + 1} {P} in a row"))
    return f"{name} {wkts}/{conceded}", facts


# ------------------------------------------------------------------ batting
def batting_facts(pid, name, team, opp, fmt, gender, host, city, runs, balls, out, match_id, hb, star):
    h = _scope(hb, fmt, gender, match_id)
    dem = DEMONYM.get(team, team)
    no = "" if out else "*"
    facts = []
    me = h[h.player_id == pid].sort_values(["date", "innings"])
    prior = me.runs.tolist()
    if runs >= 100:
        lvl = 200 if runs >= 200 else 150 if runs >= 150 else 100
        noun = {200: "double hundred", 150: "score of 150+", 100: "hundred"}[lvl]
        verb = {200: f"score {ART[fmt]} double hundred", 150: f"score 150+ in {ART[fmt]}",
                100: f"score {ART[fmt]} hundred"}[lvl]
        hit = h[h.runs >= lvl]
        facts += first_since(hit, pid, team, opp, host, verb, f"{fmt} {noun}", gender)
        mine = hit[hit.player_id == pid]
        facts.append(fact("maiden", f"his maiden {fmt} {noun}", bonus=10 if lvl >= 150 else 0) if not len(mine)
                     else fact("nth_career_count", f"his {ordn(len(mine) + 1)} {fmt} {noun}"))
        tv = h[(h.team == team) & (h.opp == opp)]
        better = (tv.runs > runs).sum()
        if better < 3:
            facts.append(fact("best_by_nation_vs_opp",
                              f"the {rank_word(better + 1)}highest score by a {dem} against {opp} since {COVER[gender]}",
                              bonus=5 if better == 0 else -10))
        if len(me) and runs > me.runs.max():
            facts.append(fact("career_best", f"a new career-best (previous {int(me.runs.max())})"))
        if star:
            facts = [(s + STAR_BONUS, a, t) for s, a, t in facts]
    elif runs >= 50:
        if fmt == "T20I" and balls and runs / balls >= 2.0:
            facts.append(fact("t20_strike_rate", f"at a strike rate of {100 * runs / balls:.0f}"))
        gap = _run(prior, lambda r: r < 50)
        if star and gap >= NO_FIFTY_MIN[fmt]:
            facts.append(fact("slump_no_fifty_since", f"his first {fmt} fifty in {gap + 1} innings, ending the drought"))
    # bad day for a star batter (once dismissed)
    if star and out and runs < 50:
        gap = _run(prior, lambda r: r < 50) + 1
        if gap >= NO_FIFTY_MIN[fmt]:
            longest = cur = 0
            for r in prior:
                cur = cur + 1 if r < 50 else 0
                longest = max(longest, cur)
            last50 = me[me.runs >= 50].date.max()
            if gap > longest:
                facts.append(fact("slump_career_worst", f"the longest run without {ART[fmt]} fifty of his career: {gap} innings"))
            elif pd.notna(last50):
                facts.append(fact("slump_no_fifty_since", f"no {fmt} fifty since {when(last50)}: {gap} innings"))
        if runs == 0:
            ducks = int(((me.runs == 0) & (me.is_out == 1)).sum()) + 1
            prev_duck = len(me) and me.iloc[-1].runs == 0 and me.iloc[-1].is_out == 1
            last_duck = me[(me.runs == 0) & (me.is_out == 1)].date.max()
            txt = f"a{' golden' if balls <= 1 else ''} duck, his {ordn(ducks)} in {FMT_PLURAL[fmt]}"
            if prev_duck:
                txt += ", and his second in a row"
            facts.append(fact("star_duck", txt, bonus=10 if prev_duck else 0))
            if pd.notna(last_duck) and (pd.Timestamp.now() - pd.Timestamp(last_duck)).days >= 2 * 365:
                facts.append(fact("duck_first_since", f"his first {fmt} duck since {when(last_duck)}"))
        if runs < LOW[fmt]:
            last = me[me.is_out == 1].runs.tolist()[-7:] + [runs]
            streak = list(reversed(last))[:_run(last, lambda r: r < LOW[fmt] + 5)]
            if len(streak) >= 4:
                facts.append(fact("slump_sequence", f"his last {len(streak)} {fmt} innings: {', '.join(str(x) for x in reversed(streak))}",
                                  bonus=len(streak)))
    return f"{name} {runs}{no} ({balls})", facts


# ------------------------------------------------------------------ team (match end)
def team_facts(winner, loser, fmt, gender, host, match_id, ht):
    h = _scope(ht, fmt, gender, match_id)
    res = h.drop_duplicates(["match_id", "team"]).sort_values("date")
    now = pd.Timestamp.now()
    facts = []
    wins = res[(res.team == winner) & (res.won == True)]  # noqa: E712
    if host and host != winner:
        w_in = wins[wins.host == host]
        if len(w_in) and (now - pd.Timestamp(w_in.date.max())).days >= MIN_GAP_DAYS:
            facts.append(fact("team_first_win_in_country", f"{winner}'s first {fmt} win in {host} since {when(w_in.date.max())}"))
        elif not len(w_in) and len(res[(res.team == winner) & (res.host == host)]) >= 3:
            facts.append(fact("first_in_records", f"{winner}'s first {fmt} win in {host} in records going back to {COVER[gender]}", bonus=30))
    h2h = wins[wins.opp == loser]
    if len(h2h) and (now - pd.Timestamp(h2h.date.max())).days >= MIN_GAP_DAYS:
        facts.append(fact("team_first_win_vs_opp", f"{winner}'s first {fmt} win over {loser} since {when(h2h.date.max())}"))
    if host == loser:
        hl = res[(res.team == loser) & (res.host == loser) & (res.won == False)]  # noqa: E712
        if len(hl) and (now - pd.Timestamp(hl.date.max())).days >= MIN_GAP_DAYS:
            facts.append(fact("team_first_home_defeat", f"{loser}'s first home {fmt} defeat since {when(hl.date.max())}"))
    run = _run(res[(res.team == loser) & (res.opp == winner)].won.fillna(False).astype(bool).tolist(), bool)
    if run >= 5:
        facts.append(fact("team_streak_ended", f"ends {loser}'s {run}-match {fmt} winning run against {winner}"))
    run_w = _run(res[(res.team == winner) & (res.opp == loser)].won.fillna(False).astype(bool).tolist(), bool)
    if run_w + 1 >= 5:
        facts.append(fact("team_win_streak", f"{winner}'s {ordn(run_w + 1)} {fmt} win in a row over {loser}"))
    return facts


def passes(pid, name, team, fmt, gender, stat, before, now, h):
    """Players of the same nation (careers fully inside the data) passed on the format list."""
    col = "runs" if stat == "runs" else "wkts"
    hh = h[(h.format == fmt) & (h.gender == gender) & (h.team == team)]
    first = hh.groupby("player_id").date.min()
    tot = hh.groupby("player_id")[col].sum()
    names = hh.groupby("player_id").player.last()
    full = first[pd.to_datetime(first) >= pd.Timestamp(f"{COVER[gender] + 1}-01-01")].index
    found = [(int(tot.get(o, 0)), names.get(o)) for o in full if o != pid
             and before <= int(tot.get(o, 0)) < now and int(tot.get(o, 0)) >= STAR[fmt]["runs" if stat == "runs" else "wkts"]]
    if not found:
        return []
    t, who = max(found)
    word = "run-scorers" if stat == "runs" else "wicket-takers"
    return [fact("passes_legend", f"moves past {who} ({t:,}) on {team}'s list of {fmt} {word}")]


HOOKS = ["first_in_country", "first_vs_opp", "first_at_home", "first_for_nation", "team_first_win_in_country",
         "team_first_win_vs_opp", "team_first_home_defeat", "team_streak_ended", "slump_career_worst",
         "slump_no_fifty_since", "star_duck", "passes_legend", "star_expensive", "best_by_nation_vs_opp",
         "first_in_records", "slump_sequence", "duck_first_since", "first_player"]


def caption(subject, facts, tag):
    """Social-ready post: strongest hook first, then up to two supporting facts."""
    top = sorted(facts, key=lambda f: -f[0])[:3]
    if not top:
        return None
    ordered = [f for f in top if f[1] in HOOKS] + [f for f in top if f[1] not in HOOKS]
    parts = [t for _, _, t in ordered]
    body = f"{subject}: {parts[0]}" if subject else parts[0][0].upper() + parts[0][1:]
    if len(parts) == 2:
        body += f". Also {parts[1]}"
    elif len(parts) == 3:
        body += f". Also {parts[1]}, and {parts[2]}"
    return f"{body}.\n\n{tag}"
