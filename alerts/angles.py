"""Angle engine: many ways to frame one performance, so the feed never reads like a template.

Runs for batters (and all-rounders), wickets for bowlers (and all-rounders). Each detector returns
facts as (score, family, text). rotation.py then picks one hook plus up to two support lines from
different families, and keeps any family from dominating the feed.

Families (from a review of big cricket stats accounts, Sep 2026):
  form        recent run of scores/wickets, series totals, streaks
  legend      all-time lists: passes, equals, clubs, "only X has more" (Statsguru snapshot)
  company     rare feats: "only the 3rd player to...", "joins A and B", "the only other time was..."
  drought     "first <nation> to ... since <player>, <month year>"
  context     venue/city, opponent, batting position, chases, away from home
  leaderboard this calendar year's charts, national clusters ("3 of the top 4 are Pakistani")
  shift       before vs after: numbers over the last 12 months against the rest of the career
  comeback    gap since last hundred/five-for, return after a long absence
  quirk       same score twice, 90s, birthdays, 100th match, identical figures
  lone_hand   big performance in a defeat, "took 20 wickets and lost every match"
  split       pace vs spin, bowling type, powerplay/death
  duel        batter vs bowler head-to-heads
  age         youngest/oldest to do it for the nation since <player>
  slump       bad days for big players (from the earlier engine)
"""
import hashlib
from dataclasses import dataclass, field

import pandas as pd

from . import config as C

COVER = {"male": 2006, "female": 2011}
MIN_GAP_DAYS = 330
DEMONYM = {"India": "Indian", "Australia": "Australian", "England": "England player",
           "South Africa": "South African", "New Zealand": "New Zealander", "Pakistan": "Pakistani",
           "Sri Lanka": "Sri Lankan", "West Indies": "West Indian", "Bangladesh": "Bangladeshi",
           "Afghanistan": "Afghan", "Ireland": "Ireland player", "Zimbabwe": "Zimbabwean"}
PLURAL = {"ODI": "ODIs", "T20I": "T20Is", "Test": "Tests", "T20": "matches"}
ART = {"ODI": "an ODI", "T20I": "a T20I", "Test": "a Test", "T20": "a match"}
BASE = {  # family weights (evidence + TypeSafe ranking; see README)
    "form": 88, "legend": 92, "company": 86, "drought": 86, "context": 74, "leaderboard": 76,
    "shift": 78, "comeback": 80, "quirk": 78, "lone_hand": 74, "split": 64, "duel": 76,
    "age": 76, "slump": 84, "team": 86}
LEAD_MIN = 82


def ordn(n):
    n = int(n)
    if n in (1, 2, 3):
        return {1: "first", 2: "second", 3: "third"}[n]
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def ord_short(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def when(d):
    return f"{pd.Timestamp(d):%B %Y}"


def surname(name):
    parts = str(name).split()
    if len(parts) > 1 and parts[0].isupper() and len(parts[0]) <= 4 and parts[0].isalpha():
        return " ".join(parts[1:])
    return str(name)


def span_dates(d0, d1):
    """Calendar span: '3 years, 20 days' (leap years handled)."""
    d0, d1 = pd.Timestamp(d0).normalize(), pd.Timestamp(d1).normalize()
    y = d1.year - d0.year
    if (d1.month, d1.day) < (d0.month, d0.day):
        y -= 1
    if y <= 0:
        return span((d1 - d0).days)
    try:
        anchor = d0.replace(year=d0.year + y)
    except ValueError:            # 29 Feb
        anchor = d0.replace(year=d0.year + y, day=28)
    d = (d1 - anchor).days
    return f"{y} year{'s' * (y > 1)}" + (f", {d} day{'s' * (d != 1)}" if d else "")


def span(days):
    y, d = divmod(int(days), 365)
    if y and d:
        return f"{y} year{'s' * (y > 1)}, {d} day{'s' * (d != 1)}"
    if y:
        return f"{y} year{'s' * (y > 1)}"
    m = d // 30
    return f"{m} month{'s' * (m > 1)}" if m >= 2 else f"{d} days"


@dataclass
class Perf:
    pid: str
    name: str
    team: str
    opp: str
    fmt: str                 # Test | ODI | T20I | T20 (league)
    gender: str
    comp: str                # e.g. "Men's ODI" or "Indian Premier League"
    match_id: str
    date: pd.Timestamp
    city: str = None
    host: str = None
    inn: int = 1
    runs: int = None
    balls: int = None
    out: int = None
    pos: int = None
    fours: int = None
    sixes: int = None
    kind: str = None
    out_bowler: str = None   # cricsheet id of the bowler credited with the wicket
    wkts: int = None
    conceded: int = None
    bballs: int = None
    pp_wkts: int = None
    death_wkts: int = None
    won: bool = None         # None while the match is in progress
    chasing: bool = None
    star: bool = False       # established batter (for batting angles) / bowler (for bowling angles)
    match_runs: int = None   # all-rounders: runs in the match so far
    match_wkts: int = None
    extra: dict = field(default_factory=dict)


class Ctx:
    """History + metadata shared by all detectors."""

    def __init__(self, hist, meta=None, legends=None, names=None, today=None):
        self.hb, self.hw, self.ht = hist["hist_bat"], hist["hist_bowl"], hist["hist_team"]
        self.duel = hist.get("hist_duel")
        self.sb, self.sw = hist.get("hist_split_bat"), hist.get("hist_split_bowl")
        for d in (self.hb, self.hw, self.ht, self.duel):
            if d is not None and len(d):
                d["date"] = pd.to_datetime(d["date"])
        self.meta = meta.set_index("player_id") if meta is not None and len(meta) else None
        self.pname = {}
        for d in (self.hw, self.hb):
            if d is not None and len(d):
                self.pname.update(dict(zip(d.player_id, d.player)))
        self.legends = legends
        self.names = names or {}
        self.today = pd.Timestamp(today or pd.Timestamp.now().normalize())
        self.charts = {}   # fact text -> (chart idea, data)

    # scope: same format, gender and (for leagues) competition, excluding this match
    def scope(self, df, p):
        mid = str(p.match_id).replace("espn", "")
        d = df[(df.format == p.fmt) & (df.gender == p.gender) & ~df.match_id.astype(str).isin([mid, f"espn{mid}"])]
        if p.fmt == "T20":
            d = d[d.competition == p.comp]
        return d

    def nm(self, pid, fallback):
        n = self.names.get(pid)
        if not (isinstance(n, str) and n):
            n = fallback or self.pname.get(pid, "")
        return surname(n)

    def full(self, pid, fallback=""):
        """Display name for standalone posts: ESPN's name if we've seen the player, else first name + surname."""
        if not hasattr(self, "_espn"):
            import json
            from pathlib import Path
            f = Path(__file__).resolve().parents[1] / "state" / "espn_names.json"
            try:
                self._espn = json.loads(f.read_text())
            except (FileNotFoundError, ValueError):
                self._espn = {}
        if pid in self._espn:
            return self._espn[pid]
        short = fallback or self.pname.get(pid, "")
        full = self.names.get(pid)
        if self.meta is not None and pid in self.meta.index and not isinstance(full, str):
            full = self.meta.at[pid, "name"]
        if isinstance(full, str) and full.split() and short:
            first = full.split()[0]
            sur = surname(short)
            if sur != short:          # 'SR Tendulkar' -> 'Sachin Tendulkar'
                return f"{first} {sur}"
            return short
        return short or (full if isinstance(full, str) else "")

    def dob(self, pid):
        if self.meta is None or pid not in self.meta.index:
            return None
        v = self.meta.at[pid, "dob"]
        return pd.Timestamp(v) if pd.notna(v) else None

    def bdetail(self, pid):
        if self.meta is None or pid not in self.meta.index:
            return None, None
        return self.meta.at[pid, "btype"], self.meta.at[pid, "bdetail"]


def F(family, text, bonus=0, strength=0):
    return (BASE[family] + bonus + min(strength, 20), family, text)


def is_league(p):
    return p.fmt == "T20"


def who(p):
    """'Indian' for internationals, 'RCB player' style for leagues."""
    if is_league(p):
        return f"{p.team} player"
    return DEMONYM.get(p.team, f"{p.team} player")


def fmt_name(p):
    return {"T20": C_SHORT.get(p.comp, p.comp)}.get(p.fmt, p.fmt)


AN_LETTERS = set("AEFHILMNORSX")


def plural(p):
    return PLURAL[p.fmt] if not is_league(p) else f"{fmt_name(p)} matches"


def in_(p):
    return f"in {PLURAL[p.fmt]}" if not is_league(p) else f"in the {fmt_name(p)}"


def art2(p):
    """'an ODI' / 'an IPL' / 'a BBL' (before a noun)."""
    if not is_league(p):
        return ART[p.fmt]
    P = fmt_name(p)
    return ("an " if P[0] in AN_LETTERS and P != "Hundred" else "a ") + P


def art(p):
    """'an ODI' / 'an IPL match' (standalone)."""
    return art2(p) if not is_league(p) else art2(p) + " match"


C_SHORT = {"Indian Premier League": "IPL", "Big Bash League": "BBL", "Women's Big Bash League": "WBBL",
           "Pakistan Super League": "PSL", "Caribbean Premier League": "CPL", "SA20": "SA20",
           "International League T20": "ILT20", "Major League Cricket": "MLC", "Women's Premier League": "WPL",
           "The Hundred Men's Competition": "Hundred", "The Hundred Women's Competition": "Hundred"}


def era(p, ctx):
    """How far back 'ever' reaches in our data for this scope."""
    if is_league(p) or (p.fmt == "T20I" and p.gender == "male"):
        return "ever"
    return f"in records since {COVER[p.gender]}"


def sex(p):
    return "" if p.gender == "male" else "women's "


# ======================================================================= drought ("first since")
def _games(ctx, p, lens, since=None):
    """The team's matches that fit a lens (in a country / vs an opponent / at home / overall), after a date."""
    t = ctx.scope(ctx.ht, p)
    t = t[t.team == p.team]
    if lens == "country":
        t = t[t.host == p.host]
    elif lens == "opp":
        t = t[t.opp == p.opp]
    elif lens == "home":
        t = t[t.host == p.team]
    elif lens == "city":
        t = t[t.city == p.city]
    if since is not None:
        t = t[t.date > since]
    return t.match_id.nunique()


def drought(ctx, p, hit, verb, noun, mates=()):
    """hit: prior rows with the same feat (in scope). Lenses: in country, vs opponent, at home, overall.
    A drought only counts if the team played enough matches in between (not just a long gap between fixtures).
    mates: team-mates who did the same thing in this match."""
    out = []
    dem = who(p)
    lenses = []
    need = 2 if p.fmt == "Test" else 3
    also = ""
    if mates:
        names = [surname(n) for n in mates]
        also = f" — {' and '.join(names)} did it too in this match"
    if not is_league(p):
        if isinstance(p.host, str) and p.host and p.host != p.team:
            lenses.append(("country", hit[(hit.team == p.team) & (hit.host == p.host)], f"the first {dem} to {verb} in {p.host}"))
        lenses.append(("opp", hit[(hit.team == p.team) & (hit.opp == p.opp)], f"the first {dem} to {verb} against {p.opp}"))
        if p.host == p.team:
            lenses.append(("home", hit[(hit.team == p.team) & (hit.host == p.team)], f"the first {dem} to {verb} at home"))
    elif isinstance(p.city, str) and p.city:
        lenses.append(("city", hit[(hit.team == p.team) & (hit.city == p.city)], f"the first {dem} to {verb} in {p.city}"))
    lenses.append(("team", hit[hit.team == p.team], f"{poss(p.team)} first {noun}"))
    for lens, rows, lead in lenses:
        if len(rows):
            last = rows.sort_values("date").iloc[-1]
            gap = (p.date - last.date).days
            if gap >= MIN_GAP_DAYS and _games(ctx, p, lens, last.date) >= need:
                if lens == "team":
                    named = f" since {when(last.date)}"
                elif last.player_id == p.pid:
                    named = f" since his own in {when(last.date)}"
                else:
                    named = f" since {ctx.nm(last.player_id, last.player)} in {when(last.date)}"
                self_ = last.player_id == p.pid     # "since his own" is weaker than beating someone else's
                out.append(F("drought", lead + named + also, strength=min(12, gap // 365 * 2) + (6 if lens in ("country", "opp") else 0) - (14 if self_ else 0)))
                break
        elif lens in ("country", "opp", "city") and _games(ctx, p, lens) >= need + 2:
            out.append(F("drought", lead + (" ever" if era(p, ctx) == "ever" else f" in records going back to {COVER[p.gender]}") + also, strength=8))
            break
    # the player's own gap
    mine = hit[hit.player_id == p.pid]
    if len(mine):
        last = mine.date.max()
        gap = (p.date - last).days
        if gap >= 365:
            out.append(F("comeback", f"his first {noun} in {span_dates(last, p.date)}", strength=min(12, gap // 365 * 3)))
    return out


# ======================================================================= company (rare feats)
BAT_FEATS = [
    # key, test(p, row-like), phrase for "the Nth player to <phrase>", formats
    ("ton_low", lambda r: r.runs >= 100 and (r.pos or 0) >= 6, "score {art2} hundred batting at No.6 or lower", None),
    ("ton_fast", lambda r: r.runs >= 100 and r.balls and r.balls <= {"T20I": 45, "T20": 45, "ODI": 60, "Test": 90}[r.format],
     "score {art2} hundred in {lim} balls or fewer", None),
    ("fifty_fast", lambda r: r.runs >= 50 and r.balls and r.balls <= {"T20I": 18, "T20": 18, "ODI": 24, "Test": 35}[r.format],
     "score {art2} fifty in {lim50} balls or fewer", None),
    ("sixes", lambda r: (r.sixes or 0) >= {"T20I": 10, "T20": 10, "ODI": 10, "Test": 7}[r.format],
     "hit {s6}+ sixes in {art2} innings", None),
    ("ton_chase", lambda r: r.runs >= 100 and bool(r.chasing == True) and bool(r.won == True) and not r.is_out,  # noqa: E712
     "score an unbeaten hundred in a successful {fmtname} chase", None),
    ("big150", lambda r: r.runs >= 150 and r.format in ("ODI", "T20I", "T20"), "score 150+ in {art}", None),
]
BOWL_FEATS = [
    ("five_t20", lambda r: r.wkts >= 5 and r.format in ("T20I", "T20"), "take five wickets in {art}", None),
    ("cheap_haul", lambda r: (r.format in ("T20I", "T20") and r.wkts >= 4 and r.runs <= 12)
     or (r.format == "ODI" and r.wkts >= 5 and r.runs <= 20), "take {w} wickets for {c} runs or fewer in {art}", None),
    ("pp_haul", lambda r: (r.pp_wkts or 0) >= {"T20I": 3, "T20": 3, "ODI": 4, "Test": 99}[r.format],
     "take {pp}+ wickets in the powerplay of {art}", None),
    ("death_haul", lambda r: (r.death_wkts or 0) >= {"T20I": 3, "T20": 3, "ODI": 4, "Test": 99}[r.format],
     "take {dw}+ wickets at the death in {art}", None),
    ("six_for", lambda r: r.wkts >= 6 and r.format == "ODI", "take six or more wickets in {art}", None),
    ("eight_for", lambda r: r.wkts >= 8 and r.format == "Test", "take eight or more wickets in a Test innings", None),
]


def _row(p, kind):
    r = pd.Series(dict(runs=p.runs or 0, balls=p.balls, pos=p.pos, sixes=p.sixes, chasing=p.chasing, won=p.won,
                       is_out=p.out, format=p.fmt, wkts=p.wkts or 0, runs_c=p.conceded, pp_wkts=p.pp_wkts,
                       death_wkts=p.death_wkts))
    if kind == "bowl":
        r["runs"] = p.conceded or 0
    return r


def _feat_mask(df, test):
    ok = []
    for r in df.itertuples():
        try:
            ok.append(bool(test(r)))
        except (TypeError, KeyError):
            ok.append(False)
    return pd.Series(ok, index=df.index)


def company(ctx, p, kind):
    df = ctx.scope(ctx.hb if kind == "bat" else ctx.hw, p)
    feats = BAT_FEATS if kind == "bat" else BOWL_FEATS
    cur = _row(p, kind)
    out = []
    for key, test, phrase, _ in feats:
        try:
            if not test(cur):
                continue
        except (TypeError, KeyError):
            continue
        # cheap pre-filter before the row-by-row test
        pre = df[df.runs >= 100] if key.startswith("ton") or key == "big150" else \
            df[df.runs >= 50] if key == "fifty_fast" else \
            df[df.sixes >= 7] if key == "sixes" else df[df.wkts >= 3] if kind == "bowl" else df
        prior = pre[_feat_mask(pre, test)] if len(pre) else pre
        ph = phrase.format(art=art(p), art2=art2(p),
                           lim={"T20I": 45, "T20": 45, "ODI": 60, "Test": 90}[p.fmt],
                           lim50={"T20I": 18, "T20": 18, "ODI": 24, "Test": 35}[p.fmt],
                           s6={"T20I": 10, "T20": 10, "ODI": 10, "Test": 7}[p.fmt], fmtname=fmt_name(p),
                           w=p.wkts, c={"T20I": 12, "T20": 12}.get(p.fmt, 20), pp=3 if p.fmt != "ODI" else 4,
                           dw=3 if p.fmt != "ODI" else 4)
        n = prior.player_id.nunique() if len(prior) else 0
        ever = era(p, ctx)
        tail = "" if ever == "ever" else f" ({ever})"
        people = prior.sort_values("date").drop_duplicates("player_id", keep="last")
        already = p.pid in set(prior.player_id)
        if n == 0:
            out.append(F("company", f"the first player to {ph}{' ever' if ever == 'ever' else f' ({ever})'}", strength=14))
        elif already:
            times = (prior.player_id == p.pid).sum() + 1
            if times >= 2 and n <= 12:
                out.append(F("company", f"the {ordn(times)} time he's managed to {ph} — no one else has done it more than "
                                        f"{prior[prior.player_id != p.pid].player_id.value_counts().max() if n > 1 else 0} times{tail}",
                             strength=6))
        elif n == 1:
            r = people.iloc[0]
            out.append(F("company", f"the only other player to {ph}{tail} is {ctx.nm(r.player_id, r.player)} "
                                    f"({r.team} v {r.opp}, {when(r.date)})", strength=12))
        elif n <= 3:
            names = [f"{ctx.nm(r.player_id, r.player)} ({r.date.year})" for r in people.itertuples()]
            out.append(F("company", f"joins {', '.join(names[:-1])} and {names[-1]} as the only players to {ph}{tail}", strength=10))
        elif n <= 15:
            out.append(F("company", f"only the {ordn(n + 1)} player to {ph}{tail}", strength=4))
        # national lens
        nat = prior[prior.team == p.team] if len(prior) else prior
        if not already and 0 < n and len(nat.player_id.unique()) <= 1 and not is_league(p):
            if len(nat) == 0:
                out.append(F("company", f"the first {who(p)} to {ph}{tail}", strength=8))
            else:
                r = nat.sort_values("date").iloc[-1]
                out.append(F("company", f"only the second {who(p)} to {ph}{tail}, after {ctx.nm(r.player_id, r.player)} in {r.date.year}", strength=6))
    return out


# ======================================================================= form
def seq(vals, outs=None):
    return ", ".join(f"{v}{'' if outs is None or outs[i] else '*'}" for i, v in enumerate(vals))


def form_bat(ctx, p):
    me = ctx.scope(ctx.hb, p)
    me = me[me.player_id == p.pid].sort_values(["date", "innings"])
    runs = me.runs.tolist() + [p.runs]
    outs = me.is_out.tolist() + [p.out]
    out = []
    # last N innings run
    best = None
    for k in range(3, 7):
        if len(runs) < k:
            break
        last, lo = runs[-k:], outs[-k:]
        dis = max(1, sum(lo))
        avg = sum(last) / dis
        need = {"ODI": 65, "Test": 70, "T20I": 45, "T20": 45}[p.fmt] * (1.35 if k == 3 else 1)
        if sum(lo) >= k - 1 and avg >= need and sum(r >= {"T20I": 30, "T20": 30}.get(p.fmt, 40) for r in last) >= k - 1:
            best = (k, last, lo, avg)
    if best:
        k, last, lo, avg = best
        out.append(F("form", f"his last {k} {fmt_name(p)} innings: {seq(last, lo)} — {sum(last)} runs at {avg:.1f}",
                     strength=min(10, int(avg / 10))))
    # streak of 50+ scores
    st = 0
    for r in reversed(runs):
        if r >= 50:
            st += 1
        else:
            break
    if st >= 3:
        out.append(F("form", f"his {ordn(st)} successive 50+ score {in_(p)}", strength=4 * st))
    # x of last m
    if p.runs >= 50 and st < 3:
        for m in range(12, 5, -1):
            if len(runs) >= m:
                c = sum(r >= 50 for r in runs[-m:])
                if c >= max(5, int(0.6 * m)):
                    out.append(F("form", f"his {ordn(c)} 50+ score in his last {m} {fmt_name(p)} innings", strength=c))
                    break
    # series total (same opponent, within 45 days, same host)
    if not is_league(p):
        s = me[(me.opp == p.opp) & (me.date >= p.date - pd.Timedelta(days=45))]
        if p.host:
            s = s[s.host == p.host]
        if len(s) >= 1:
            tot = int(s.runs.sum()) + p.runs
            dis = max(1, int(s.is_out.sum()) + (p.out or 0))
            need = {"ODI": 200, "Test": 350, "T20I": 140}[p.fmt]
            if tot >= need:
                allb = ctx.scope(ctx.hb, p)
                allb = allb[allb.match_id.isin(set(s.match_id))]
                others = allb[allb.player_id != p.pid].groupby("player_id").runs.sum()
                top = tot > (others.max() if len(others) else 0)
                sc = [str(x) for x in s.runs.tolist()] + [f"{p.runs}{'' if p.out else '*'}"]
                out.append(F("form", f"{tot} runs in the series at {tot / dis:.1f} ({', '.join(sc)})"
                                     + (" — the most by anyone" if top else ""), strength=6 + (6 if top else 0)))
    return out


def form_bowl(ctx, p):
    me = ctx.scope(ctx.hw, p)
    me = me[me.player_id == p.pid].sort_values(["date", "innings"])
    w = me.wkts.tolist() + [p.wkts]
    c = me.runs.tolist() + [p.conceded]
    out = []
    for k in range(6, 2, -1):
        if len(w) >= k:
            lw, lc = w[-k:], c[-k:]
            need = {"T20I": 2.4, "T20": 2.4, "ODI": 2.8, "Test": 3.5}[p.fmt]
            if sum(lw) / k >= need and min(lw) >= 1:
                out.append(F("form", f"wickets in his last {k} {fmt_name(p)} innings: {', '.join(map(str, lw))} — "
                                     f"{sum(lw)} at {sum(lc) / max(1, sum(lw)):.1f}", strength=min(10, sum(lw) - 2 * k)))
                break
    st = 0
    for x in reversed(w):
        if x >= 3:
            st += 1
        else:
            break
    if st >= 3:
        out.append(F("form", f"his {ordn(st)} successive three-wicket haul {in_(p)}", strength=4 * st))
    if not is_league(p):
        s = me[(me.opp == p.opp) & (me.date >= p.date - pd.Timedelta(days=45))]
        if p.host:
            s = s[s.host == p.host]
        if len(s) >= 1:
            tot = int(s.wkts.sum()) + p.wkts
            need = {"ODI": 9, "Test": 18, "T20I": 7}[p.fmt]
            if tot >= need:
                allw = ctx.scope(ctx.hw, p)
                allw = allw[allw.match_id.isin(set(s.match_id))]
                others = allw[allw.player_id != p.pid].groupby("player_id").wkts.sum()
                top = tot > (others.max() if len(others) else 0)
                runs = int(s.runs.sum()) + p.conceded
                out.append(F("form", f"{tot} wickets in the series at {runs / tot:.1f}" + (" — the most by anyone" if top else ""),
                             strength=5 + (6 if top else 0)))
    return out


# ======================================================================= context
def context_bat(ctx, p):
    out = []
    h = ctx.scope(ctx.hb, p)
    me = h[h.player_id == p.pid]
    P = fmt_name(p)
    if p.runs >= 100 and len(me) >= 15 and p.runs > me.runs.max():
        out.append(F("context", f"a new career-best (previous {int(me.runs.max())})", strength=-4))
    if p.runs >= 100 and not is_league(p):
        tv = h[(h.team == p.team) & (h.opp == p.opp)]
        b = int((tv.runs > p.runs).sum())
        if len(tv) >= 40 and b < 3:
            out.append(F("context", f"the {'' if b == 0 else ordn(b + 1) + '-'}highest score by {art_of(who(p))} against {p.opp} ({era(p, ctx)})".replace("(ever)", "ever"),
                         strength=8 if b == 0 else -2))
    if p.runs >= 100:
        # city
        if p.city:
            here = me[(me.city == p.city) & (me.runs >= 100)]
            n = len(here) + 1
            if n >= 3:
                rivals = h[(h.city == p.city) & (h.runs >= 100)].groupby("player_id").size()
                most = n > (rivals.drop(p.pid, errors="ignore").max() if len(rivals) else 0)
                out.append(F("context", f"his {ordn(n)} {P} hundred in {p.city}" + (f" — the most by anyone there {era(p, ctx)}" if most else ""),
                             strength=2 * n + (6 if most else 0)))
        # opponent
        if not is_league(p):
            vs = me[(me.opp == p.opp) & (me.runs >= 100)]
            n = len(vs) + 1
            if n >= 3:
                rivals = h[(h.opp == p.opp) & (h.runs >= 100)].groupby("player_id").size().drop(p.pid, errors="ignore")
                nxt = rivals.max() if len(rivals) else 0
                if n > nxt:
                    out.append(F("context", f"his {ordn(n)} {P} hundred against {p.opp} — no one else has more than {nxt} {era(p, ctx)}",
                                 strength=2 * n))
            # away from home
            if p.host and p.host != p.team:
                aw = me[(me.runs >= 100) & (me.host.notna()) & (me.host != me.team)]
                if len(aw) == 0 and len(me[me.runs >= 100]) >= 2:
                    out.append(F("context", f"his first {P} hundred away from home, at the {len(me[me.runs >= 100]) + 1}{ord_short(len(me[me.runs >= 100]) + 1)[-2:]} attempt",
                                 strength=4))
        # batting position
        if p.pos:
            same = h[(h.team == p.team) & (h.pos == p.pos) & (h.runs > p.runs)]
            if p.host and not is_league(p):
                same = same[(same.host == p.team) == (p.host == p.team)]
            where = " at home" if p.host == p.team else " away from home" if p.host and not is_league(p) else ""
            if len(same):
                last = same.sort_values("date").iloc[-1]
                if (p.date - last.date).days >= 3 * 365:
                    out.append(F("context", f"the highest {P} score by {art_of(who(p))} at No.{p.pos}{where} since "
                                            f"{ctx.nm(last.player_id, last.player)}'s {last.runs} in {when(last.date)}", strength=6))
            elif len(h[(h.team == p.team) & (h.pos == p.pos)]) >= 30:
                out.append(F("context", f"the highest {P} score by {art_of(who(p))} batting at No.{p.pos}{where} {era(p, ctx)}", strength=8))
        # chases
        if p.chasing and p.won and p.fmt != "Test":
            ch = me[(me.chasing == True) & (me.won == True) & (me.runs >= 100)]  # noqa: E712
            n = len(ch) + 1
            if n >= 3:
                rivals = h[(h.chasing == True) & (h.won == True) & (h.runs >= 100)].groupby("player_id").size().drop(p.pid, errors="ignore")  # noqa: E712
                nxt = rivals.max() if len(rivals) else 0
                out.append(F("context", f"his {ordn(n)} hundred in a successful {P} chase" + (f" — the most by anyone {era(p, ctx)}" if n > nxt else ""),
                             strength=2 * n))
    # unbeaten run at a city (Kohli: 315 runs in Thiruvananthapuram without being dismissed)
    if p.city and not p.out:
        here = me[me.city == p.city].sort_values("date")
        seqn = 0
        tot = p.runs
        for r in reversed(list(here.itertuples())):
            if r.is_out:
                break
            seqn += 1
            tot += r.runs
        if seqn >= 2 and tot >= {"ODI": 200, "Test": 300, "T20I": 120, "T20": 120}[p.fmt]:
            out.append(F("context", f"{tot} {P} runs in {p.city} without being dismissed ({seqn + 1} innings)", strength=10))
    return out


def poss(name):
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def art_of(w):
    return ("an " if w[0].lower() in "aeiou" else "a ") + w


def context_bowl(ctx, p):
    out = []
    h = ctx.scope(ctx.hw, p)
    me = h[h.player_id == p.pid]
    P = fmt_name(p)
    better = lambda d: (d.wkts > p.wkts) | ((d.wkts == p.wkts) & (d.runs < p.conceded))  # noqa: E731
    if p.wkts >= (5 if p.fmt == "Test" else 4):
        if len(me) >= 10 and not better(me).any():
            prev = me.sort_values(["wkts", "runs"], ascending=[False, True]).iloc[0]
            out.append(F("context", f"his career-best figures (previous best {prev.wkts}/{prev.runs})", strength=-4))
        if not is_league(p):
            tv = h[(h.team == p.team) & (h.opp == p.opp)]
            b = int(better(tv).sum())
            if len(tv) >= 20 and b < 3:
                out.append(F("context", f"the {'' if b == 0 else ordn(b + 1) + '-'}best figures by {art_of(who(p))} against {p.opp} ({era(p, ctx)})".replace("(ever)", "ever"),
                             strength=8 if b == 0 else 0))
    lvl = 5 if p.wkts >= 5 else 4
    if p.fmt == "Test" and p.wkts < 5:
        return out
    if p.wkts >= 4 and p.city:
        here = me[(me.city == p.city) & (me.wkts >= lvl)]
        n = len(here) + 1
        if n >= 2:
            out.append(F("context", f"his {ordn(n)} {'five-for' if lvl == 5 else 'four-wicket haul'} in {p.city}", strength=2 * n))
    if p.wkts >= 4 and not is_league(p):
        vs = me[me.opp == p.opp]
        tot = int(vs.wkts.sum()) + p.wkts
        rivals = h[h.opp == p.opp].groupby("player_id").wkts.sum().drop(p.pid, errors="ignore")
        if len(rivals) and tot > rivals.max() and tot >= 20:
            out.append(F("context", f"{tot} {P} wickets against {p.opp} — more than anyone else {era(p, ctx)}", strength=8))
    return out


# ======================================================================= leaderboard (calendar year)
def leaderboard(ctx, p, kind):
    out = []
    yr = p.date.year
    df = ctx.scope(ctx.hb if kind == "bat" else ctx.hw, p)
    df = df[df.date.dt.year == yr]
    col = "runs" if kind == "bat" else "wkts"
    add = p.runs if kind == "bat" else p.wkts
    if not add:
        return out
    tot = df.groupby("player_id")[col].sum()
    tot.loc[p.pid] = tot.get(p.pid, 0) + add
    tot = tot.sort_values(ascending=False)
    rank = list(tot.index).index(p.pid) + 1
    P = fmt_name(p)
    noun = "runs" if kind == "bat" else "wickets"
    if rank == 1 and tot.iloc[0] >= {"bat": {"ODI": 500, "Test": 700, "T20I": 300, "T20": 300},
                                    "bowl": {"ODI": 20, "Test": 30, "T20I": 15, "T20": 12}}[kind][p.fmt]:
        lead = tot.iloc[0] - (tot.iloc[1] if len(tot) > 1 else 0)
        txt = f"now leads the {yr} {sex(p)}{P} {noun} chart with {tot.iloc[0]}" if not is_league(p) else \
            f"tops the {P} {yr} {noun} list with {tot.iloc[0]}"
        out.append(F("leaderboard", txt + (f", {lead} clear" if lead >= 3 else ""), strength=6))
    # national cluster: 3 of the top 4 are from one country (internationals only)
    if not is_league(p) and rank <= 4:
        top = tot.head(4).index
        teams = df.drop_duplicates("player_id").set_index("player_id").team.reindex(top).fillna(p.team)
        teams.loc[p.pid] = p.team
        if (teams == p.team).sum() >= 3:
            k = (teams == p.team).sum()
            out.append(F("leaderboard", f"{'all' if k == 4 else k} of the top 4 {sex(p)}{P} {noun[:-1]}-{'scorers' if kind == 'bat' else 'takers'} "
                                        f"in {yr} are {who(p).replace(' player', '')}{'s' if not who(p).endswith('s') else ''}", strength=6))
    return out


# ======================================================================= shift (before vs after)
def shift_bat(ctx, p):
    h = ctx.scope(ctx.hb, p)
    me = h[h.player_id == p.pid]
    cut = p.date - pd.Timedelta(days=365)
    a, b = me[me.date < cut], me[me.date >= cut]
    ra, rb = a.runs.sum(), b.runs.sum() + p.runs
    oa, ob = max(1, a.is_out.sum()), max(1, b.is_out.sum() + (p.out or 0))
    if len(a) < 15 or len(b) + 1 < 10:
        return []
    avg_a, avg_b = ra / oa, rb / ob
    sr_a = 100 * ra / max(1, a.balls.sum())
    sr_b = 100 * rb / max(1, b.balls.sum() + (p.balls or 0))
    out = []
    P = fmt_name(p)
    since = f"{cut:%b %Y}"
    if p.fmt in ("T20I", "T20", "ODI") and sr_b - sr_a >= (20 if p.fmt != "ODI" else 15) and avg_b >= avg_a * 0.9:
        out.append(F("shift", f"since {since} he strikes at {sr_b:.0f} {in_(p)}, up from {sr_a:.0f} before", strength=int((sr_b - sr_a) / 4)))
    elif avg_b >= avg_a * 1.4 and avg_b - avg_a >= 12:
        out.append(F("shift", f"since {since} he averages {avg_b:.1f} {in_(p)}, up from {avg_a:.1f} before", strength=int((avg_b - avg_a) / 4)))
    return out


def shift_bowl(ctx, p):
    h = ctx.scope(ctx.hw, p)
    me = h[h.player_id == p.pid]
    cut = p.date - pd.Timedelta(days=365)
    a, b = me[me.date < cut], me[me.date >= cut]
    if len(a) < 15 or len(b) + 1 < 8:
        return []
    wa, wb = a.wkts.sum(), b.wkts.sum() + p.wkts
    if wa < 10 or wb < 8:
        return []
    ave_a, ave_b = a.runs.sum() / wa, (b.runs.sum() + p.conceded) / wb
    if ave_b <= ave_a * 0.7 and ave_a - ave_b >= 6:
        return [F("shift", f"since {cut:%b %Y} he averages {ave_b:.1f} with the ball {in_(p)}, down from {ave_a:.1f} before",
                  strength=int((ave_a - ave_b) / 2))]
    return []


# ======================================================================= quirks
def quirks_bat(ctx, p):
    out = []
    h = ctx.scope(ctx.hb, p)
    me = h[h.player_id == p.pid]
    P = fmt_name(p)
    if p.out and 40 <= p.runs <= 99:
        same = me[(me.runs == p.runs) & (me.is_out == 1)]
        if len(same) >= 1:
            n = len(same) + 1
            special = p.runs in (66, 77, 88, 99, 69, 50, 90)
            out.append(F("quirk", f"the {ordn(n)} time he's been dismissed for {p.runs} {in_(p)}",
                         strength=(4 * n if n >= 3 else -8) + (10 if special else 0)))
    if p.out and 90 <= p.runs <= 99:
        n = len(me[(me.runs >= 90) & (me.runs <= 99) & (me.is_out == 1)]) + 1
        if n >= 3:
            nat = h[(h.team == p.team) & (h.runs >= 90) & (h.runs <= 99) & (h.is_out == 1)].groupby("player_id").size().drop(p.pid, errors="ignore")
            most = n > (nat.max() if len(nat) else 0)
            out.append(F("quirk", f"his {ordn(n)} dismissal in the 90s {in_(p)}"
                                  + (f" — the most by {art_of(who(p))} {era(p, ctx)}" if most else ""), strength=2 * n))
    out += birthday(ctx, p, p.runs >= 50)
    out += landmark_match(ctx, p, ctx.hb, p.runs >= 50)
    return out


def quirks_bowl(ctx, p):
    out = []
    h = ctx.scope(ctx.hw, p)
    me = h[h.player_id == p.pid]
    if p.wkts >= 3:
        same = me[(me.wkts == p.wkts) & (me.runs == p.conceded)]
        if len(same):
            first = same.date.min()
            out.append(F("quirk", f"identical figures of {p.wkts}/{p.conceded} to his spell against {same.sort_values('date').iloc[-1].opp} in {when(same.date.max())}",
                         strength=4))
    out += birthday(ctx, p, p.wkts >= 3)
    out += landmark_match(ctx, p, ctx.hw, p.wkts >= 3)
    return out


def birthday(ctx, p, good):
    dob = ctx.dob(p.pid)
    if good and dob is not None and dob.month == p.date.month and dob.day == p.date.day:
        age = p.date.year - dob.year
        return [F("quirk", f"on his {ord_short(age)} birthday", strength=10)]
    return []


def landmark_match(ctx, p, df, good):
    if not good:
        return []
    h = ctx.scope(df, p)
    n = h[h.player_id == p.pid].match_id.nunique() + 1
    if n in (50, 100, 150, 200, 250, 300) or (n % 100 == 0):
        return [F("quirk", f"in his {ord_short(n)} {fmt_name(p)}", strength=8 if n >= 100 else -8)]
    return []


# ======================================================================= lone hand
def lone_hand_bat(ctx, p):
    if p.won is not False or p.runs < 100:
        return []
    h = ctx.scope(ctx.hb, p)
    lost = h[(h.team == p.team) & (h.won == False) & (h.runs >= p.runs)]  # noqa: E712
    P = fmt_name(p)
    if len(lost):
        last = lost.sort_values("date").iloc[-1]
        if (p.date - last.date).days >= 2 * 365:
            return [F("lone_hand", f"the highest {P} score by {art_of(who(p))} in a defeat since {ctx.nm(last.player_id, last.player)}'s "
                                   f"{last.runs} in {when(last.date)}", strength=6)]
        return []
    return [F("lone_hand", f"the highest {P} score by {art_of(who(p))} in a losing cause {era(p, ctx)}", strength=10)]


def lone_hand_bowl(ctx, p):
    out = []
    if p.won is False and p.wkts >= 5:
        h = ctx.scope(ctx.hw, p)
        me = h[(h.player_id == p.pid) & (h.won == False) & (h.wkts >= 5)]  # noqa: E712
        n = len(me) + 1
        if n >= 3:
            out.append(F("lone_hand", f"his {ordn(n)} five-for in a defeat", strength=2 * n))
    # run of wickets while the team keeps losing (Abbas: 38 wickets in 7 Tests, lost all 7)
    if p.won is False:
        h = ctx.scope(ctx.hw, p)
        me = h[h.player_id == p.pid].groupby("match_id").agg(date=("date", "max"), w=("wkts", "sum"), r=("runs", "sum"),
                                                             won=("won", "max")).sort_values("date")
        k, w, r = 1, p.wkts, p.conceded
        for m in reversed(list(me.itertuples())):
            if m.won is False or m.won == 0:
                k += 1
                w += m.w
                r += m.r
            else:
                break
        if k >= 4 and w >= {"Test": 5, "ODI": 2.5, "T20I": 2, "T20": 2}[p.fmt] * k:
            out.append(F("lone_hand", f"{w} wickets at {r / max(1, w):.1f} in his last {k} {plural(p)} — and {p.team} lost every one",
                         strength=10))
    return out


# ======================================================================= splits (bowling type / phase)
def split_bat(ctx, p):
    if ctx.sb is None or p.fmt == "Test" or p.runs < 50:
        return []
    s = ctx.sb[(ctx.sb.format == p.fmt) & (ctx.sb.gender == p.gender) & (ctx.sb.yr >= p.date.year - 1)]
    if p.fmt == "T20":
        s = s[s.competition == p.comp]
    btype = {"off-spin": "spin", "leg-spin": "spin", "left-arm spin": "spin", "left-arm wrist-spin": "spin", "spin": "spin",
             "right-arm pace": "pace", "left-arm pace": "pace"}
    s = s.assign(grp=s.vs.map(btype))
    out = []
    for grp in ("spin", "pace"):
        g = s[s.grp == grp].groupby("player_id")[["runs", "balls", "outs"]].sum()
        g = g[g.balls >= (150 if p.fmt != "ODI" else 250)]
        if p.pid not in g.index or len(g) < 15:
            continue
        g["sr"] = 100 * g.runs / g.balls
        rank = int((g.sr > g.at[p.pid, "sr"]).sum()) + 1
        if rank <= 3:
            out.append(F("split", f"since the start of {p.date.year - 1}, {'no one' if rank == 1 else f'only {rank - 1} batter' + ('s' if rank > 2 else '')} "
                                  f"{'strikes faster' if rank == 1 else 'strike faster'} against {grp} {in_(p)} "
                                  f"(min {150 if p.fmt != 'ODI' else 250} balls): {g.at[p.pid, 'sr']:.0f}", strength=8 if rank == 1 else 2))
    return out


def split_dismissal(ctx, p):
    """Batter out to a bowling type that keeps getting him."""
    if not p.out or not p.out_bowler or ctx.sb is None:
        return []
    _, det = ctx.bdetail(p.out_bowler)
    if not det or det in ("right-arm pace", "spin"):
        return []
    s = ctx.sb[(ctx.sb.player_id == p.pid) & (ctx.sb.format == p.fmt) & (ctx.sb.gender == p.gender) & (ctx.sb.yr >= p.date.year - 1)]
    tot = s.groupby("vs")[["runs", "balls", "outs"]].sum()
    if det not in tot.index:
        return []
    n = int(tot.at[det, "outs"]) + 1
    allr, allo = tot.runs.sum(), max(1, tot.outs.sum())
    avg = (tot.at[det, "runs"] + (p.runs or 0)) / n
    if n >= 6 and avg <= 0.7 * (allr / allo):
        return [F("split", f"his {ordn(n)} {fmt_name(p)} dismissal to {det} since the start of {p.date.year - 1}, "
                           f"averaging {avg:.1f} against it ({allr / allo:.1f} against everything else)", strength=n)]
    return []


def split_bowl(ctx, p):
    if ctx.sw is None or p.fmt == "Test":
        return []
    out = []
    s = ctx.sw[(ctx.sw.format == p.fmt) & (ctx.sw.gender == p.gender) & (ctx.sw.yr == p.date.year)]
    if p.fmt == "T20":
        s = s[s.competition == p.comp]
    for phase, got in (("powerplay", p.pp_wkts), ("death", p.death_wkts)):
        if not got or got < 2:
            continue
        g = s[s.phase == phase].groupby("player_id").wkts.sum()
        mine = int(g.get(p.pid, 0)) + got
        others = g.drop(p.pid, errors="ignore")
        if mine >= 8 and mine > (others.max() if len(others) else 0):
            out.append(F("split", f"{mine} {phase} wickets {in_(p)} this year — the most by anyone", strength=6))
    return out


# ======================================================================= duels
def duel_for_dismissal(ctx, bowler_pid, batter_pid, bowler_name, batter_name, gender, date, league=False):
    """Facts about a bowler who has dismissed this batter repeatedly: internationals, or all T20s for league games."""
    if ctx.duel is None or not bowler_pid or not batter_pid:
        return []
    fmts = ["T20I", "T20"] if league else ["Test", "ODI", "T20I"]
    where = "in T20 cricket" if league else "in internationals"
    d = ctx.duel[(ctx.duel.batter_id == batter_pid) & (ctx.duel.gender == gender) & (ctx.duel.format.isin(fmts))]
    if not len(d):
        return []
    by = d.groupby("bowler_id").outs.sum()
    n = int(by.get(bowler_pid, 0)) + 1
    if n < 4:
        return []
    others = by.drop(bowler_pid, errors="ignore")
    most = n > (others.max() if len(others) else 0)
    r = d[d.bowler_id == bowler_pid]
    txt = (f"{ctx.nm(bowler_pid, bowler_name)} has now dismissed {ctx.nm(batter_pid, batter_name)} {n} times {where}"
           + (" — more than any other bowler" if most else "")
           + f" ({int(r.runs.sum())} runs off {int(r.balls.sum())} balls between them)")
    return [F("duel", txt, strength=2 * n + (6 if most else 0))]


# ======================================================================= age
def age_facts(ctx, p, df, cond, noun):
    dob = ctx.dob(p.pid)
    if dob is None or is_league(p):
        return []
    age = (p.date - dob).days
    h = ctx.scope(df, p)
    hit = h[(h.team == p.team) & cond(h)]
    if not len(hit) or ctx.meta is None:
        return []
    dobs = ctx.meta.dob.reindex(hit.player_id).values
    hit = hit.assign(age=(hit.date.values - dobs).astype("timedelta64[D]").astype(float))
    hit = hit[hit.age.notna()]
    out = []
    y = age // 365
    for mpid, _, v in p.extra.get("mates_bat" if df is ctx.hb else "mates_bowl", []):
        md = ctx.dob(mpid)
        if md is not None and cond(pd.DataFrame({"runs": [v], "wkts": [v]})).iloc[0]:
            if age > 35 * 365 and md < dob:
                return []
            if age < 23 * 365 and md > dob:
                return []
    if age < 23 * 365:
        younger = hit[hit.age < age].sort_values("date")
        if not len(younger) and len(hit) >= 10:
            verb = "score" if "hundred" in noun or "fifty" in noun else "take"
            ev = era(p, ctx)
            out.append(F("age", f"at {y}, the youngest {who(p)} to {verb} {noun}{' ever' if ev == 'ever' else f' ({ev})'}", strength=10))
        elif len(younger):
            last = younger.iloc[-1]
            if (p.date - last.date).days >= 3 * 365:
                verb = "score" if "hundred" in noun or "fifty" in noun else "take"
                out.append(F("age", f"at {y}, the youngest {who(p)} to {verb} {noun} since {ctx.nm(last.player_id, last.player)} in {last.date.year}",
                             strength=6))
    if age > 35 * 365:
        older = hit[hit.age > age]
        if not len(older) and len(hit) >= 10:
            verb = "score" if "hundred" in noun or "fifty" in noun else "take"
            ev = era(p, ctx)
            out.append(F("age", f"at {y}, the oldest {who(p)} to {verb} {noun}{' ever' if ev == 'ever' else f' ({ev})'}", strength=8))
    return out


# ======================================================================= slumps (bad days for big players)
NO_FIFTY_MIN = {"ODI": 8, "T20I": 10, "Test": 8, "T20": 10}
LOW = {"ODI": 20, "T20I": 15, "Test": 20, "T20": 15}
EXPENSIVE = {"ODI": 70, "T20I": 45, "Test": 130, "T20": 45}


def slump_bat(ctx, p):
    if not p.star or not p.out or p.runs >= 50:
        return []
    h = ctx.scope(ctx.hb, p)
    me = h[h.player_id == p.pid].sort_values(["date", "innings"])
    prior = me.runs.tolist()
    out = []
    gap = 1
    for r in reversed(prior):
        if r < 50:
            gap += 1
        else:
            break
    P = fmt_name(p)
    top_order = (p.pos or 3) <= 5
    if gap >= NO_FIFTY_MIN[p.fmt] and top_order:
        longest = cur = 0
        for r in prior:
            cur = cur + 1 if r < 50 else 0
            longest = max(longest, cur)
        last50 = me[me.runs >= 50].date.max()
        if gap > longest and gap >= NO_FIFTY_MIN[p.fmt] + 3:
            out.append(F("slump", f"the longest run without {art2(p)} fifty of his career: {gap} innings", strength=12))
        elif pd.notna(last50):
            out.append(F("slump", f"no {P} fifty since {when(last50)}: {gap} innings", strength=4))
    if p.runs == 0:
        ducks = int(((me.runs == 0) & (me.is_out == 1)).sum()) + 1
        prev = len(me) and me.iloc[-1].runs == 0 and me.iloc[-1].is_out == 1
        out.append(F("slump", f"a{' golden' if (p.balls or 0) <= 1 else ''} duck, his {ordn(ducks)} {in_(p)}"
                              + (", and his second in a row" if prev else ""), strength=10 if prev else -12))
    if p.runs < LOW[p.fmt]:
        last = me[me.is_out == 1].runs.tolist()[-7:] + [p.runs]
        streak = 0
        for r in reversed(last):
            if r < LOW[p.fmt] + 5:
                streak += 1
            else:
                break
        if streak >= 4:
            out.append(F("slump", f"his last {streak} {P} innings: {', '.join(str(x) for x in last[-streak:])}", strength=streak - 4))
    return out


def slump_bowl(ctx, p):
    if not p.star:
        return []
    out = []
    h = ctx.scope(ctx.hw, p)
    me = h[h.player_id == p.pid]
    P = fmt_name(p)
    if p.conceded >= EXPENSIVE[p.fmt] and len(me) and p.conceded > me.runs.max():
        out.append(F("slump", f"the most runs he's ever conceded in {art(p)} (previous worst {int(me.runs.max())})", strength=10))
    if p.wkts == 0 and p.fmt != "Test":
        w = me.sort_values("date").wkts.tolist()
        st = 1
        for x in reversed(w):
            if x == 0:
                st += 1
            else:
                break
        if st >= 3:
            out.append(F("slump", f"wicketless in {st} {plural(p)} in a row", strength=st))
    return out


# ======================================================================= entry points
def batting(ctx, p):
    """-> subject, facts"""
    no = "" if p.out else "*"
    subject = f"{p.name} {p.runs}{no} ({p.balls})"
    facts = []
    h = ctx.scope(ctx.hb, p)
    P = fmt_name(p)
    if p.runs >= 100:
        lvl = 200 if p.runs >= 200 else 150 if p.runs >= 150 else 100
        noun = {200: "double hundred", 150: "score of 150+", 100: "hundred"}[lvl]
        verb = {200: f"score {art2(p)} double hundred", 150: f"score 150+ in {art(p)}", 100: f"score {art2(p)} hundred"}[lvl]
        hit = h[h.runs >= lvl]
        facts += drought(ctx, p, hit, verb, f"{P} {noun}", [n for _, n, r in p.extra.get("mates_bat", []) if r >= lvl])
        mine = hit[hit.player_id == p.pid]
        if not len(mine):
            facts.append(F("comeback", f"his maiden {P} {noun}", bonus=-10 + (10 if lvl >= 150 else 0)))
        facts += age_facts(ctx, p, ctx.hb, lambda d: d.runs >= 100, f"{art2(p)} hundred")
    if p.runs >= 50:
        facts += form_bat(ctx, p)
        facts += company(ctx, p, "bat")
        facts += context_bat(ctx, p)
        facts += leaderboard(ctx, p, "bat")
        facts += shift_bat(ctx, p)
        facts += split_bat(ctx, p)
        facts += lone_hand_bat(ctx, p)
        if ctx.legends is not None and not is_league(p):
            for stat, got in (("runs", p.runs), ("hundreds", int(p.runs >= 100)), ("fifty_plus", int(p.runs >= 50)),
                              ("sixes", p.sixes if (p.sixes or 0) >= 3 else 0)):
                facts += [(BASE["legend"] + b, "legend", t) for b, a, t in
                          ctx.legends.facts(p.pid, p.name, p.team, p.fmt, p.gender, stat, got or 0, ctx.hb, ctx.hw, p.match_id,
                                            lambda a, t, b=0: (b, a, t), match_date=p.date)]
    facts += quirks_bat(ctx, p)
    facts += slump_bat(ctx, p)
    facts += split_dismissal(ctx, p)
    if p.out and p.out_bowler and p.star:
        facts += duel_for_dismissal(ctx, p.out_bowler, p.pid, p.extra.get("out_bowler_name", ""), p.name, p.gender, p.date, is_league(p))
    from . import analysis as AN
    facts = AN.finish(ctx, p, facts + AN.extra_bat(ctx, p), "bat")
    if p.star:
        facts = [(s + 6, f, t) for s, f, t in facts]
    return subject, facts


def bowling(ctx, p):
    subject = f"{p.name} {p.wkts}/{p.conceded}"
    facts = []
    h = ctx.scope(ctx.hw, p)
    P = fmt_name(p)
    haul = 5 if p.fmt == "Test" else 4
    if p.wkts >= haul:
        lvl = 5 if p.wkts >= 5 else 4
        short = "five-for" if lvl == 5 else "four-wicket haul"
        hit = h[h.wkts >= lvl]
        facts += drought(ctx, p, hit, f"take {art2(p)} {short}", f"{P} {short}", [n for _, n, w in p.extra.get("mates_bowl", []) if w >= lvl])
        if not len(hit[hit.player_id == p.pid]):
            facts.append(F("comeback", f"his maiden {P} {short}", bonus=-8))
        facts += age_facts(ctx, p, ctx.hw, lambda d: d.wkts >= lvl, f"{art2(p)} {short}")
    if p.wkts >= 3:
        facts += form_bowl(ctx, p)
        facts += company(ctx, p, "bowl")
        facts += context_bowl(ctx, p)
        facts += leaderboard(ctx, p, "bowl")
        facts += shift_bowl(ctx, p)
        facts += split_bowl(ctx, p)
        facts += lone_hand_bowl(ctx, p)
        if ctx.legends is not None and not is_league(p):
            for stat, got in (("wkts", p.wkts), ("fivefors", int(p.wkts >= 5)), ("fourplus", int(p.wkts >= 4))):
                facts += [(BASE["legend"] + b, "legend", t) for b, a, t in
                          ctx.legends.facts(p.pid, p.name, p.team, p.fmt, p.gender, stat, got, ctx.hb, ctx.hw, p.match_id,
                                            lambda a, t, b=0: (b, a, t), match_date=p.date)]
        for d in p.extra.get("dismissed", []):
            facts += duel_for_dismissal(ctx, p.pid, d["pid"], p.name, d["name"], p.gender, p.date, is_league(p))
    facts += quirks_bowl(ctx, p)
    facts += slump_bowl(ctx, p)
    from . import analysis as AN
    facts = AN.finish(ctx, p, facts + AN.extra_bowl(ctx, p), "bowl")
    if p.star:
        facts = [(s + 6, f, t) for s, f, t in facts]
    return subject, facts


def allround(ctx, p):
    """Same-match double: runs and wickets (p.match_runs / p.match_wkts)."""
    r, w = p.match_runs or 0, p.match_wkts or 0
    need = {"Test": (100, 5), "ODI": (50, 3), "T20I": (30, 3), "T20": (30, 3)}[p.fmt]
    if r < need[0] or w < need[1]:
        return None, []
    df_b = ctx.scope(ctx.hb, p).groupby(["match_id", "player_id"]).agg(runs=("runs", "sum"), date=("date", "max"),
                                                                       player=("player", "last"), team=("team", "last"), opp=("opp", "last"))
    df_w = ctx.scope(ctx.hw, p).groupby(["match_id", "player_id"]).wkts.sum()
    j = df_b.join(df_w, how="inner").reset_index()
    prior = j[(j.runs >= r) & (j.wkts >= w)]
    P = fmt_name(p)
    phrase = f"score {r}+ runs and take {w}+ wickets in the same {P}" if p.fmt != "T20" else f"score {r}+ and take {w}+ wickets in the same {P} match"
    ev = era(p, ctx)
    tail = "" if ev == "ever" else f" ({ev})"
    facts = []
    n = prior.player_id.nunique()
    if n == 0:
        facts.append(F("company", f"the first player to {phrase}{' ever' if ev == 'ever' else tail}", strength=16))
    elif n == 1:
        x = prior.iloc[0]
        facts.append(F("company", f"the only other player to {phrase}{tail} is {ctx.nm(x.player_id, x.player)} ({x.team} v {x.opp}, {when(x.date)})", strength=12))
    elif n <= 5:
        facts.append(F("company", f"only the {ordn(n + 1)} player to {phrase}{tail}", strength=8))
    nat = prior[prior.team == p.team] if not is_league(p) else prior.iloc[0:0]
    if not is_league(p) and n and len(nat) == 0:
        facts.append(F("company", f"the first {who(p)} to {phrase}{tail}", strength=8))
    return f"{p.name}: {r} runs and {w} wickets", facts


def key_of(text):
    return hashlib.md5(text.encode()).hexdigest()[:10]
