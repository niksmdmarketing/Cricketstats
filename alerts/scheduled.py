"""Scheduled posts that don't depend on a live performance, so the feed has something every day:

  previews      head-to-head records, droughts and player-vs-opponent numbers for today's matches
  on this day   notable performances on this date in past years (round anniversaries first)
  series wrap   leaders and standout numbers once a series finishes
  leaderboards  calendar-year leaders (Mondays)
  comparison    two in-form players side by side, as a debate starter (Thursdays)

All of it goes out in one silent morning pack, together with yesterday's weaker live options.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import angles as A
from . import config as C

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
SERIES = STATE / "series_done.json"
INTL = ("Test", "ODI", "T20I")


def _fm(df):
    return df[df.team.isin(C.FULL_MEMBERS) & df.opp.isin(C.FULL_MEMBERS)]


def post(kind, headline, caption, note="", chart=None, key=None):
    if chart:
        note += f"\n📊 <b>Chart idea:</b> {chart[0]}\n<i>Data: {chart[1]}</i>"
    return dict(kind=kind, key=key or f"{kind}:{headline}", headline=headline, caption=caption, note=note.strip(),
                priority=50, silent=True)


# ======================================================================= previews
def previews(ctx, events):
    out = []
    from .watch import classify
    for ev in events:
        if ev["status"] != "pre":
            continue
        fmt, gender, scopes, teams = classify(ev)
        if not scopes or scopes[0] in C.NUGGET_LEAGUES or len(teams) != 2:
            continue
        f = {"T20": "T20I"}.get(fmt, fmt)
        a, b = teams
        lines = []
        t = ctx.ht[(ctx.ht.format == f) & (ctx.ht.gender == gender)].drop_duplicates(["match_id", "team"])
        h2h = t[(t.team == a) & (t.opp == b)].sort_values("date")
        res = h2h[h2h.won.notna()]
        if len(res) >= 3:
            wa = int((res.won == True).sum())  # noqa: E712
            wb = len(res) - wa
            lines.append(f"{a} {wa}–{wb} {b} in {'women' + chr(39) + 's ' if gender == 'female' else ''}{A.PLURAL[f]} since {res.date.min().year}")
            streak, who_ = 0, None
            for w in reversed(res.won.astype(bool).tolist()):
                side = a if w else b
                if who_ in (None, side):
                    who_ = side
                    streak += 1
                else:
                    break
            if streak >= 4:
                other = b if who_ == a else a
                lines.append(f"{who_} have won the last {streak} {A.PLURAL[f]} between the sides")
            if wb == 0 and wa >= 4:
                lines.append(f"{b} have never beaten {a} in {A.PLURAL[f]} ({wa} defeats)")
            elif wa == 0 and wb >= 4:
                lines.append(f"{a} have never beaten {b} in {A.PLURAL[f]} ({wb} defeats)")
        # player vs opponent: biggest gap between record against this side and overall
        hb = ctx.hb[(ctx.hb.format == f) & (ctx.hb.gender == gender)]
        recent = hb[hb.date >= pd.Timestamp.now() - pd.Timedelta(days=400)]
        picks = []
        for team, opp in ((a, b), (b, a)):
            for pid in recent[recent.team == team].player_id.unique():
                me = hb[hb.player_id == pid]
                vs = me[me.opp == opp]
                if len(vs) < 8 or me.runs.sum() < 1000:
                    continue
                avg_all = me.runs.sum() / max(1, me.is_out.sum())
                avg_vs = vs.runs.sum() / max(1, vs.is_out.sum())
                tons = int((vs.runs >= 100).sum())
                picks.append((abs(avg_vs - avg_all) + 3 * tons, pid, team, opp, avg_vs, avg_all, len(vs), tons, vs.runs.sum()))
        for _, pid, team, opp, av, aa, n, tons, runs in sorted(picks, reverse=True)[:2]:
            nm = ctx.nm(pid, "")
            lines.append(f"{nm} averages {av:.1f} against {opp} in {A.PLURAL[f]} ({runs} runs in {n} innings"
                         + (f", {tons} hundreds" if tons >= 2 else "") + f"), {'up' if av > aa else 'down'} from {aa:.1f} overall")
        if len(lines) >= 2:
            tag = "#" + ev["short"].replace(" ", "") if ev.get("short") else "#Cricket"
            cap = f"{a} v {b} today 🏏\n\n" + "\n".join(f"• {x}" for x in lines[:4]) + f"\n\n{tag}"
            out.append(post("preview", f"Preview: {ev['name']}", cap, key=f"preview:{ev['id']}"))
    return out


def _game(r):
    w = "women's " if r.gender == "female" else ""
    art = "an" if (not w and r.format == "ODI") else "a"
    return f"{art} {w}{r.format}"


# ======================================================================= on this day
def on_this_day(ctx, today=None):
    today = pd.Timestamp(today or datetime.now(timezone.utc).date())
    cands = []
    for kind, df in (("bat", ctx.hb), ("bowl", ctx.hw)):
        d = _fm(df[df.format.isin(INTL)])
        d = d[(d.date.dt.month == today.month) & (d.date.dt.day == today.day) & (d.date.dt.year < today.year)]
        for r in d.itertuples():
            if kind == "bat":
                big = {"Test": 200, "ODI": 150, "T20I": 100}[r.format]
                if r.runs < big:
                    continue
                allsc = df[(df.format == r.format) & (df.gender == r.gender)]
                rank = int((allsc.runs > r.runs).sum()) + 1
                score = r.runs / big * 10 + (15 if rank <= 5 else 0)
                what = f"{ctx.full(r.player_id, r.player)} made {r.runs}{'' if r.is_out else '*'} off {r.balls} balls in {_game(r)} against {r.opp}"
                ctxl = f"Still the {A.ordn(rank) + '-' if rank > 1 else ''}highest {'women' + chr(39) + 's ' if r.gender == 'female' else ''}{r.format} score in our records (since {A.COVER[r.gender]})" if rank <= 10 else ""
            else:
                big = {"Test": 7, "ODI": 6, "T20I": 5}[r.format]
                if r.wkts < big:
                    continue
                allsc = df[(df.format == r.format) & (df.gender == r.gender)]
                rank = int(((allsc.wkts > r.wkts) | ((allsc.wkts == r.wkts) & (allsc.runs < r.runs))).sum()) + 1
                score = r.wkts / big * 10 + (15 if rank <= 5 else 0)
                what = f"{ctx.full(r.player_id, r.player)} took {r.wkts}/{r.runs} in {_game(r)} against {r.opp}"
                ctxl = f"Still the {A.ordn(rank) + '-' if rank > 1 else ''}best {'women' + chr(39) + 's ' if r.gender == 'female' else ''}{r.format} figures in our records (since {A.COVER[r.gender]})" if rank <= 10 else ""
            yrs = today.year - r.date.year
            score += 8 if yrs % 5 == 0 else 0
            where = f" in {r.city}" if isinstance(r.city, str) else ""
            cands.append((score, yrs, r, what + where, ctxl))
    out = []
    for score, yrs, r, what, ctxl in sorted(cands, key=lambda x: -x[0])[:2]:
        cap = f"🗓 On this day in {r.date.year}{f' ({yrs} years ago)' if yrs % 5 == 0 else ''}: {what}." + (f"\n\n{ctxl}." if ctxl else "") + "\n\n#OnThisDay #Cricket"
        out.append(post("onthisday", f"On this day: {what}", cap, key=f"otd:{today:%m%d}:{r.match_id}:{r.player_id}"))
    return out


# ======================================================================= series wrap
def mark_series(ev, teams, fmt, gender):
    """Called from the live pass when a finished match's summary mentions the series."""
    s = (ev.get("summary") or "").lower()
    if "series" not in s:
        return
    done = json.loads(SERIES.read_text()) if SERIES.exists() else []
    if any(d["id"] == ev["id"] for d in done):
        return
    done.append(dict(id=ev["id"], summary=ev.get("summary"), teams=teams, fmt=fmt, gender=gender,
                     date=ev["date"][:10], sent=False, short=ev.get("short")))
    SERIES.write_text(json.dumps(done[-50:], indent=0))


def series_wraps(ctx):
    if not SERIES.exists():
        return []
    done = json.loads(SERIES.read_text())
    out = []
    for d in done:
        if d.get("sent"):
            continue
        f = {"T20": "T20I"}.get(d["fmt"], d["fmt"])
        a, b = d["teams"]
        end = pd.Timestamp(d["date"])
        rows = ctx.hb[(ctx.hb.format == f) & (ctx.hb.gender == d["gender"]) & (ctx.hb.date >= end - pd.Timedelta(days=45))
                      & (ctx.hb.date <= end + pd.Timedelta(days=1)) & (((ctx.hb.team == a) & (ctx.hb.opp == b)) | ((ctx.hb.team == b) & (ctx.hb.opp == a)))]
        wk = ctx.hw[(ctx.hw.format == f) & (ctx.hw.gender == d["gender"]) & (ctx.hw.match_id.isin(set(rows.match_id)))]
        if rows.match_id.nunique() < 2:
            d["sent"] = True
            continue
        bat = rows.groupby("player_id").agg(r=("runs", "sum"), o=("is_out", "sum"), b=("balls", "sum")).sort_values("r", ascending=False)
        bowl = wk.groupby("player_id").agg(w=("wkts", "sum"), r=("runs", "sum"), b=("balls", "sum")).sort_values(["w", "r"], ascending=[False, True])
        lines = []
        tb = bat.iloc[0]
        lines.append(f"Most runs: {ctx.nm(bat.index[0], '')} {int(tb.r)} at {tb.r / max(1, tb.o):.1f}, SR {100 * tb.r / max(1, tb.b):.0f}")
        if len(bowl):
            tw = bowl.iloc[0]
            lines.append(f"Most wickets: {ctx.nm(bowl.index[0], '')} {int(tw.w)} at {tw.r / max(1, tw.w):.1f}")
        best = rows.sort_values("runs", ascending=False).iloc[0]
        lines.append(f"Top score: {ctx.nm(best.player_id, best.player)} {best.runs}{'' if best.is_out else '*'} v {best.opp}")
        if f != "Test":
            q = bat[bat.b >= 60]
            if len(q):
                q = q.assign(sr=100 * q.r / q.b).sort_values("sr", ascending=False)
                lines.append(f"Fastest scorer (min 60 balls): {ctx.nm(q.index[0], '')} SR {q.sr.iloc[0]:.0f}")
        tag = "#" + (d.get("short") or "Cricket").split(" ")[0].replace(" ", "")
        cap = f"{d['summary']}\n\nThe series in numbers:\n" + "\n".join(f"• {x}" for x in lines) + "\n\n#Cricket"
        data = "; ".join(f"{ctx.nm(i, '')} {int(r.r)}" for i, r in bat.head(8).iterrows())
        out.append(post("wrap", f"Series wrap: {a} v {b}", cap, chart=("Horizontal bar: series run-scorers", data),
                        key=f"wrap:{d['id']}"))
        d["sent"] = True
    SERIES.write_text(json.dumps(done, indent=0))
    return out


# ======================================================================= leaderboards (Mondays)
def leaderboards(ctx, today=None):
    today = pd.Timestamp(today or datetime.now(timezone.utc).date())
    yr = today.year
    out = []
    for f in INTL:
        for kind, df, col in (("runs", ctx.hb, "runs"), ("wickets", ctx.hw, "wkts")):
            d = _fm(df[(df.format == f) & (df.gender == "male") & (df.date.dt.year == yr)])
            if not len(d):
                continue
            tot = d.groupby("player_id").agg(v=(col, "sum"), team=("team", "last")).sort_values("v", ascending=False).head(5)
            if len(tot) < 5 or tot.v.iloc[0] < (300 if kind == "runs" else 15):
                continue
            lines = [f"{i + 1}. {ctx.nm(pid, '')} ({r.team}) {int(r.v)}" for i, (pid, r) in enumerate(tot.iterrows())]
            top_team = tot.team.value_counts()
            extra = f"\n\n{top_team.max()} of the top 5 are from {top_team.idxmax()}." if top_team.max() >= 3 else ""
            cap = f"Most men's {f} {kind} in {yr} so far:\n\n" + "\n".join(lines) + extra + "\n\n#Cricket"
            out.append(post("leaders", f"{yr} {f} {kind} leaders", cap,
                            chart=(f"Horizontal bar: {yr} men's {f} {kind} leaders", "; ".join(x.split(". ", 1)[1] for x in lines)),
                            key=f"leaders:{today:%Y%W}:{f}:{kind}"))
    return out


# ======================================================================= comparison (Thursdays)
def comparison(ctx, today=None):
    today = pd.Timestamp(today or datetime.now(timezone.utc).date())
    since = pd.Timestamp(f"{today.year - 1}-01-01")
    out = []
    for f in ("Test", "ODI", "T20I"):
        d = _fm(ctx.hb[(ctx.hb.format == f) & (ctx.hb.gender == "male") & (ctx.hb.date >= since) & (ctx.hb.pos.fillna(9) <= 4)])
        g = d.groupby("player_id").agg(r=("runs", "sum"), o=("is_out", "sum"), b=("balls", "sum"), n=("runs", "size"),
                                       t=("runs", lambda x: (x >= 100).sum()), team=("team", "last"), last=("date", "max"))
        g = g[(g.n >= (12 if f == "Test" else 10)) & (g.last >= today - pd.Timedelta(days=60))].sort_values("r", ascending=False)
        # best pair from the same country, else the top two overall
        pair = None
        for team, grp in g.groupby("team"):
            if len(grp) >= 2 and (pair is None or grp.r.iloc[:2].sum() > pair.r.sum()):
                pair = grp.head(2)
        if pair is None and len(g) >= 2:
            pair = g.head(2)
        if pair is None:
            continue
        rows = []
        for pid, r in pair.iterrows():
            rows.append((ctx.nm(pid, ""), int(r.r), r.r / max(1, r.o), 100 * r.r / max(1, r.b), int(r.t), int(r.n)))
        (n1, r1, a1, s1, t1, i1), (n2, r2, a2, s2, t2, i2) = rows
        cap = (f"{n1} vs {n2} in {f}s since the start of {since.year}:\n\n"
               f"Runs: {r1} | {r2}\nAverage: {a1:.1f} | {a2:.1f}\n"
               + (f"Strike rate: {s1:.0f} | {s2:.0f}\n" if f != "Test" else "")
               + f"Hundreds: {t1} | {t2}\nInnings: {i1} | {i2}\n\nWho's been better? 👇\n\n#Cricket")
        out.append(post("compare", f"{n1} vs {n2} ({f})", cap,
                        chart=("Side-by-side bars: runs, average, strike rate, hundreds",
                               f"{n1}: {r1} runs, avg {a1:.1f}, SR {s1:.0f}, {t1} hundreds; {n2}: {r2} runs, avg {a2:.1f}, SR {s2:.0f}, {t2} hundreds"),
                        key=f"compare:{today:%Y%W}:{f}"))
    return out[:2]


# ======================================================================= morning pack
DIGEST = STATE / "digest.json"


def queue_digest(items):
    """Weaker live options, saved for the next morning pack."""
    cur = json.loads(DIGEST.read_text()) if DIGEST.exists() else []
    cur += items
    DIGEST.write_text(json.dumps(cur[-60:], indent=0))


def morning_pack(ctx, events, today=None):
    today = pd.Timestamp(today or (datetime.now(timezone.utc) + timedelta(hours=10)).date())
    posts = []
    posts += series_wraps(ctx)
    posts += previews(ctx, events)
    posts += on_this_day(ctx, today)
    if today.weekday() == 0:
        posts += leaderboards(ctx, today)
    if today.weekday() == 3:
        posts += comparison(ctx, today)
    digest = json.loads(DIGEST.read_text()) if DIGEST.exists() else []
    if DIGEST.exists():
        DIGEST.unlink()
    return posts, digest
