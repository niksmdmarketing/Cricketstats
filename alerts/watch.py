"""Watch list (players close to milestones in today's matches) and live milestone checks,
using ESPN's public cricket feed plus Cricsheet-based career totals."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import config as C
from . import espn
from .stats import scope_name

ROOT = Path(__file__).resolve().parents[1]
INTL = {"1": ("Test", "male"), "2": ("ODI", "male"), "3": ("T20I", "male"),
        "8": ("Test", "female"), "9": ("ODI", "female"), "10": ("T20I", "female")}
LEAGUE_KEYS = [
    ("women's big bash", "Women's Big Bash League"), ("big bash", "Big Bash League"),
    ("indian premier league", "Indian Premier League"), ("pakistan super league", "Pakistan Super League"),
    ("caribbean premier league", "Caribbean Premier League"), ("sa20", "SA20"),
    ("international league t20", "International League T20"), ("ilt20", "International League T20"),
    ("major league cricket", "Major League Cricket"), ("women's premier league", "Women's Premier League"),
    ("vitality blast", "T20 Blast"), ("t20 blast", "T20 Blast"),
    ("bangladesh premier league", "Bangladesh Premier League"), ("lanka premier league", "Lanka Premier League"),
    ("super smash", "Super Smash"),
]


def _plain(team):
    return re.sub(r"\s*Women\b", "", team or "").strip()


def classify(ev):
    """-> (fmt 'Test'|'ODI'|'T20', gender, [scopes], [plain team names]) or (None, None, [], [])"""
    teams = ev["teams"]
    if len(teams) != 2:
        return None, None, [], []
    gender = "female" if any(re.search(r"\bWomen\b", t) for t in teams) else "male"
    plain = [_plain(t) for t in teams]
    sex = "Men's" if gender == "male" else "Women's"
    scopes = []
    if ev["intl_class"] in INTL:
        f, gender = INTL[ev["intl_class"]]
        sex = "Men's" if gender == "male" else "Women's"
        if not (set(plain) & C.FULL_MEMBERS):
            return None, None, [], []
        scopes.append(f"{sex} {f}")
        fmt = "T20" if f == "T20I" else f
    else:
        text = f"{ev['league']} {ev['description']}".lower()
        if re.search(r"under-?19|u19", text):
            return None, None, [], []
        comp = None
        if "the hundred" in text:
            comp = f"The Hundred {'Men' if gender == 'male' else 'Women'}'s Competition"
        else:
            for key, c in LEAGUE_KEYS:
                if key in text:
                    comp = c
                    break
        if not comp:
            return None, None, [], []
        scopes.append(comp)
        fmt = "T20"
    if fmt == "T20":
        scopes.append(f"T20 (all, {'men' if gender == 'male' else 'women'})")
    return fmt, gender, scopes, plain


def _kind(scope):
    if scope.startswith("T20 (all"):
        return "t20_all"
    return "league" if scope in C.T20_LEAGUES else "international"


def _next_mark(val, stat, kind, fmt):
    rs, ws = C.MILESTONES[kind]
    step = rs if stat == "runs" else ws
    low = C.MIN_MILESTONE[kind][0 if stat == "runs" else 1]
    mark = (val // step + 1) * step
    return mark, low


def display_names():
    f = ROOT / "state" / "names.csv"
    if not f.exists():
        return {}
    m = pd.read_csv(f, dtype=str)
    m = m.dropna()
    return dict(zip(m.cricsheet_id, m.name))


def ci_map():
    f = ROOT / "state" / "people.csv"
    p = pd.read_csv(f, dtype=str)
    m = {}
    for col in [c for c in p.columns if c.startswith("key_cricinfo")]:
        for pid, ci in zip(p.identifier, p[col]):
            if isinstance(ci, str):
                m[ci] = pid
    return m


def run(careers, sent, now=None):
    """One pass: watch-list alerts for upcoming matches + live milestone alerts. Returns alerts."""
    now = now or datetime.now(timezone.utc)
    alerts = []
    seen = set()
    try:
        evs = espn.events()
    except Exception as e:  # noqa: BLE001
        print("ESPN events failed:", e)
        return alerts
    focus = ROOT / "state" / "focus_matches"
    if focus.exists():   # only follow the matches listed there
        keep = set(focus.read_text().split())
        evs = [e for e in evs if e["id"] in keep]
    ci2cs = ci_map()
    dn = display_names()
    cs2ci = {v: k for k, v in ci2cs.items()}
    careers = careers.copy()
    careers["last_match"] = pd.to_datetime(careers["last_match"])
    active_since = pd.Timestamp(now.date()) - pd.Timedelta(days=C.ACTIVE_DAYS)
    for ev in evs:
        fmt, gender, scopes, teams = classify(ev)
        if not scopes:
            continue
        try:
            start = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        except ValueError:
            continue
        mdate = pd.Timestamp(start.date())
        team_scopes = [s for s in scopes if not s.startswith("T20 (all")]

        # ---- watch list (once per match, before or early in the game)
        wkey = f"watch:{ev['id']}"
        if wkey not in sent and ev["status"] in ("pre", "in") and start <= now + timedelta(hours=26):
            pool = careers[careers.scope.isin(team_scopes) & careers.team.isin(teams) & (careers.last_match >= active_since)]
            ids = set(pool.player_id)
            items = []
            for r in careers[careers.scope.isin(scopes) & careers.player_id.isin(ids)].itertuples():
                kind = _kind(r.scope)
                for stat, val in (("runs", int(r.runs)), ("wickets", int(r.wkts))):
                    mark, low = _next_mark(val, stat, kind, fmt)
                    need = mark - val
                    thr = (C.WATCH_RUNS if stat == "runs" else C.WATCH_WKTS)[fmt]
                    if mark < low or need > thr or (stat == "wickets" and val == 0):
                        continue
                    items.append((need / thr, r, stat, val, mark, need, kind))
            items.sort(key=lambda x: x[0])
            if items:
                lines, first = [], None
                for _, r, stat, val, mark, need, kind in items[:8]:
                    fy = pd.Timestamp(r.first_match).year
                    warn = " ⚠️" if kind == "t20_all" or (kind != "league" and fy <= C.COVERAGE_START[r.gender]) else ""
                    lines.append(f"• {dn.get(r.player_id, r.player)} ({r.team}): needs {need} for {mark:,} {scope_name(r.scope)} {stat} (on {val:,}){warn}")
                    if first is None:
                        first = (r, stat, mark, need)
                r, stat, mark, need = first
                tease = (f"{dn.get(r.player_id, r.player)} needs {need} more to reach {mark:,} {scope_name(r.scope)} {stat}. "
                         f"Will it happen today in {ev['name']}? 👀\n\n#Cricket")
                alerts.append(dict(kind="watch", key=wkey, competition=ev["league"], match=ev["name"],
                    date=start.strftime("%d %b %H:%M UTC"), headline=f"Milestones in reach: {ev['name']}",
                    note="\n".join(lines) + ("\n⚠️ = career started before full data coverage; check the official total." if "⚠️" in "".join(lines) else ""),
                    caption=tease, priority=60, match_id=ev["id"],
                    cricinfo_player=cs2ci.get(r.player_id)))
            else:
                sent[wkey] = now.isoformat()   # nothing to watch; don't re-check this match

        # ---- live / just-finished: check milestone crossings from the real scorecard
        fkey = f"final:{ev['id']}"
        if ev["status"] not in ("in", "post") or fkey in sent:
            continue
        if ev["status"] == "post" and start < now - timedelta(days=6):
            continue
        try:
            sc = espn.scorecard(ev)
        except Exception as e:  # noqa: BLE001
            print("scorecard failed", ev["id"], e)
            continue
        for ci, s in sc.items():
            pid = ci2cs.get(ci)
            if not pid:
                continue
            rows = careers[(careers.player_id == pid) & careers.scope.isin(scopes)]
            for r in rows.itertuples():
                if r.last_match >= mdate:      # this match already in the Cricsheet totals
                    continue
                kind = _kind(r.scope)
                for stat, base, got in (("runs", int(r.runs), s["runs"]), ("wickets", int(r.wkts), s["wkts"])):
                    mark, low = _next_mark(base, stat, kind, fmt)
                    if got <= 0 or base + got < mark or mark < low:
                        continue
                    key = f"ms:{r.scope}:{pid}:{stat}:{mark}"
                    if key in sent or key in seen:
                        continue
                    seen.add(key)
                    sn = scope_name(r.scope)
                    fy = pd.Timestamp(r.first_match).year
                    partial = kind == "t20_all" or (kind != "league" and fy <= C.COVERAGE_START[r.gender])
                    alerts.append(dict(kind="live", key=key, competition=r.scope, player=s["name"],
                        headline=f"{s['name']} reaches {mark:,} {sn} {stat} (about {base + got:,})",
                        match=ev["name"], date=start.strftime("%d %b %Y"), match_id=ev["id"],
                        cricinfo_player=ci, needs_check=bool(partial),
                        note=f"Before this match: {base:,} (Cricsheet). This match: {got} {stat}. {ev['summary']}",
                        caption=f"{mark:,} {sn} {stat.upper()} 🙌\n\n{s['name']} brings up {mark:,} {sn} {stat} for {s['team']}.\n\n#Cricket",
                        priority=95))
        if ev["status"] == "post":
            sent[fkey] = now.isoformat()
    return alerts


def status(careers, event_ids):
    """Progress report for chosen matches: live score + every player chasing a milestone."""
    evs = [e for e in espn.events() if e["id"] in event_ids]
    ci2cs = ci_map()
    dn = display_names()
    careers = careers.copy()
    careers["last_match"] = pd.to_datetime(careers["last_match"])
    msgs = []
    for ev in evs:
        fmt, gender, scopes, teams = classify(ev)
        start = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        mdate = pd.Timestamp(start.date())
        sc = espn.scorecard(ev) if ev["status"] in ("in", "post") else {}
        rows = []
        for ci, s in sc.items():
            pid = ci2cs.get(ci)
            for r in careers[(careers.player_id == pid) & careers.scope.isin(scopes)].itertuples():
                if r.last_match >= mdate:
                    continue
                kind = _kind(r.scope)
                for stat, base, got in (("runs", int(r.runs), s["runs"]), ("wickets", int(r.wkts), s["wkts"])):
                    mark, low = _next_mark(base, stat, kind, fmt)
                    thr = (C.WATCH_RUNS if stat == "runs" else C.WATCH_WKTS)[fmt]
                    if mark < low or mark - base > thr or (stat == "wickets" and base == 0):
                        continue
                    left = mark - base - got
                    icon = "✅" if left <= 0 else "⏳"
                    rows.append((left, f"{icon} {dn.get(pid, s['name'])}: {got} {stat} today · "
                                       + (f"reached {mark:,} {scope_name(r.scope)} {stat}!" if left <= 0
                                          else f"{left} more for {mark:,} {scope_name(r.scope)} {stat}")))
        top = sorted(sc.values(), key=lambda v: -v["runs"])[:3]
        topb = sorted([v for v in sc.values() if v["wkts"]], key=lambda v: -v["wkts"])[:3]
        lines = [f"<b>{ev['name']}</b> · {ev['summary']}",
                 "Top bats: " + (", ".join(f"{v['name']} {v['runs']} ({v['balls']})" for v in top) or "-"),
                 "Top bowlers: " + (", ".join(f"{v['name']} {v['wkts']}w" for v in topb) or "-"),
                 "", "<b>Milestone chase</b>"] + ([t for _, t in sorted(rows)] or ["Nobody within range in this match."])
        msgs.append("\n".join(lines))
    return msgs
