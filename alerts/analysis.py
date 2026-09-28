"""Analyst layer on top of angles.py.

  * rarity      how unusual the performance itself is (share of innings that reach it), used to rank
                performances against each other across the day
  * baselines   peer comparisons so numbers mean something ("58, against a top-order average of 34")
  * new angles  carry (share of team runs), rescue acts, team impact, bursts/hat-tricks, big-match context,
                conversion rate, scoring relative to the rest of the match
  * charts      a suggested visual plus the exact numbers for it, attached to the facts that suit one

Sample-size guards everywhere: no claim rests on fewer than the minimums set below.
"""
import math

import pandas as pd

from . import angles as A
from .angles import F, art2, era, fmt_name, in_, is_league, ordn, plural, poss, when, who

A.BASE.update({"carry": 86, "rescue": 86, "impact": 84, "spell": 88, "bigmatch": 90, "conversion": 80,
               "relative": 80})
KNOCKOUT = ("final", "semi", "qualifier", "eliminator", "quarter")


# ======================================================================= rarity
def rarity(ctx, p, kind):
    """0-16 bonus: -log10(share of innings in scope at least this good) x 4."""
    if kind == "bat":
        if (p.runs or 0) < 50:
            return 0
        h = ctx.scope(ctx.hb, p)
        share = (h.runs >= p.runs).mean() if len(h) else 1
    else:
        if (p.wkts or 0) < 3:
            return 0
        h = ctx.scope(ctx.hw, p)
        share = ((h.wkts > p.wkts) | ((h.wkts == p.wkts) & (h.runs <= p.conceded))).mean() if len(h) else 1
    share = max(share, 1e-5)
    return int(min(16, max(0, -math.log10(share) * 4)))


# ======================================================================= charts
def chart(ctx, fact, idea, data):
    ctx.charts[fact[2]] = (idea, data)
    return fact


def attach_charts(ctx, p, facts, kind):
    """Suggest a visual for facts whose family suits one, with the numbers to build it."""
    h = ctx.scope(ctx.hb if kind == "bat" else ctx.hw, p)
    me = h[h.player_id == p.pid].sort_values(["date", "innings"])
    P = fmt_name(p)
    for f in facts:
        fam = f[1]
        if f[2] in ctx.charts:
            continue
        if fam == "form" and kind == "bat" and len(me) >= 5:
            last = me.tail(9)
            vals = [f"{r.runs}{'' if r.is_out else '*'} v {r.opp}" for r in last.itertuples()] + [f"{p.runs}{'' if p.out else '*'} v {p.opp}"]
            avg = me.runs.sum() / max(1, me.is_out.sum())
            chart(ctx, f, f"Bar chart: his last {len(vals)} {P} innings, with a line at his career average ({avg:.1f})",
                  "; ".join(vals))
        elif fam == "form" and kind == "bowl" and len(me) >= 5:
            last = me.tail(7)
            vals = [f"{r.wkts}/{r.runs} v {r.opp}" for r in last.itertuples()] + [f"{p.wkts}/{p.conceded} v {p.opp}"]
            chart(ctx, f, f"Bar chart: wickets in his last {len(vals)} {P} innings", "; ".join(vals))
        elif fam == "leaderboard":
            col, add = ("runs", p.runs) if kind == "bat" else ("wkts", p.wkts)
            yr = h[h.date.dt.year == p.date.year]
            tot = yr.groupby("player_id")[col].sum()
            tot.loc[p.pid] = tot.get(p.pid, 0) + add
            top = tot.sort_values(ascending=False).head(8)
            chart(ctx, f, f"Horizontal bar: {p.date.year} {P} {'run' if kind == 'bat' else 'wicket'} leaders",
                  "; ".join(f"{ctx.nm(i, '')} {v}" for i, v in top.items()))
        elif fam == "shift" and len(me) >= 20:
            me2 = me.assign(q=me.date.dt.to_period("Q"))
            if kind == "bat":
                g = me2.groupby("q").agg(r=("runs", "sum"), o=("is_out", "sum"), b=("balls", "sum")).tail(10)
                stat = "strike rate" if "strikes" in f[2] else "average"
                vals = [f"{q} {100 * r.r / max(1, r.b):.0f}" if stat == "strike rate" else f"{q} {r.r / max(1, r.o):.1f}"
                        for q, r in g.iterrows()]
            else:
                g = me2.groupby("q").agg(w=("wkts", "sum"), r=("runs", "sum")).tail(10)
                stat = "bowling average"
                vals = [f"{q} {r.r / r.w:.1f}" if r.w else f"{q} -" for q, r in g.iterrows()]
            chart(ctx, f, f"Line chart: his {P} {stat} by quarter (the shift is visible)", "; ".join(vals))
        elif fam == "duel":
            chart(ctx, f, "Head-to-head card: runs, balls, dismissals, average and strike rate for the matchup",
                  f[2].split("(")[-1].rstrip(")"))
        elif fam == "carry" and p.extra.get("team_total"):
            chart(ctx, f, "Stacked bar: his runs vs the rest of the team in this innings",
                  f"{p.name} {p.runs}; rest of team {p.extra['team_total'] - p.runs}")


# ======================================================================= carry (share of team runs)
def _team_innings(ctx, p):
    t = ctx.scope(ctx.ht, p)
    lim = {"T20I": 120, "T20": 120, "ODI": 300}.get(p.fmt)
    done = (t.wkts >= 10) | (t.balls >= lim) if lim else (t.wkts >= 10)
    return t[done][["match_id", "innings", "team", "score"]]


def carry(ctx, p):
    tot, best_other = p.extra.get("team_total"), p.extra.get("others_best")
    if not tot or not p.runs or best_other is None:
        return []
    need_tot = {"T20I": 100, "T20": 100, "ODI": 150, "Test": 150}[p.fmt]
    share = p.runs / tot
    if tot < need_tot or share < (0.40 if p.fmt == "Test" else 0.45):
        return []
    team_word = "the side" if is_league(p) else p.team
    out = [F("carry", f"{p.runs} of {poss(team_word)} {tot} ({share:.0%}) — no one else made more than {best_other}",
             strength=int((share - 0.4) * 60))]
    # how rare is a share like that for this team?
    h = ctx.scope(ctx.hb, p)
    ti = _team_innings(ctx, p)
    j = h.merge(ti, on=["match_id", "innings", "team"])
    j = j[j.team == p.team]
    j = j.assign(share=j.runs / j.score.clip(lower=1))
    hi = j[j.share >= share]
    if len(j) >= 50:
        if not len(hi):
            out.append(F("carry", f"the biggest share of a completed {fmt_name(p)} innings by {A.art_of(who(p))} "
                                  f"{'ever' if era(p, ctx) == 'ever' else f'({era(p, ctx)})'}", strength=12))
        else:
            last = hi.sort_values("date").iloc[-1]
            if (p.date - last.date).days >= 3 * 365:
                out.append(F("carry", f"the biggest share of a completed {fmt_name(p)} innings by {A.art_of(who(p))} since "
                                      f"{ctx.nm(last.player_id, last.player)} ({last.runs} of {last.score}) in {when(last.date)}", strength=8))
    return out


# ======================================================================= rescue acts
def rescue(ctx, p):
    w0, s0 = p.extra.get("entry_wkts"), p.extra.get("entry_score")
    if w0 is None or s0 is None:
        return []
    need_w = 5 if p.fmt == "Test" else 4
    need_r = 80 if p.fmt == "Test" else 50
    max_s = {"T20I": 70, "T20": 70, "ODI": 120, "Test": 160}[p.fmt]   # a real crisis, not late-order slogging
    if w0 < need_w or p.runs < need_r or s0 > max_s:
        return []
    h = ctx.scope(ctx.hb, p)
    h = h[(h.entry_wkts >= w0) & (h.entry_score <= max_s)]
    better = h[h.runs >= p.runs]
    out = [F("rescue", f"came in at {s0}/{w0} and made {p.runs}{'' if p.out else '*'}", strength=2 * (w0 - need_w) + (6 if p.runs >= 100 else 0))]
    tail = f"arriving at {w0} down or worse with the score under {max_s}"
    if len(h) >= 100:
        if not len(better):
            out.append(F("rescue", f"the highest {fmt_name(p)} score by anyone {tail} "
                                   f"{'ever' if era(p, ctx) == 'ever' else f'({era(p, ctx)})'}", strength=14))
        else:
            last = better.sort_values("date").iloc[-1]
            if (p.date - last.date).days >= 2 * 365:
                out.append(F("rescue", f"the highest {fmt_name(p)} score by anyone {tail} since {ctx.nm(last.player_id, last.player)}'s "
                                       f"{last.runs} in {when(last.date)}", strength=8))
    if not is_league(p):
        nat = h[(h.team == p.team) & (h.runs >= p.runs)]
        if len(h[h.team == p.team]) >= 30 and not len(nat) and len(better):
            out.append(F("rescue", f"the highest {fmt_name(p)} score by {A.art_of(who(p))} {tail} "
                                   f"({era(p, ctx)})".replace("(ever)", "ever"), strength=6))
    return out


# ======================================================================= team impact
def impact(ctx, p, kind):
    if kind == "bat":
        lvl = 50 if p.fmt in ("T20I", "T20") else 100
        if p.runs < lvl:
            return []
        h = ctx.scope(ctx.hb, p)
        mine = h[(h.player_id == p.pid) & (h.runs >= lvl)]
        noun = "fifty" if lvl == 50 else "hundred"
        verb = f"scored {'a' if lvl == 50 else 'a'} {noun}"
    else:
        lvl = 5 if p.fmt == "Test" else 4
        if p.wkts < lvl:
            return []
        h = ctx.scope(ctx.hw, p)
        mine = h[(h.player_id == p.pid) & (h.wkts >= lvl)]
        verb = f"taken {'five' if lvl == 5 else 'four'} or more wickets"
    games = mine.drop_duplicates("match_id")
    games = games[games.won.notna()]
    w = int((games.won == True).sum()) + (1 if p.won is True else 0)  # noqa: E712
    n = len(games) + (1 if p.won is not None else 0)
    if n < 6:
        return []
    team = "they" if is_league(p) else p.team
    team_cap = p.team
    P = plural(p)
    if w == n:
        return [F("impact", f"{team_cap} have won all {n} {P} in which he's {verb}", strength=12)]
    if w / n >= 0.85:
        return [F("impact", f"{team_cap} have won {w} of the {n} {P} in which he's {verb}", strength=6)]
    if w / n <= 0.3 and kind == "bat":
        return [F("impact", f"{team_cap} have lost {n - w} of the {n} {P} in which he's {verb}", strength=4)]
    del team
    return []


# ======================================================================= bursts / hat-tricks (live ball data)
def spell(ctx, p):
    balls = sorted(p.extra.get("wicket_balls") or [])
    if len(balls) < 3:
        return []

    def idx(b):  # 10.3 -> legal ball index within the innings (approx: 6 balls an over)
        o = int(b)
        return o * 6 + round((b - o) * 10)
    ix = [idx(b) for b in balls]
    out = []
    # hat-trick: consecutive deliveries by this bowler (same over, or last ball of an over then first of his next)
    for i in range(len(ix) - 2):
        a, b, c = balls[i:i + 3]

        def nxt(x, y):
            return (int(x) == int(y) and round((y - x) * 10) == 1) or (round((x - int(x)) * 10) == 6 and round((y - int(y)) * 10) == 1 and int(y) > int(x))
        if nxt(a, b) and nxt(b, c):
            out.append(F("spell", "a hat-trick", strength=20))
            break
    # three or more wickets in a single over / in a short burst
    overs = pd.Series([int(b) for b in balls]).value_counts()
    if overs.max() >= 3 and not out:
        out.append(F("spell", f"{overs.max()} wickets in one over (the {ordn(int(overs.idxmax()) + 1)})", strength=10))
    best = None
    for i in range(len(ix)):
        for j in range(i + 2, len(ix)):
            k = j - i + 1
            span_ = ix[j] - ix[i] + 1
            if span_ <= 12 and (best is None or k > best[0] or (k == best[0] and span_ < best[1])):
                best = (k, span_)
    if best and not out:
        out.append(F("spell", f"{best[0]} wickets in the space of {best[1]} balls", strength=4 + 2 * best[0]))
    return out


# ======================================================================= big-match context
def bigmatch(ctx, p, kind):
    stage = (p.extra.get("stage") or "").lower()
    if not any(k in stage for k in KNOCKOUT):
        return []
    final = "final" in stage and "semi" not in stage and "quarter" not in stage
    df = ctx.scope(ctx.hb if kind == "bat" else ctx.hw, p)
    df = df[df.stage.fillna("").str.lower().str.contains("final" if final else "|".join(KNOCKOUT))]
    if final:
        df = df[~df.stage.str.lower().str.contains("semi|quarter")]
    where = f"{'a' if not final else 'a'} {fmt_name(p)} {'final' if final else 'knockout'}"
    ev = era(p, ctx)
    tail = " ever" if ev == "ever" else f" ({ev})"
    out = []
    if kind == "bat" and p.runs >= 50:
        b = int((df.runs > p.runs).sum())
        if len(df) >= 20 and b < 3:
            out.append(F("bigmatch", f"the {'' if b == 0 else ordn(b + 1) + '-'}highest score in {where}{tail}", strength=14 if b == 0 else 6))
        mine = df[(df.player_id == p.pid) & (df.runs >= 50)]
        if len(mine) >= 2:
            others = df[df.runs >= 50].groupby("player_id").size().drop(p.pid, errors="ignore")
            n = len(mine) + 1
            if n > (others.max() if len(others) else 0):
                out.append(F("bigmatch", f"his {ordn(n)} 50+ score in {fmt_name(p)} {'finals' if final else 'knockouts'} — the most by anyone{tail}", strength=8))
    if kind == "bowl" and p.wkts >= 3:
        better = int(((df.wkts > p.wkts) | ((df.wkts == p.wkts) & (df.runs < p.conceded))).sum())
        if len(df) >= 20 and better < 3:
            out.append(F("bigmatch", f"the {'' if better == 0 else ordn(better + 1) + '-'}best figures in {where}{tail}", strength=14 if better == 0 else 6))
    return out


# ======================================================================= conversion rate
def conversion(ctx, p):
    h = ctx.scope(ctx.hb, p)
    me = h[h.player_id == p.pid]
    P = fmt_name(p)
    if p.fmt in ("T20I", "T20"):
        return []
    out = []
    fifty_plus = int((me.runs >= 50).sum()) + (1 if p.runs >= 50 else 0)
    tons = int((me.runs >= 100).sum()) + (1 if p.runs >= 100 else 0)
    if p.runs >= 100 and fifty_plus >= 15:
        active = h[h.date >= p.date - pd.Timedelta(days=540)].player_id.unique()
        g = h[h.player_id.isin(active)].groupby("player_id").runs.agg(fp=lambda r: (r >= 50).sum(), t=lambda r: (r >= 100).sum())
        g.loc[p.pid] = [fifty_plus, tons]
        g = g[g.fp >= 15]
        g["rate"] = g.t / g.fp
        rank = int((g.rate > g.at[p.pid, "rate"]).sum()) + 1
        if rank <= 2 and len(g) >= 10:
            out.append(F("conversion", f"he's turned {tons} of his {fifty_plus} {P} 50+ scores into hundreds ({tons / fifty_plus:.0%}) — "
                                       f"{'the best' if rank == 1 else 'the second-best'} conversion rate among active batters (min 15)",
                         strength=8 if rank == 1 else 2))
            ctx.charts[out[-1][2]] = ("Bar chart: conversion rate (hundreds / 50+ scores) of active batters",
                                      "; ".join(f"{ctx.nm(i, '')} {r.rate:.0%} ({int(r.t)}/{int(r.fp)})"
                                                for i, r in g.sort_values("rate", ascending=False).head(8).iterrows()))
    if p.out and 50 <= p.runs < 100 and p.star:
        me_s = me.sort_values("date")
        last_ton = me_s[me_s.runs >= 100].date.max()
        if pd.notna(last_ton):
            since = me_s[(me_s.date > last_ton) & (me_s.runs >= 50) & (me_s.runs < 100)]
            n = len(since) + 1
            if n >= 5:
                out.append(F("conversion", f"his {ordn(n)} score of 50-99 since his last {P} hundred in {when(last_ton)}", strength=n))
    return out


# ======================================================================= relative scoring (limited overs)
def relative(ctx, p):
    ob, orr = p.extra.get("others_balls"), p.extra.get("others_runs")
    if p.fmt == "Test" or not ob or ob < 120 or not p.balls or p.runs < 40:
        return []
    sr, osr = 100 * p.runs / p.balls, 100 * orr / ob
    gap = sr - osr
    if gap >= (55 if p.fmt != "ODI" else 35):
        return [F("relative", f"struck at {sr:.0f} in a match where everyone else scored at {osr:.0f}", strength=int(gap / 10))]
    return []


# ======================================================================= baselines
def peer_baseline(ctx, p, fact):
    """Append 'the top-order average in that time is X' to a shift fact."""
    if "since" not in fact[2] or p.fmt in ("T20", ):
        return fact
    h = ctx.scope(ctx.hb, p)
    cut = p.date - pd.Timedelta(days=365)
    peers = h[(h.date >= cut) & (h.pos.fillna(9) <= 4)] if "pos" in h else h.iloc[0:0]
    if len(peers) < 100:
        return fact
    if "strikes at" in fact[2]:
        v = 100 * peers.runs.sum() / max(1, peers.balls.sum())
        add = f" (top-order strike rate in that time: {v:.0f})"
    else:
        v = peers.runs.sum() / max(1, peers.is_out.sum())
        add = f" (top-order average in that time: {v:.1f})"
    return (fact[0], fact[1], fact[2] + add)


# ======================================================================= entry points
def extra_bat(ctx, p):
    facts = []
    if p.runs >= 50:
        facts += carry(ctx, p) + rescue(ctx, p) + impact(ctx, p, "bat") + bigmatch(ctx, p, "bat") + relative(ctx, p)
    facts += conversion(ctx, p)
    return facts


def extra_bowl(ctx, p):
    facts = []
    if p.wkts >= 3:
        facts += spell(ctx, p) + impact(ctx, p, "bowl") + bigmatch(ctx, p, "bowl")
    return facts


def finish(ctx, p, facts, kind):
    """Rarity bonus, peer baselines, chart ideas."""
    bonus = rarity(ctx, p, kind)
    out = []
    for f in facts:
        if f[1] == "shift" and kind == "bat":
            f = peer_baseline(ctx, p, f)
        out.append((f[0] + (bonus if f[1] not in ("slump", "quirk") else 0), f[1], f[2]))
    attach_charts(ctx, p, out, kind)
    return out


__all__ = ["extra_bat", "extra_bowl", "finish", "art2", "in_"]
