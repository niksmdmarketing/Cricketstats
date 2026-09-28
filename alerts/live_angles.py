"""Turn a live/finished ESPN scorecard into stat-angle alerts (see angles.py and rotation.py)."""
from pathlib import Path

import pandas as pd

from . import angles as A
from . import config as C
from . import espn
from . import recent
from . import rotation as R

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
HIST = STATE / "hist"
STAR = {"ODI": (40, 1500, 50), "T20I": (40, 1000, 40), "Test": (25, 1500, 70), "T20": (30, 800, 35)}
_ctx = None


def load_context():
    """History (Cricsheet + recent ESPN innings), player metadata, all-time snapshot. Cached per run."""
    global _ctx
    if _ctx is not None:
        return _ctx
    from . import history, meta
    from .legends import Legends
    h = history.load(HIST if HIST.exists() else STATE)
    if h.get("hist_bat") is None:
        return None
    h = recent.merge(h)
    m = meta.load()
    try:
        ppl = pd.read_csv(STATE / "people.csv", dtype=str)
    except FileNotFoundError:
        ppl = None
    names = {}
    f = STATE / "names.csv"
    if f.exists():
        names = dict(pd.read_csv(f, dtype=str).dropna().values)
    _ctx = A.Ctx(h, m, Legends(ppl), names)
    return _ctx


def star(ctx, pid, fmt, gender, comp, kind):
    b = ctx.hb[(ctx.hb.player_id == pid) & (ctx.hb.format == fmt) & (ctx.hb.gender == gender)]
    w = ctx.hw[(ctx.hw.player_id == pid) & (ctx.hw.format == fmt) & (ctx.hw.gender == gender)]
    if fmt == "T20":
        b, w = b[b.competition == comp], w[w.competition == comp]
    s = STAR[fmt]
    if len(set(b.match_id) | set(w.match_id)) < s[0]:
        return False
    if kind == "bat":
        return b.runs.sum() >= s[1] and b.runs.sum() / max(1, b.is_out.sum()) >= (22 if fmt in ("T20I", "T20") else 28)
    return w.wkts.sum() >= s[2]


def fix(txt, gender):
    if gender != "female":
        return txt
    for a, b in ((" his ", " her "), ("His ", "Her "), (" him", " her"), ("he's", "she's"), (" he ", " she "),
                 ("He ", "She "), ("his ", "her ")):
        txt = txt.replace(a, b)
    return txt


def match_alerts(ev, fmt, gender, comp, teams, ci2cs, host, sent, seen, log, tag):
    ctx = load_context()
    if ctx is None:
        return []
    f = {"T20": "T20I"}.get(fmt, fmt) if comp and not comp in C.NUGGET_LEAGUES else "T20"
    if comp in C.NUGGET_LEAGUES:
        host = None
    d = espn.detail(ev)
    date = pd.Timestamp(ev["date"][:10])
    post = ev["status"] == "post" and d.get("winner")
    winner = _plain(d.get("winner")) if post else None
    out, quiet = [], []

    def base(pid, name, team):
        opp = next((t for t in teams if t != team), None)
        return dict(pid=pid, name=name, team=team, opp=opp, fmt=f, gender=gender, comp=comp, match_id=str(ev["id"]),
                    date=date, city=ev.get("city"), host=host, won=(team == winner) if winner else None)

    def emit(key, subject, facts, label, pid, ci):
        if not facts or key in sent or key in seen:
            return
        seen.add(key)
        charts = {fix(t, gender): ctx.charts[t] for _, _, t in facts if t in ctx.charts}
        facts = [(s, fam, fix(t, gender)) for s, fam, t in facts]
        lead, sup = R.pick(facts, log, pid)
        if lead is None:
            return
        others = [t for _, _, t in sorted(facts, key=lambda x: -x[0]) if t not in {lead[3]} | {s[3] for s in sup}][:5]
        if lead[0] < A.LEAD_MIN:
            quiet.append((key, f"{subject}: {lead[3]}"))
            return
        R.record(log, lead, pid, supports=sup)
        note = "\n".join(f"• {t}" for t in [lead[3]] + [s[3] for s in sup])
        idea = next((charts[f[3]] for f in [lead] + sup if f[3] in charts), None)
        if idea:
            note += f"\n📊 <b>Chart idea:</b> {idea[0]}\n<i>Data: {idea[1]}</i>"
        if others:
            note += "\n<b>Other angles</b>\n" + "\n".join(f"◦ {t}" for t in others)
        out.append(dict(kind="nugget", key=key, competition=comp, headline=f"{subject} · {label}", match=ev["name"],
                        date=ev["date"][:10], match_id=ev["id"], note=note,
                        caption=fix(R.caption(subject, lead, sup, tag), gender), priority=int(lead[0]),
                        cricinfo_player=ci, family=lead[2]))

    players = d["players"]
    remember_names({ci2cs[c]: p_["name"] for c, p_ in players.items() if c in ci2cs and p_.get("name")})
    stage = _stage(ev)
    tot_runs = sum(x["runs"] for p_ in players.values() for x in p_.get("batx", []))
    tot_balls = sum(x["balls"] or 0 for p_ in players.values() for x in p_.get("batx", []))
    totals = {(_plain(t), per): r for t, per, r, w, ov, tg in d.get("innings", [])}
    lim = {"ODI": 50, "T20I": 20, "T20": 20}.get(f)

    def _ov(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0
    complete = {(_plain(t), per): (w >= 10 or (lim is not None and _ov(ov) >= lim)) for t, per, r, w, ov, tg in d.get("innings", [])}

    def entry(team, inn, pos):
        if not pos:
            return None, None
        if pos <= 2:
            return 0, 0
        for p_ in players.values():
            if _plain(p_["team"]) != team:
                continue
            for x in p_.get("batx", []):
                if x["inn"] == inn and x.get("fow") and x["fow"][0] == pos - 2:
                    return x["fow"][1], x["fow"][0]
        return None, None

    def mates_of(team, me, kind):
        out_ = []
        for c2, q in players.items():
            if c2 == me or _plain(q["team"]) != team or not ci2cs.get(c2):
                continue
            if kind == "bat":
                v = max([x["runs"] for x in q.get("batx", [])] or [0])
            else:
                v = max([x["wkts"] for x in q.get("bowlx", [])] or [0])
            out_.append((ci2cs[c2], q["name"], v))
        return out_

    for ci, p in players.items():
        pid = ci2cs.get(ci)
        if not pid:
            continue
        team = _plain(p["team"])
        name = p["name"]
        sb = star(ctx, pid, f, gender, comp, "bat")
        sw = star(ctx, pid, f, gender, comp, "bowl")
        mr = mw = 0
        for bx in p.get("batx", []):
            mr += bx["runs"]
            if bx["runs"] < 50 and not (sb and bx["out"] and bx["runs"] < 15):
                continue
            chasing = (bx["inn"] == 2) if f != "Test" else (bx["inn"] == 4)
            perf = A.Perf(**base(pid, name, team), inn=bx["inn"], runs=bx["runs"], balls=bx["balls"], out=bx["out"],
                          pos=bx["pos"], fours=bx["fours"], sixes=bx["sixes"], kind=bx["kind"],
                          out_bowler=ci2cs.get(bx["bowler"]) if bx["bowler"] else None, chasing=chasing, star=sb)
            if bx["bowler"] and bx["bowler"] in players:
                perf.extra["out_bowler_name"] = players[bx["bowler"]]["name"]
            mates = [x["runs"] for q in players.values() if _plain(q["team"]) == team and q is not p
                     for x in q.get("batx", []) if x["inn"] == bx["inn"]]
            es, ew = entry(team, bx["inn"], bx["pos"])
            perf.extra["mates_bat"] = mates_of(team, ci, "bat")
            perf.extra.update(team_total=totals.get((team, bx["inn"])), innings_complete=complete.get((team, bx["inn"])), others_best=max(mates) if mates else 0,
                              entry_score=es, entry_wkts=ew, stage=stage,
                              others_runs=tot_runs - bx["runs"], others_balls=tot_balls - (bx["balls"] or 0))
            subj, facts = A.batting(ctx, perf)
            r = bx["runs"]
            lvl = 200 if r >= 200 else 150 if r >= 150 else 100 if r >= 100 else 50 if r >= 50 else f"low{bx['out']}"
            emit(f"nug:{ev['id']}:{pid}:bat:{bx['inn']}:{lvl}", subj, facts, "batting", pid, ci)
        for bw in p.get("bowlx", []):
            mw += bw["wkts"]
            if bw["wkts"] < 3 and not (sw and bw["conceded"] >= A.EXPENSIVE[f]):
                continue
            overs = bw.get("wicket_overs", [])
            pp, death = (10, 41) if f == "ODI" else (6, 16)
            perf = A.Perf(**base(pid, name, team), inn=bw["inn"], wkts=bw["wkts"], conceded=bw["conceded"], bballs=bw["balls"],
                          pp_wkts=sum(o <= pp for o in overs) if f != "Test" else None,
                          death_wkts=sum(o >= death for o in overs) if f != "Test" else None, star=sw)
            perf.extra["mates_bowl"] = mates_of(team, ci, "bowl")
            perf.extra.update(stage=stage, wicket_balls=[b for b in bw.get("wicket_balls", []) if b is not None])
            perf.extra["dismissed"] = [dict(pid=ci2cs.get(bci), name=players[bci]["name"])
                                       for bci, bp in players.items() for x in bp.get("batx", [])
                                       if x["bowler"] == ci and x["inn"] == bw["inn"] and ci2cs.get(bci)
                                       and star(ctx, ci2cs.get(bci), f, gender, comp, "bat")]
            subj, facts = A.bowling(ctx, perf)
            emit(f"nug:{ev['id']}:{pid}:bowl:{bw['inn']}:{bw['wkts']}:{bw['conceded'] // 20}", subj, facts, "bowling", pid, ci)
        if mr and mw:
            perf = A.Perf(**base(pid, name, team), match_runs=mr, match_wkts=mw, star=sb or sw)
            subj, facts = A.allround(ctx, perf)
            if subj:
                emit(f"nug:{ev['id']}:{pid}:ar:{mr // 25}:{mw}", subj, facts, "all-round", pid, ci)
    # team angles at the end of the match
    if post and f != "T20":
        from . import nuggets as N
        loser = next((t for t in teams if t != winner), None)
        if loser:
            facts = [(s, "team", t) for s, _, t in N.team_facts(winner, loser, f, gender, host, ev["id"], ctx.ht)]
            emit(f"nug:{ev['id']}:team", "", facts, f"{winner} beat {loser}", None, None)
    if post:
        store_recent(ev, d, f, gender, comp, teams, ci2cs, host, winner)
        from . import scheduled
        scheduled.mark_series(ev, teams, fmt, gender)
    if quiet:   # weaker options go into the next morning pack instead of pinging now
        from . import scheduled
        scheduled.queue_digest([dict(key=k, match=ev["name"], text=t) for k, t in quiet])
        for k, _ in quiet:
            sent[k] = pd.Timestamp.now().isoformat()
    return out


def remember_names(m):
    import json
    f = STATE / "espn_names.json"
    try:
        cur = json.loads(f.read_text())
    except (FileNotFoundError, ValueError):
        cur = {}
    if any(cur.get(k) != v for k, v in m.items()):
        cur.update(m)
        f.write_text(json.dumps(cur, indent=0, sort_keys=True))


def _stage(ev):
    import re
    text = f"{ev.get('description') or ''} {ev.get('name') or ''}"
    m = re.search(r"(semi[- ]?final|quarter[- ]?final|final|qualifier\s*\d?|eliminator)", text, re.I)
    return m.group(1).title().replace("-", " ") if m else None


def store_recent(ev, d, f, gender, comp, teams, ci2cs, host, winner):
    date = ev["date"][:10]
    common = dict(match_id=f"espn{ev['id']}", date=date, gender=gender, format=f, competition=comp,
                  series=ev.get("description"), city=ev.get("city"), venue=ev.get("location"), host=host)
    bat, bowl = [], []
    for ci, p in d["players"].items():
        pid = ci2cs.get(ci)
        if not pid:
            continue
        team = _plain(p["team"])
        opp = next((t for t in teams if t != team), None)
        won = (team == winner) if winner else None
        for x in p.get("batx", []):
            bat.append(dict(common, team=team, opp=opp, innings=x["inn"], pos=x["pos"], player_id=pid, player=p["name"],
                            runs=x["runs"], balls=x["balls"], is_out=x["out"], kind=x["kind"],
                            out_bowler=ci2cs.get(x["bowler"]) if x["bowler"] else None, fours=x["fours"], sixes=x["sixes"],
                            won=won, chasing=(x["inn"] == 2) if f != "Test" else (x["inn"] == 4)))
        for x in p.get("bowlx", []):
            overs = x.get("wicket_overs", [])
            pp, death = (10, 41) if f == "ODI" else (6, 16)
            bowl.append(dict(common, team=team, opp=opp, innings=x["inn"], player=p["name"], player_id=pid, wkts=x["wkts"],
                             runs=x["conceded"], balls=x["balls"], dots=x.get("dots"),
                             pp_wkts=sum(o <= pp for o in overs), death_wkts=sum(o >= death for o in overs), won=won))
    tm = [dict(common, team=_plain(t), opp=next((o for o in teams if o != _plain(t)), None), innings=per, score=r, wkts=w,
               target=tg or None, won=(_plain(t) == winner) if winner else None) for t, per, r, w, ov, tg in d.get("innings", [])]
    recent.store(bat, bowl, tm)


def _plain(team):
    import re
    return re.sub(r"\s*Women\b", "", team or "").strip()
