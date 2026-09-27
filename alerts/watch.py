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
        both = set(plain) <= C.FULL_MEMBERS
        if not both if C.INTERNATIONALS_ONLY else not (set(plain) & C.FULL_MEMBERS):
            return None, None, [], []
        scopes.append(f"{sex} {f}")
        fmt = "T20" if f == "T20I" else f
    elif C.INTERNATIONALS_ONLY:
        return None, None, [], []
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
    if fmt == "T20" and not C.INTERNATIONALS_ONLY:
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
    from .nuggets import load_history
    hist = load_history()
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
            from . import nuggets as N
            fN = {"T20": "T20I"}.get(fmt, fmt)
            for r in careers[careers.scope.isin(scopes) & careers.player_id.isin(ids)].itertuples():
                kind = _kind(r.scope)
                if hist is not None and not N.is_star(r.player_id, fN, gender, hist[0], hist[1]):
                    continue
                for stat, val in (("runs", int(r.runs)), ("wickets", int(r.wkts))):
                    mark, low = _next_mark(val, stat, kind, fmt)
                    need = mark - val
                    thr = (C.WATCH_RUNS if stat == "runs" else C.WATCH_WKTS)[fmt]
                    if mark < low or need > thr or (stat == "wickets" and val == 0):
                        continue
                    if C.INTERNATIONALS_ONLY and mark < N.BIG_MILESTONE[stat]:
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
            if hist is not None:
                from . import nuggets as N
                if not N.is_star(pid, {"T20": "T20I"}.get(fmt, fmt), gender, hist[0], hist[1]):
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
                    if C.INTERNATIONALS_ONLY and mark < N.BIG_MILESTONE[stat]:
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
        # ---- stat nuggets for big performances (internationals)
        if hist is not None and fmt:
            alerts += _nuggets(ev, fmt, gender, teams, ci2cs, dn, hist, sent, seen)
        if ev["status"] == "post":
            sent[fkey] = now.isoformat()
    return alerts


def _nuggets(ev, fmt, gender, teams, ci2cs, dn, hist, sent, seen):
    from . import nuggets as N
    hb, hw, ht = hist
    f = {"T20": "T20I"}.get(fmt, fmt)
    d = espn.detail(ev)
    tag = "#" + ev["short"].replace(" ", "") if ev.get("short") else "#Cricket"
    upto = pd.to_datetime(hb.date).max()
    out, quiet = [], []

    def fix(txt):  # women's cricket
        return txt.replace("his ", "her ").replace(" him", " her").replace("he's", "she's") if gender == "female" else txt

    def add(key, subject, facts, label, ci=None):
        if not facts or key in sent or key in seen:
            return
        seen.add(key)
        facts = [(sc, ang, fix(t)) for sc, ang, t in facts]
        best = max(sc for sc, _, _ in facts)
        if best < N.LEAD_MIN:
            quiet.append((key, f"{subject}: " + "; ".join(t for _, _, t in sorted(facts, key=lambda x: -x[0])[:2])))
            return
        out.append(dict(kind="nugget", key=key, competition=f"{'Men' if gender == 'male' else 'Women'}'s {f}",
            headline=f"{subject} · {label}", match=ev["name"], date=ev["date"][:10], match_id=ev["id"],
            note="\n".join(f"• {t}" for _, _, t in sorted(facts, key=lambda x: -x[0]))
                 + f"\n<i>History: ball-by-ball records {N.COVER[gender]}–{upto:%d %b %Y}; matches after that not yet counted.</i>",
            caption=N.caption(subject, facts, tag), priority=best, cricinfo_player=ci))

    host = N.host_for(ev["city"], _plain(ev.get("home") or ""), ht)
    for ci, p in d["players"].items():
        pid = ci2cs.get(ci)
        if not pid:
            continue
        team = _plain(p["team"])
        opp = next((t for t in teams if t != team), None)
        name = dn.get(pid, p["name"])
        star = N.is_star(pid, f, gender, hb, hw)
        for inn, w, c in p["bowl"]:
            subj, facts = N.bowling_facts(pid, name, team, opp, f, gender, host, ev["city"], w, c, ev["id"], hb, hw, star)
            if facts:
                lvl = f"{5 if w >= 5 else 4 if w >= 4 else 0}:{c // 20}"
                add(f"nug:{ev['id']}:{pid}:bowl:{inn}:{lvl}", subj, facts,
                    "bad day" if all(a in ("star_expensive", "wicketless_streak") for _, a, _ in facts) else "wicket haul", ci)
        for inn, r, b, o in p["bat"]:
            subj, facts = N.batting_facts(pid, name, team, opp, f, gender, host, ev["city"], r, b, o, ev["id"], hb, star)
            if facts:
                lvl = 200 if r >= 200 else 150 if r >= 150 else 100 if r >= 100 else 50 if r >= 50 else f"low{o}"
                add(f"nug:{ev['id']}:{pid}:bat:{inn}:{lvl}", subj, facts,
                    "bad day" if all(a in ("star_duck", "star_slump") for _, a, _ in facts) else "batting", ci)
        # passing big names on the nation's list (stars only)
        if star:
            me_b = hb[(hb.player_id == pid) & (hb.format == f) & (hb.gender == gender) & (hb.match_id.astype(str) != ev["id"])]
            me_w = hw[(hw.player_id == pid) & (hw.format == f) & (hw.gender == gender) & (hw.match_id.astype(str) != ev["id"])]
            for stat, before, got, hh in (("runs", int(me_b.runs.sum()), p["runs"], hb), ("wickets", int(me_w.wkts.sum()), p["wkts"], hw)):
                if got > 0:
                    facts = N.passes(pid, name, team, f, gender, stat, before, before + got, hh)
                    if facts:
                        add(f"nug:{ev['id']}:{pid}:pass:{stat}:{facts[0][2][:40]}", f"{name}", facts, "all-time list", ci)
    # team facts once the match is over
    if ev["status"] == "post" and d["winner"]:
        winner = _plain(d["winner"])
        loser = next((t for t in teams if t != winner), None)
        if loser:
            facts = N.team_facts(winner, loser, f, gender, host, ev["id"], ht)
            add(f"nug:{ev['id']}:team", "", facts, f"{winner} beat {loser}")
    # quiet digest of lower-tier facts, once per match at the end
    dkey = f"digest:{ev['id']}"
    if ev["status"] == "post" and quiet and dkey not in sent:
        out.append(dict(kind="digest", key=dkey, competition=f, headline=f"Other notes: {ev['name']}",
            match=ev["name"], date=ev["date"][:10], match_id=ev["id"],
            note="\n".join(f"• {t}" for _, t in quiet[:15]), caption=f"{ev['name']} notes\n\n{tag}", priority=10))
        for k, _ in quiet:
            sent[k] = pd.Timestamp.now().isoformat()
    return out
