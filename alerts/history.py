"""Innings-level history used by the angle engine.

Covers internationals involving a full member (men and women) and the major T20 leagues.
Tables (saved to state/ as parquet):
  hist_bat    one row per batter innings: position, dismissal (kind + bowler), result, chase flag
  hist_bowl   one row per bowler innings: wickets, runs, balls, powerplay/death wickets, result
  hist_team   one row per team innings
  hist_duel   batter vs bowler ball-by-ball summary per match (runs, balls, dismissals)
  hist_split  batter yearly runs/balls/outs vs bowler type, and bowler yearly figures per phase
"""
import pandas as pd

from . import config as C
from . import meta as M

INTL_FORMATS = {"Test": "Test", "ODI": "ODI", "T20": "T20I", "IT20": "T20I"}


def _scope_sql():
    fm = ", ".join("'" + t.replace("'", "''") + "'" for t in sorted(C.FULL_MEMBERS))
    lg = ", ".join("'" + t.replace("'", "''") + "'" for t in sorted(C.T20_LEAGUES))
    return f"""
      SELECT match_id, start_date::DATE AS date, gender, competition,
             CASE WHEN team_type = 'international' THEN
                  CASE WHEN match_type IN ('T20','IT20') THEN 'T20I' ELSE match_type END
                  ELSE 'T20' END AS format,
             COALESCE(event, competition) AS series, event_stage AS stage, city, venue, winner, win_by_runs, win_by_wickets,
             team1, team2, team_type
      FROM m
      WHERE (team_type = 'international' AND match_type IN ('Test','ODI','T20','IT20')
             AND (team1 IN ({fm}) OR team2 IN ({fm})))
         OR (competition IN ({lg}))"""


def _hosts(con, base):
    fm = sorted(C.FULL_MEMBERS)
    hosts = con.execute(f"""
      WITH mm AS (SELECT * FROM ({base}) WHERE team_type='international' AND team1 IN (SELECT UNNEST(?)) AND team2 IN (SELECT UNNEST(?))),
           t AS (SELECT city, team1 AS team, match_id FROM mm UNION ALL SELECT city, team2, match_id FROM mm),
           c AS (SELECT city, team, COUNT(DISTINCT match_id) n FROM t GROUP BY ALL),
           tot AS (SELECT city, COUNT(DISTINCT match_id) n FROM mm GROUP BY city),
           r AS (SELECT city, team, n, ROW_NUMBER() OVER (PARTITION BY city ORDER BY n DESC) rk FROM c)
      SELECT a.city, a.team AS host, a.n * 1.0 / tot.n AS share, COALESCE(b.n, 0) * 1.0 / tot.n AS share2, tot.n
      FROM r a JOIN tot USING (city) LEFT JOIN r b ON b.city = a.city AND b.rk = 2
      WHERE a.rk = 1""", [fm, fm]).df()
    hosts = hosts[(hosts.n >= 3) & (hosts.share >= 0.4) & (hosts.share >= 2 * hosts.share2)][["city", "host"]]
    manual = pd.DataFrame(list(C.CITY_COUNTRY.items()), columns=["city", "host"])
    return pd.concat([manual, hosts[~hosts.city.isin(manual.city)]], ignore_index=True)


def build(con):
    base = _scope_sql()
    con.register("hosts_df", _hosts(con, base))
    con.execute(f"CREATE OR REPLACE TEMP TABLE mm AS SELECT s.*, h.host FROM ({base}) s LEFT JOIN hosts_df h USING (city)")
    con.execute("""CREATE OR REPLACE TEMP TABLE dd AS
                   SELECT d.* FROM d JOIN mm USING (match_id)""")
    # last innings of a limited-overs match or 4th innings of a Test = chasing
    con.execute("""CREATE OR REPLACE TEMP TABLE chase AS
                   SELECT match_id, innings, (MAX(target_runs) IS NOT NULL) AS chasing FROM dd GROUP BY ALL""")
    # batting order: first ball the batter appears on (as striker or non-striker)
    bat = con.execute("""
      WITH appear AS (
        SELECT match_id, innings, batter_id pid, MIN(delivery_seq) s FROM dd GROUP BY ALL),
      ns AS (SELECT match_id, innings, non_striker nm, MIN(delivery_seq) s FROM dd GROUP BY ALL),
      nsid AS (SELECT DISTINCT match_id, innings, batter nm, batter_id pid FROM dd),
      first AS (
        SELECT match_id, innings, pid, MIN(s) s FROM (
          SELECT match_id, innings, pid, s FROM appear
          UNION ALL SELECT ns.match_id, ns.innings, nsid.pid, ns.s FROM ns JOIN nsid USING (match_id, innings, nm)) GROUP BY ALL),
      pos AS (SELECT match_id, innings, pid, s, ROW_NUMBER() OVER (PARTITION BY match_id, innings ORDER BY s) pos FROM first),
      entry AS (SELECT pos.match_id, pos.innings, pos.pid,
                       (dd.team_score - dd.runs_total)::INT entry_score,
                       (dd.team_wickets - (dd.is_wicket)::INT)::INT entry_wkts
                FROM pos JOIN dd ON dd.match_id = pos.match_id AND dd.innings = pos.innings AND dd.delivery_seq = pos.s),
      outs AS (SELECT match_id, innings, player_out, ANY_VALUE(wicket_kind) kind,
                      ANY_VALUE(CASE WHEN bowler_wicket THEN bowler_id END) bowler_id
               FROM dd WHERE is_wicket AND player_out IS NOT NULL GROUP BY ALL),
      b AS (SELECT match_id, innings, batting_team team, bowling_team opp, batter player, ANY_VALUE(batter_id) player_id,
                   SUM(runs_batter)::INT runs, SUM((wides=0)::INT)::INT balls, SUM(is_four::INT)::INT fours,
                   SUM(is_six::INT)::INT sixes FROM dd GROUP BY ALL)
      SELECT b.match_id, mm.date, mm.gender, mm.format, mm.competition, mm.series, mm.stage, mm.city, mm.venue, mm.host,
             b.team, b.opp, b.innings, pos.pos::INT pos, en.entry_score, en.entry_wkts, b.player_id, b.player, b.runs, b.balls,
             (o.player_out IS NOT NULL)::INT is_out, o.kind, o.bowler_id AS out_bowler, b.fours, b.sixes,
             CASE WHEN mm.winner IS NULL THEN NULL ELSE mm.winner = b.team END AS won, c.chasing
      FROM b JOIN mm USING (match_id)
      LEFT JOIN pos ON pos.match_id=b.match_id AND pos.innings=b.innings AND pos.pid=b.player_id
      LEFT JOIN entry en ON en.match_id=b.match_id AND en.innings=b.innings AND en.pid=b.player_id
      LEFT JOIN outs o ON o.match_id=b.match_id AND o.innings=b.innings AND o.player_out=b.player
      LEFT JOIN chase c ON c.match_id=b.match_id AND c.innings=b.innings""").df()
    bowl = con.execute("""
      SELECT d.match_id, mm.date, mm.gender, mm.format, mm.competition, mm.series, mm.stage, mm.city, mm.venue, mm.host,
             d.bowling_team team, d.batting_team opp, d.innings, d.bowler player, ANY_VALUE(d.bowler_id) player_id,
             SUM(d.bowler_wicket::INT)::INT wkts, SUM(runs_batter + wides + noballs)::INT runs,
             SUM(is_legal::INT)::INT balls, SUM((is_dot AND is_legal)::INT)::INT dots,
             SUM((bowler_wicket AND ((mm.format IN ('T20','T20I') AND over <= 6) OR (mm.format='ODI' AND over <= 10)))::INT)::INT pp_wkts,
             SUM((bowler_wicket AND ((mm.format IN ('T20','T20I') AND over >= 16) OR (mm.format='ODI' AND over >= 41)))::INT)::INT death_wkts,
             CASE WHEN ANY_VALUE(mm.winner) IS NULL THEN NULL ELSE ANY_VALUE(mm.winner) = d.bowling_team END AS won
      FROM dd d JOIN mm USING (match_id) GROUP BY d.match_id, mm.date, mm.gender, mm.format, mm.competition, mm.series,
             mm.stage, mm.city, mm.venue, mm.host, d.bowling_team, d.batting_team, d.innings, d.bowler""").df()
    team = con.execute("""
      SELECT i.match_id, mm.date, mm.gender, mm.format, mm.competition, mm.series, mm.stage, mm.city, mm.venue, mm.host,
             i.team, i.opp, i.innings, i.score, i.wkts, i.balls, i.target,
             CASE WHEN mm.winner IS NULL THEN NULL ELSE mm.winner = i.team END AS won, mm.win_by_runs, mm.win_by_wickets
      FROM (SELECT match_id, innings, batting_team team, bowling_team opp, MAX(team_score)::INT score,
                   MAX(team_wickets)::INT wkts, MAX(legal_balls_bowled)::INT balls, MAX(target_runs) AS target
            FROM dd GROUP BY ALL) i JOIN mm USING (match_id)""").df()
    duel = con.execute("""
      SELECT match_id, ANY_VALUE(mm.date) date, ANY_VALUE(mm.gender) gender, ANY_VALUE(mm.format) format,
             ANY_VALUE(mm.competition) competition, batter_id, bowler_id,
             SUM(runs_batter)::INT runs, SUM((wides=0)::INT)::INT balls,
             SUM((bowler_wicket AND player_out = batter)::INT)::INT outs
      FROM dd JOIN mm USING (match_id) GROUP BY match_id, batter_id, bowler_id""").df()
    # splits need bowling type (from player metadata) and phases
    meta = M.load()[["player_id", "btype", "bdetail"]]
    con.register("meta_df", meta)
    split_bat = con.execute("""
      SELECT d.batter_id player_id, mm.format, mm.gender, mm.competition, YEAR(mm.date) yr, t.bdetail vs,
             SUM(runs_batter)::INT runs, SUM((wides=0)::INT)::INT balls,
             SUM((player_out = batter AND bowler_wicket)::INT)::INT outs
      FROM dd d JOIN mm USING (match_id) JOIN meta_df t ON t.player_id = d.bowler_id
      WHERE t.bdetail IS NOT NULL GROUP BY ALL""").df()
    split_bowl = con.execute("""
      SELECT d.bowler_id player_id, mm.format, mm.gender, mm.competition, YEAR(mm.date) yr,
             CASE WHEN mm.format IN ('T20','T20I') THEN CASE WHEN over <= 6 THEN 'powerplay' WHEN over >= 16 THEN 'death' ELSE 'middle' END
                  WHEN mm.format = 'ODI' THEN CASE WHEN over <= 10 THEN 'powerplay' WHEN over >= 41 THEN 'death' ELSE 'middle' END
                  ELSE 'all' END AS phase,
             SUM(bowler_wicket::INT)::INT wkts, SUM(is_legal::INT)::INT balls, SUM(runs_batter + wides + noballs)::INT runs,
             SUM((is_dot AND is_legal)::INT)::INT dots
      FROM dd d JOIN mm USING (match_id) GROUP BY ALL""").df()
    return dict(hist_bat=bat, hist_bowl=bowl, hist_team=team, hist_duel=duel,
                hist_split_bat=split_bat, hist_split_bowl=split_bowl)


def save(tables, state):
    for k, v in tables.items():
        v.to_parquet(state / f"{k}.parquet", index=False)


def load(state):
    out = {}
    for k in ("hist_bat", "hist_bowl", "hist_team", "hist_duel", "hist_split_bat", "hist_split_bowl"):
        f = state / f"{k}.parquet"
        out[k] = pd.read_parquet(f) if f.exists() else None
    return out
