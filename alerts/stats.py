"""Record, milestone and rare-event detection over the local Cricsheet copy."""
from pathlib import Path

import duckdb
import pandas as pd

from . import config as C
from .labels import fmt_of, hashtag, short

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def connect():
    con = duckdb.connect()
    con.execute(f"CREATE VIEW m AS SELECT * FROM read_parquet('{(DATA/'matches.parquet').as_posix()}')")
    con.execute(f"CREATE VIEW d AS SELECT * FROM read_parquet('{(DATA/'deliveries'/'*.parquet').as_posix()}', union_by_name=true) WHERE NOT super_over")
    con.execute("""
    CREATE TABLE bat AS
    SELECT d.match_id, d.innings, d.batting_team team, d.bowling_team opp, d.batter player, any_value(d.batter_id) player_id,
           SUM(runs_batter) runs, SUM((wides=0)::INT) balls, SUM(is_four::INT) fours, SUM(is_six::INT) sixes,
           MAX(COALESCE(player_out = batter, false)::INT) is_out
    FROM d GROUP BY ALL""")
    con.execute("""
    CREATE TABLE bowl AS
    SELECT d.match_id, d.innings, d.bowling_team team, d.batting_team opp, d.bowler player, any_value(d.bowler_id) player_id,
           SUM(is_legal::INT) balls, SUM(runs_batter+wides+noballs) runs, SUM(bowler_wicket::INT) wkts
    FROM d GROUP BY ALL""")
    con.execute("""
    CREATE TABLE inns AS
    SELECT match_id, innings, batting_team team, bowling_team opp, MAX(team_score) score, MAX(team_wickets) wkts,
           MAX(legal_balls_bowled) balls, max(target_runs) AS target
    FROM d GROUP BY ALL""")
    # balls taken to reach 50 / 100 (limited overs)
    con.execute("""
    CREATE TABLE fast AS
    WITH b AS (SELECT match_id, innings, batter player, batter_id, batter_runs_so_far r, batter_balls_so_far bb FROM d)
    SELECT match_id, innings, player, any_value(batter_id) player_id,
           MIN(CASE WHEN r >= 50 THEN bb END) b50, MIN(CASE WHEN r >= 100 THEN bb END) b100
    FROM b GROUP BY ALL""")
    return con


def _eligible(mrow):
    comp = mrow["competition"]
    if mrow["team_type"] == "international":
        fm = {mrow["team1"], mrow["team2"]} & C.FULL_MEMBERS
        return len(fm) == 2 if C.BOTH_FULL_MEMBERS else bool(fm)
    return (not C.INTERNATIONALS_ONLY) and comp in C.NUGGET_LEAGUES


def _ord(n):
    return "" if n == 1 else {2: "2nd-", 3: "3rd-"}.get(n, f"{n}th-")


def _since(con, comp, gender):
    y = con.execute("SELECT MIN(year) FROM m WHERE competition=? AND gender=?", [comp, gender]).fetchone()[0]
    return y


def detect(con, new_ids):
    """Return a list of alert dicts for the given newly added match ids."""
    if not new_ids:
        return []
    mm = con.execute("SELECT * FROM m WHERE match_id IN (SELECT UNNEST(?))", [list(new_ids)]).df()
    counts = con.execute("SELECT competition, gender, COUNT(*) n FROM m GROUP BY ALL").df()
    counts = {(r.competition, r.gender): r.n for r in counts.itertuples()}
    alerts = []
    for _, mr in mm.iterrows():
        if not _eligible(mr):
            continue
        comp, g, mid = mr["competition"], mr["gender"], mr["match_id"]
        fmt = fmt_of(mr["match_type"])
        deep = counts.get((comp, g), 0) >= C.MIN_MATCHES_FOR_RECORDS
        since = _since(con, comp, g)
        where = f"m.competition = '{comp.replace(chr(39), chr(39)*2)}' AND m.gender = '{g}'"
        ctx = dict(match_id=mid, competition=comp, gender=g, format=fmt, date=str(mr["start_date"])[:10],
                   match=f"{mr['team1']} v {mr['team2']}", venue=mr["venue"], tag=hashtag(comp))
        scope = f"{short(comp)}" + ("" if since is None else f" (since {since})")

        # 1. individual scores
        for r in con.execute(f"SELECT * REPLACE (runs::INT AS runs, balls::INT AS balls) FROM bat WHERE match_id=? AND runs >= ?", [mid, 50 if fmt == 'T20' else 100]).df().itertuples():
            if deep:
                rank = con.execute(f"""SELECT 1 + COUNT(*) FROM bat JOIN m USING(match_id)
                    WHERE {where} AND bat.runs > ?""", [int(r.runs)]).fetchone()[0]
                if rank <= C.TOP_N:
                    tie = con.execute(f"""SELECT COUNT(*) FROM bat JOIN m USING(match_id)
                        WHERE {where} AND bat.runs = ? AND NOT (bat.match_id = ? AND bat.player = ?)""",
                        [int(r.runs), mid, r.player]).fetchone()[0]
                    no = "*" if not r.is_out else ""
                    alerts.append(dict(ctx, kind="record", player=r.player, player_id=r.player_id, key=f"hs:{mid}:{r.player}",
                        headline=f"{r.player} {r.runs}{no} ({r.balls}b): {'joint-' if tie else ''}{_ord(rank)}highest score in {scope}",
                        caption=f"{r.runs}{no} off {r.balls} balls 🔥\n\n{r.player} just posted the {'joint-' if tie else ''}{_ord(rank)}highest individual score in {short(comp)} history ({r.team} v {r.opp}).\n\n{ctx['tag']}",
                        priority=90 - rank))
            # rare: T20 hundred, ODI double
            if fmt == "T20" and r.runs >= 100 and mr["team_type"] == "international":
                alerts.append(dict(ctx, kind="rare", player=r.player, player_id=r.player_id, key=f"t20ton:{mid}:{r.player}",
                    headline=f"T20I hundred: {r.player} {r.runs} ({r.balls}b) for {r.team} v {r.opp}",
                    caption=f"💯 {r.player} smashes {r.runs} off {r.balls} balls for {r.team} against {r.opp}.\n\n{ctx['tag']}",
                    priority=70))
            if fmt == "ODI" and r.runs >= 200:
                alerts.append(dict(ctx, kind="rare", player=r.player, player_id=r.player_id, key=f"odi200:{mid}:{r.player}",
                    headline=f"ODI DOUBLE HUNDRED: {r.player} {r.runs} ({r.balls}b)",
                    caption=f"2️⃣0️⃣0️⃣ {r.player} hits an ODI double century: {r.runs} off {r.balls} for {r.team} v {r.opp}.\n\n{ctx['tag']}",
                    priority=95))

        # 2. fastest 50 / 100
        if fmt != "Test" and deep:
            for r in con.execute("SELECT * FROM fast WHERE match_id=? AND (b50 IS NOT NULL)", [mid]).df().itertuples():
                for lab, col in (("hundred", "b100"), ("fifty", "b50")):
                    v = getattr(r, col)
                    if pd.isna(v):
                        continue
                    rank = con.execute(f"""SELECT 1 + COUNT(*) FROM fast JOIN m USING(match_id)
                        WHERE {where} AND fast.{col} < ?""", [int(v)]).fetchone()[0]
                    if rank <= C.TOP_N:
                        joint = con.execute(f"""SELECT COUNT(*) FROM fast JOIN m USING(match_id)
                            WHERE {where} AND fast.{col} = ?""", [int(v)]).fetchone()[0] > 1
                        j = "joint-" if joint else ""
                        alerts.append(dict(ctx, kind="record", player=r.player, player_id=r.player_id, key=f"fast{lab}:{mid}:{r.player}",
                            headline=f"{r.player}: {int(v)}-ball {lab}, {j}{_ord(rank)}fastest in {scope}",
                            caption=f"⚡ {int(v)} balls.\n\n{r.player} brings up the {j}{_ord(rank)}fastest {lab} in {short(comp)} history.\n\n{ctx['tag']}",
                            priority=88 - rank))
                        break  # don't double-post 50 and 100 for the same innings

        # 3. bowling figures
        for r in con.execute("SELECT * REPLACE (runs::INT AS runs, wkts::INT AS wkts) FROM bowl WHERE match_id=? AND wkts >= ?", [mid, 4 if fmt != 'Test' else 6]).df().itertuples():
            if deep:
                rank = con.execute(f"""SELECT 1 + COUNT(*) FROM bowl JOIN m USING(match_id)
                    WHERE {where} AND (bowl.wkts > ? OR (bowl.wkts = ? AND bowl.runs < ?))""",
                                   [int(r.wkts), int(r.wkts), int(r.runs)]).fetchone()[0]
                if rank <= C.TOP_N:
                    alerts.append(dict(ctx, kind="record", player=r.player, player_id=r.player_id, key=f"bb:{mid}:{r.player}",
                        headline=f"{r.player} {r.wkts}/{r.runs}: {_ord(rank)}best figures in {scope}",
                        caption=f"🎯 {r.wkts}/{r.runs}\n\n{r.player} returns the {_ord(rank)}best bowling figures in {short(comp)} history ({r.team} v {r.opp}).\n\n{ctx['tag']}",
                        priority=89 - rank))
            if fmt == "T20" and r.wkts >= 6:
                alerts.append(dict(ctx, kind="rare", player=r.player, player_id=r.player_id, key=f"6fer:{mid}:{r.player}",
                    headline=f"T20 six-for: {r.player} {r.wkts}/{r.runs}",
                    caption=f"🔥 {r.wkts}/{r.runs}! {r.player} rips through {r.opp}.\n\n{ctx['tag']}", priority=80))

        # 4. team totals and chases
        if deep:
            for r in con.execute("SELECT * REPLACE (score::INT AS score, wkts::INT AS wkts) FROM inns WHERE match_id=?", [mid]).df().itertuples():
                rank = con.execute(f"""SELECT 1 + COUNT(*) FROM inns JOIN m USING(match_id)
                    WHERE {where} AND inns.score > ?""", [int(r.score)]).fetchone()[0]
                if rank <= C.TOP_N:
                    alerts.append(dict(ctx, kind="record", player=None, key=f"tt:{mid}:{r.innings}",
                        headline=f"{r.team} {r.score}/{r.wkts}: {_ord(rank)}highest total in {scope}",
                        caption=f"{r.score}/{r.wkts} 🤯\n\n{r.team} post the {_ord(rank)}highest total in {short(comp)} history, against {r.opp}.\n\n{ctx['tag']}",
                        priority=86 - rank))
                won_chase = (fmt != "Test" and r.innings == 2 and not pd.isna(r.target)
                             and r.score >= r.target and mr["winner"] == r.team)
                if won_chase:
                    rank = con.execute(f"""SELECT 1 + COUNT(*) FROM inns JOIN m USING(match_id)
                        WHERE {where} AND inns.innings = 2 AND inns.score >= inns.target
                          AND m.winner = inns.team AND inns.target > ?""", [int(r.target)]).fetchone()[0]
                    if rank <= C.TOP_N:
                        alerts.append(dict(ctx, kind="record", player=None, key=f"chase:{mid}",
                            headline=f"{r.team} chase {int(r.target)}: {_ord(rank)}highest successful chase in {scope}",
                            caption=f"Target {int(r.target)}? Done ✅\n\n{r.team} complete the {_ord(rank)}highest successful chase in {short(comp)} history against {r.opp}.\n\n{ctx['tag']}",
                            priority=85 - rank))

        # 5. rare events: hat-tricks, six sixes in an over
        seq = con.execute("""SELECT innings, bowler, over, delivery_seq, bowler_wicket, is_legal, wides, noballs,
                                    runs_batter, is_six FROM d WHERE match_id=? ORDER BY innings, delivery_seq""", [mid]).df()
        for (inn, bowler), grp in seq.groupby(["innings", "bowler"], sort=False):
            streak = 0
            for w, wd in zip(grp.bowler_wicket, grp.wides):
                if w:
                    streak += 1
                    if streak == 3:
                        alerts.append(dict(ctx, kind="rare", player=bowler, key=f"hattrick:{mid}:{bowler}:{inn}",
                            headline=f"HAT-TRICK: {bowler} ({ctx['match']})",
                            caption=f"🎩 HAT-TRICK!\n\n{bowler} takes three in three in {ctx['match']}.\n\n{ctx['tag']}",
                            priority=92))
                elif not wd:
                    streak = 0
        ov = seq.groupby(["innings", "over"]).agg(sixes=("is_six", "sum"), legal=("is_legal", "sum"), bowler=("bowler", "first")).reset_index()
        for r in ov[(ov.sixes >= 6)].itertuples():
            alerts.append(dict(ctx, kind="rare", player=None, key=f"6sixes:{mid}:{r.innings}:{r.over}",
                headline=f"SIX SIXES IN AN OVER off {r.bowler} ({ctx['match']})",
                caption=f"6️⃣6️⃣6️⃣6️⃣6️⃣6️⃣\n\nSix sixes in an over off {r.bowler} in {ctx['match']}!\n\n{ctx['tag']}", priority=99))
    return alerts


# ---------------------------------------------------------------- careers
def career_table(con):
    con.execute("""
    CREATE OR REPLACE TABLE scopes AS
    SELECT match_id, competition AS scope, 'international' AS kind, gender, start_date FROM m WHERE team_type='international'
    UNION ALL SELECT match_id, CASE WHEN gender='male' THEN 'T20 (all, men)' ELSE 'T20 (all, women)' END,
                     't20_all', gender, start_date FROM m WHERE match_type IN ('T20','IT20')
    UNION ALL SELECT match_id, competition, 'league', gender, start_date FROM m
              WHERE team_type <> 'international' AND competition IN (SELECT UNNEST(?))
    """, [sorted(C.MAJOR_LEAGUES)])
    con.execute("""
    CREATE OR REPLACE TABLE appear AS
    SELECT match_id, player_id, any_value(player) player, any_value(team) team FROM (
        SELECT match_id, player_id, player, team FROM bat UNION ALL
        SELECT match_id, player_id, player, team FROM bowl) GROUP BY ALL""")
    return con.execute("""
    SELECT s.scope, s.kind, s.gender, a.player_id, any_value(a.player) player,
           arg_max(a.team, s.start_date) team, COUNT(DISTINCT a.match_id) matches,
           MIN(s.start_date)::DATE first_match, MAX(s.start_date)::DATE last_match,
           COALESCE(SUM(bt.runs), 0) runs, COALESCE(SUM(bw.wkts), 0) wkts
    FROM appear a JOIN scopes s USING (match_id)
    LEFT JOIN (SELECT match_id, player_id, SUM(runs) runs FROM bat GROUP BY ALL) bt USING (match_id, player_id)
    LEFT JOIN (SELECT match_id, player_id, SUM(wkts) wkts FROM bowl GROUP BY ALL) bw USING (match_id, player_id)
    WHERE a.player_id IS NOT NULL
    GROUP BY ALL""").df()


def scope_name(scope):
    """'Men's ODI' -> 'ODI', 'T20 (all, men)' -> 'T20', leagues -> short name."""
    if scope.startswith("T20 (all"):
        return "T20"
    for p in ("Men's ", "Women's "):
        if scope.startswith(p) and scope[len(p):] in ("Test", "ODI", "T20I"):
            return scope[len(p):]
    return short(scope)


def milestone_alerts(before, after, new_ids_players):
    """Compare career tables before/after adding new matches; flag crossed milestones."""
    out = []
    key = ["scope", "player_id"]
    j = after.merge(before[key + ["runs", "wkts"]], on=key, how="left", suffixes=("", "_before")).fillna({"runs_before": 0, "wkts_before": 0})
    j = j[j.player_id.isin(new_ids_players)]
    for r in j.itertuples():
        if r.kind == "international" and r.team not in C.FULL_MEMBERS:
            continue
        if r.kind == "league" and r.scope not in C.T20_LEAGUES:
            continue
        if r.kind == "t20_all" or (r.kind != "international" and (C.INTERNATIONALS_ONLY or r.scope not in C.NUGGET_LEAGUES)):
            continue
        rs, ws = C.MILESTONES[r.kind]
        mins = C.MIN_MILESTONE[r.kind]
        for stat, step, now, prev, low in (("runs", rs, r.runs, r.runs_before, mins[0]),
                                           ("wickets", ws, r.wkts, r.wkts_before, mins[1])):
            now, prev = int(now), int(prev)
            if now // step > prev // step and now >= low:
                mark = now // step * step
                start = C.COVERAGE_START[r.gender]
                partial = pd.Timestamp(r.first_match).year <= start and r.kind != "league"
                verb = "reaches" if now == mark else "passes"
                out.append(dict(kind="milestone", player=r.player, player_id=r.player_id, scope=r.scope,
                    competition=r.scope, key=f"ms:{r.scope}:{r.player_id}:{stat}:{mark}", gender=r.gender,
                    headline=f"{r.player} ({r.team}) {verb} {mark:,} {scope_name(r.scope)} {stat}: now {now:,} in {r.matches} matches",
                    caption=f"{mark:,} {scope_name(r.scope)} {stat.upper()} 🙌\n\n{r.player} brings up {mark:,} {scope_name(r.scope)} {stat}.\n\n#Cricket",
                    needs_check=bool(partial), priority={"international": 84, "league": 76, "t20_all": 74}[r.kind]))
    return out


# ---------------------------------------------------------------- history for stat nuggets
def history_tables(con):
    """Innings-level history for internationals: batting, bowling and team innings."""
    base = """
      SELECT match_id, start_date::DATE AS date, gender,
             CASE WHEN match_type IN ('T20','IT20') THEN 'T20I' ELSE match_type END AS format,
             city, venue, winner, win_by_runs, win_by_wickets, team1, team2
      FROM m WHERE team_type = 'international' AND match_type IN ('Test','ODI','T20','IT20')"""
    # host country per city: the full member that plays (almost) every international there
    from . import config as C
    fm = sorted(C.FULL_MEMBERS)
    hosts = con.execute(f"""
      WITH mm AS (SELECT * FROM ({base}) WHERE team1 IN (SELECT UNNEST(?)) AND team2 IN (SELECT UNNEST(?))),
           t AS (SELECT city, team1 AS team, match_id FROM mm UNION ALL SELECT city, team2, match_id FROM mm),
           c AS (SELECT city, team, COUNT(DISTINCT match_id) n FROM t GROUP BY ALL),
           tot AS (SELECT city, COUNT(DISTINCT match_id) n FROM mm GROUP BY city),
           r AS (SELECT city, team, n, ROW_NUMBER() OVER (PARTITION BY city ORDER BY n DESC) rk FROM c)
      SELECT a.city, a.team AS host, a.n * 1.0 / tot.n AS share, COALESCE(b.n, 0) * 1.0 / tot.n AS share2, tot.n
      FROM r a JOIN tot USING (city) LEFT JOIN r b ON b.city = a.city AND b.rk = 2
      WHERE a.rk = 1""", [fm, fm]).df()
    hosts = hosts[(hosts.n >= 3) & (hosts.share >= 0.4) & (hosts.share >= 2 * hosts.share2)][["city", "host"]]
    manual = pd.DataFrame(list(C.CITY_COUNTRY.items()), columns=["city", "host"])
    hosts = pd.concat([manual, hosts[~hosts.city.isin(manual.city)]], ignore_index=True)
    con.register("hosts_df", hosts)
    base = base.replace("FROM m WHERE", "FROM m LEFT JOIN hosts_df USING (city) WHERE").replace(
        "city, venue,", "city, host, venue,")
    bat = con.execute(f"""
      SELECT b.match_id, mm.date, mm.gender, mm.format, mm.city, mm.host, b.team, b.opp, b.innings,
             b.player_id, b.player, b.runs::INT runs, b.balls::INT balls, b.is_out::INT is_out,
             b.fours::INT fours, b.sixes::INT sixes
      FROM bat b JOIN ({base}) mm USING (match_id)""").df()
    bowl = con.execute(f"""
      SELECT b.match_id, mm.date, mm.gender, mm.format, mm.city, mm.host, b.team, b.opp, b.innings,
             b.player_id, b.player, b.wkts::INT wkts, b.runs::INT runs, b.balls::INT balls
      FROM bowl b JOIN ({base}) mm USING (match_id)""").df()
    team = con.execute(f"""
      SELECT i.match_id, mm.date, mm.gender, mm.format, mm.city, mm.host, i.team, i.opp, i.innings,
             i.score::INT score, i.wkts::INT wkts, i.target, (mm.winner = i.team) AS won,
             mm.win_by_runs, mm.win_by_wickets
      FROM inns i JOIN ({base}) mm USING (match_id)""").df()
    return bat, bowl, team
