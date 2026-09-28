"""Entry point.
  python -m alerts.run daily   # Cricsheet update + records/milestones + watch/live pass
  python -m alerts.run live    # watch list + live milestone pass only (ESPN feed)
  python -m alerts.run test    # send a test message
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import telegram as tg

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
SENT = STATE / "sent.json"
CAREERS = STATE / "careers.parquet"
MAX_SINGLE = 40


def load_sent():
    return json.loads(SENT.read_text()) if SENT.exists() else {}


def save_sent(sent):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    sent = {k: v for k, v in sent.items() if v >= cutoff}
    STATE.mkdir(exist_ok=True)
    SENT.write_text(json.dumps(sent, indent=0, sort_keys=True))


LATE = {"record", "rare", "milestone"}   # from Cricsheet, days after the match: digest, not a ping


def deliver(alerts, sent):
    late = [a for a in alerts if a.get("kind") in LATE and a["key"] not in sent]
    if late:
        from . import scheduled
        scheduled.queue_digest([dict(key=a["key"], match=a.get("match", ""), text=a["headline"]) for a in late])
        now_ = datetime.now(timezone.utc).isoformat()
        for a in late:
            sent[a["key"]] = now_
    alerts = [a for a in alerts if a.get("kind") not in LATE]
    for a in alerts:
        if a.get("kind") == "watch":
            a["silent"] = True
    fresh = [a for a in alerts if a["key"] not in sent]
    fresh.sort(key=lambda a: -a.get("priority", 0))
    now = datetime.now(timezone.utc).isoformat()
    for a in fresh[:MAX_SINGLE]:
        try:
            tg.send_alert(a)
            sent[a["key"]] = now
        except Exception as e:  # noqa: BLE001
            print("send failed:", e, a["headline"])
    rest = fresh[MAX_SINGLE:]
    if rest:
        lines = ["<b>📋 More from today</b>"] + [f"• {tg.esc(a['headline'])}" for a in rest[:40]]
        tg.send("\n".join(lines), silent=True)
        for a in rest:
            sent[a["key"]] = now
    print(f"sent {len(fresh)} alerts")


def refresh_names():
    """Player display names (e.g. 'V Kohli' -> 'Virat Kohli') from the cricketdata R package."""
    f = STATE / "names.csv"
    try:
        import io
        import requests
        import rdata
        url = "https://raw.githubusercontent.com/robjhyndman/cricketdata/master/data/player_meta.rda"
        raw = requests.get(url, timeout=60).content
        tmp = ROOT / "data" / "player_meta.rda"
        tmp.write_bytes(raw)
        full = rdata.read_rda(str(tmp))["player_meta"]
        df = full[["cricsheet_id", "name"]].dropna()
        STATE.mkdir(exist_ok=True)
        df.to_csv(f, index=False)
        cols = [c for c in ("cricsheet_id", "cricinfo_id", "name", "full_name", "country", "dob", "batting_style",
                            "bowling_style", "playing_role") if c in full.columns]
        mf = full[cols].dropna(subset=["cricsheet_id"]).copy()
        if "dob" in mf and not pd.api.types.is_numeric_dtype(mf["dob"]):
            mf["dob"] = (pd.to_datetime(mf["dob"], errors="coerce") - pd.Timestamp("1970-01-01")).dt.days
        mf.to_csv(STATE / "meta.csv", index=False)
    except Exception as e:  # noqa: BLE001
        print("name refresh failed:", e)
    if not f.exists():
        return {}
    m = pd.read_csv(f, dtype=str).dropna()
    return dict(zip(m.cricsheet_id, m.name))


def daily():
    from . import build_data, stats
    first_run = not (STATE / "daily_done").exists()
    bf = STATE / "backfill_days"
    backfill = int(bf.read_text().strip()) if bf.exists() else (4 if first_run else 0)
    new_ids = build_data.update()
    con = stats.connect()
    print("latest match in data:", con.execute("SELECT MAX(start_date) FROM m").fetchone()[0])
    if backfill:   # re-check recent matches (first run, or on request)
        new_ids = [r[0] for r in con.execute("SELECT match_id FROM m WHERE start_date >= ?",
                                             [pd.Timestamp.now() - pd.Timedelta(days=backfill)]).fetchall()]
        if bf.exists():
            bf.unlink()
    if new_ids:
        recent_cut = pd.Timestamp.now() - pd.Timedelta(days=max(backfill, 10))
        recent = [r[0] for r in con.execute(
            "SELECT match_id FROM m WHERE match_id IN (SELECT UNNEST(?)) AND start_date >= ?",
            [new_ids, recent_cut]).fetchall()]
    else:
        recent = []
    print(f"{len(new_ids)} new matches, {len(recent)} recent enough to alert on")
    alerts = stats.detect(con, recent)
    after = stats.career_table(con)
    if recent:
        players = set(con.execute("""SELECT player_id FROM bat WHERE match_id IN (SELECT UNNEST(?))
                                     UNION SELECT player_id FROM bowl WHERE match_id IN (SELECT UNNEST(?))""",
                                  [recent, recent]).df().player_id)
        con.execute("DELETE FROM bat WHERE match_id IN (SELECT UNNEST(?))", [recent])
        con.execute("DELETE FROM bowl WHERE match_id IN (SELECT UNNEST(?))", [recent])
        before = stats.career_table(con)
        alerts += stats.milestone_alerts(before, after, players)
    # attach Cricinfo player ids for the Verify button
    import shutil
    STATE.mkdir(exist_ok=True)
    shutil.copy(ROOT / "data" / "people.csv", STATE / "people.csv")
    ppl = pd.read_csv(STATE / "people.csv", dtype=str)
    ci = dict(zip(ppl.identifier, ppl.key_cricinfo))
    dn = refresh_names()
    for a in alerts:
        if a.get("player_id"):
            a["cricinfo_player"] = ci.get(a["player_id"])
            full = dn.get(a["player_id"])
            if full and a.get("player"):
                a["caption"] = a["caption"].replace(a["player"], full)
    from . import history
    (STATE / "hist").mkdir(exist_ok=True)
    history.save(history.build(con), STATE / "hist")     # kept in the Actions cache, not in git
    keep = after[pd.to_datetime(after.last_match) >= pd.Timestamp.now() - pd.Timedelta(days=3 * 365)]
    STATE.mkdir(exist_ok=True)
    keep.to_parquet(CAREERS, index=False)
    sent = load_sent()
    deliver(alerts, sent)
    save_sent(sent)
    (STATE / "daily_done").write_text(datetime.now(timezone.utc).isoformat())
    live()


def live():
    from . import watch
    if not CAREERS.exists():
        print("No career table yet - run daily first")
        return
    sent = load_sent()
    alerts = watch.run(pd.read_parquet(CAREERS), sent)
    deliver(alerts, sent)
    save_sent(sent)


def morning():
    """One silent morning pack: previews, on this day, series wraps, Monday leaderboards, Thursday comparisons,
    plus yesterday's weaker live options as a single list."""
    from . import espn, live_angles, scheduled
    ctx = live_angles.load_context()
    if ctx is None:
        print("no history yet")
        return
    try:
        evs = espn.events()
    except Exception as e:  # noqa: BLE001
        print("ESPN events failed:", e)
        evs = []
    posts, digest = scheduled.morning_pack(ctx, evs)
    sent = load_sent()
    posts = [p for p in posts if p["key"] not in sent]
    if posts or digest:
        tg.send(f"☀️ <b>Morning pack</b>: {len(posts)} ready-to-post idea{'s' * (len(posts) != 1)}"
                + (f" + {len(digest)} more options from yesterday" if digest else ""), silent=True)
    now_ = datetime.now(timezone.utc).isoformat()
    for p in posts:
        try:
            tg.send_alert(p)
            sent[p["key"]] = now_
        except Exception as e:  # noqa: BLE001
            print("send failed:", e)
    if digest:
        lines = ["<b>🗒 More options from yesterday</b> (weaker, but usable)"]
        for d in digest[:40]:
            lines.append(f"• <b>{tg.esc(d.get('match', ''))}</b>: {tg.esc(d['text'])}")
        tg.send("\n".join(lines), silent=True)
    save_sent(sent)
    print(f"morning pack: {len(posts)} posts, {len(digest)} digest items")


def match():
    """Run the stat engine on specific finished matches: python -m alerts.run match LEAGUE:EVENT ... [--send]
    --wide: don't hold back sub-threshold performances for the digest - give every performance that has any
    qualifying facts its own full alert (still deduped against what's already been sent)."""
    from . import espn, live_angles, rotation
    from .watch import ci_map, classify, _plain
    from . import nuggets as N
    from . import angles as A
    import alerts.config as C
    args = [a for a in sys.argv[2:] if ":" in a]
    send = "--send" in sys.argv
    wide = "--wide" in sys.argv
    ctx = live_angles.load_context()
    ci2cs = ci_map()
    log = rotation.load()
    sent = load_sent() if send else {}
    if "--force" in sys.argv:     # re-evaluate matches the old engine already covered
        ids = {a.split(":")[1] for a in args}
        sent = {k: v for k, v in sent.items() if not (k.startswith("nug:") and k.split(":")[1] in ids)}
    if wide:
        A.LEAD_MIN = -10_000   # nothing gets diverted to the quiet/digest queue this run
    out = []
    for a in args:
        lg, eid = a.split(":")
        js = espn._get(espn.SUMMARY.format(league=lg), event=eid)
        comp = ((js.get("header") or {}).get("competitions") or [{}])[0]
        cs = comp.get("competitors") or []
        venue = ((js.get("gameInfo") or {}).get("venue") or {})
        cls = comp.get("class") or {}
        ev = dict(id=eid, league_id=lg, league=((js.get("header") or {}).get("league") or {}).get("name", ""),
                  name=" v ".join((c.get("team") or {}).get("displayName", "") for c in cs),
                  description=comp.get("description") or "", date=comp.get("date") or "",
                  status=((comp.get("status") or {}).get("type") or {}).get("state", "post"),
                  summary=((comp.get("status") or {}).get("summary")) or "",
                  intl_class=str(cls.get("internationalClassId") or "0"),
                  teams=[(c.get("team") or {}).get("displayName") for c in cs],
                  city=((venue.get("address") or {}).get("city")) or "", location=venue.get("fullName") or "",
                  short=" v ".join((c.get("team") or {}).get("abbreviation", "") for c in cs),
                  home=next(((c.get("team") or {}).get("displayName") for c in cs if c.get("homeAway") == "home"), None))
        espn._cache.pop(eid, None)
        fmt, gender, scopes, teams = classify(ev)
        if not scopes:
            print("not in scope:", ev["name"], ev["intl_class"])
            continue
        host = None if scopes[0] in C.NUGGET_LEAGUES else N.host_for(ev["city"], _plain(ev.get("home") or ""), ctx.ht)
        tag = "#" + ev["short"].replace(" ", "") if ev.get("short") else "#Cricket"
        got = live_angles.match_alerts(ev, fmt, gender, scopes[0], teams, ci2cs, host, sent, set(), log, tag)
        print(ev["name"], ev["description"], ev["city"], "->", len(got), "alerts")
        out += got
    lines = []
    for a in sorted(out, key=lambda a: -a.get("priority", 0)):
        lines.append(f"[{a.get('priority')}] {a.get('family', a.get('kind'))} | {a.get('headline')}\n{a.get('caption')}\n{a.get('note', '')}\n")
    (STATE / "last_match.log").write_text("\n".join(lines) or "nothing")
    if send:
        deliver(out, sent)
        save_sent(sent)
        rotation.save(log)


def checktigzig():
    """One-off: sanity-check our own history tables against the free Tigzig Cricsheet-based SQL API
    (https://db-mcp.tigzig.com). Prints a report to state/last_tigzig.log; sends nothing."""
    import requests

    from . import history

    lines = []

    def log(*a):
        s = " ".join(str(x) for x in a)
        print(s)
        lines.append(s)

    def q(engine, sql):
        r = requests.get(f"https://db-mcp.tigzig.com/v1/query/{engine}",
                          params={"sql": sql, "format": "json"}, timeout=30)
        r.raise_for_status()
        j = r.json()
        if j.get("rows") is None:
            raise ValueError(j)
        return [dict(zip(j["columns"], row)) for row in j["rows"]]

    def first_that_works(engine, queries, key):
        """Try candidate SQL strings in order (table/column names aren't documented), return (value, sql-used)."""
        errs = []
        for sql in queries:
            try:
                rows = q(engine, sql)
                return (rows[0].get(key) if rows else None), sql
            except Exception as e:  # noqa: BLE001
                errs.append(f"{sql[:60]}... -> {e}")
        return None, "; ".join(errs)

    hist_dir = STATE / "hist"
    h = history.load(hist_dir if hist_dir.exists() else STATE)
    hb, hw = h.get("hist_bat"), h.get("hist_bowl")
    if hb is None:
        log("no local history tables available (run build first)")
        (STATE / "last_tigzig.log").write_text("\n".join(lines))
        return

    for engine in ("duckdb", "postgres"):
        try:
            rows = q(engine, "SELECT table_name FROM information_schema.tables ORDER BY 1")
            log(f"[{engine}] tables:", [r["table_name"] for r in rows])
        except Exception as e:  # noqa: BLE001
            log(f"[{engine}] table listing failed:", e)

    # coverage / freshness: does it already have the two ODIs we processed on 2026-09-27?
    val, used = first_that_works("postgres", [
        "SELECT COUNT(*) n FROM match_info WHERE start_date >= '2026-09-25'",
        "SELECT COUNT(*) n FROM match_info WHERE start_date::date >= DATE '2026-09-25'",
    ], "n")
    log("recent (>=2026-09-25) matches in match_info:", val, "|", used if val is None else "")

    for tbl in ("match_info_odi_men", "match_info_odi_women", "match_info_t20_men", "match_info_t20_women",
                "match_info_test_men", "match_info_test_women", "match_info_ipl"):
        try:
            rows = q("postgres", f"SELECT MIN(start_date) mn, MAX(start_date) mx, COUNT(*) n FROM {tbl}")
        except Exception as e:  # noqa: BLE001
            log(f"{tbl}: query failed -> {e}")
            continue
        r = rows[0] if rows else {}
        log(f"{tbl}: {r.get('n')} matches, {r.get('mn')} to {r.get('mx')}")
    for fmt in ("ODI", "T20I", "Test"):
        d = hb[hb.format == fmt].date
        if len(d):
            log(f"our hist_bat: {fmt} covers {d.min().date()} to {d.max().date()} ({d.dt.date.nunique()} distinct dates)")

    ours_kohli = hb[(hb.player == "V Kohli") & (hb.format == "ODI")]
    our_runs, our_100s = int(ours_kohli.runs.sum()), int((ours_kohli.runs >= 100).sum())
    log(f"our hist_bat: V Kohli ODI runs={our_runs}, 100s={our_100s}, innings={len(ours_kohli)}")
    for engine, table in (("postgres", "ball_by_ball_odi_men"), ("postgres", "odi_ball_by_ball"),
                          ("postgres", "ball_by_ball")):
        val, used = first_that_works(engine, [
            f"SELECT SUM(runs_off_bat) runs, COUNT(DISTINCT match_id) inns FROM {table} "
            f"WHERE striker='V Kohli'" + (" AND match_type='ODI'" if table == "ball_by_ball" else "")],
            "runs")
        if val is not None:
            log(f"tigzig [{engine}.{table}] V Kohli ODI runs={val}  (query: {used})")
            break
    else:
        log("tigzig: couldn't find a working ball-by-ball table name for Kohli check - errors above")

    ours_kuldeep = hw[(hw.player == "Kuldeep Yadav") & (hw.format == "ODI")]
    our_wkts = int(ours_kuldeep.wkts.sum())
    log(f"our hist_bowl: Kuldeep Yadav ODI wickets={our_wkts}, innings={len(ours_kuldeep)}")

    (STATE / "last_tigzig.log").write_text("\n".join(lines))
    print("wrote state/last_tigzig.log")


def datatest():
    """Query TigZig and send a compact historical-data coverage check to Telegram."""
    import requests

    endpoint = "https://db-mcp.tigzig.com/v1/query/postgres"

    def q(sql):
        r = requests.post(endpoint, json={"sql": sql, "format": "json"}, timeout=30)
        r.raise_for_status()
        js = r.json()
        return [dict(zip(js["columns"], row)) for row in js["rows"]]

    totals = q("""
        SELECT
          (SELECT COUNT(*) FROM match_info) AS matches,
          (SELECT COUNT(*) FROM ball_by_ball) AS deliveries
    """)[0]
    coverage = q("""
        SELECT 'Men ODI' AS scope, MIN(start_date) AS first_match, MAX(start_date) AS latest_match
        FROM match_info_odi_men
        UNION ALL
        SELECT 'Women ODI', MIN(start_date), MAX(start_date) FROM match_info_odi_women
        UNION ALL
        SELECT 'Men T20I', MIN(start_date), MAX(start_date) FROM match_info_t20_men
        UNION ALL
        SELECT 'Women T20I', MIN(start_date), MAX(start_date) FROM match_info_t20_women
        UNION ALL
        SELECT 'Men Test', MIN(start_date), MAX(start_date) FROM match_info_test_men
        UNION ALL
        SELECT 'Women Test', MIN(start_date), MAX(start_date) FROM match_info_test_women
        UNION ALL
        SELECT 'IPL', MIN(start_date), MAX(start_date) FROM match_info_ipl
    """)

    lines = [
        "<b>📊 DATA CHECK · TigZig / Cricsheet</b>",
        f"{int(totals['matches']):,} matches · {int(totals['deliveries']):,} deliveries",
        "",
        "<b>Coverage currently available</b>",
    ]
    scope_order = {name: i for i, name in enumerate(
        ("Men Test", "Women Test", "Men ODI", "Women ODI", "Men T20I", "Women T20I", "IPL"))}
    coverage.sort(key=lambda row: scope_order.get(row["scope"], 99))
    for row in coverage:
        first = str(row["first_match"])[:10]
        latest = str(row["latest_match"])[:10]
        lines.append(f"• {tg.esc(row['scope'])}: {first} → {latest}")
    lines += [
        "",
        "<i>Historical database only. A finished live match may appear later after Cricsheet publishes it.</i>",
        "Source: Cricsheet via TigZig (ODC-BY)",
    ]
    tg.send("\n".join(lines))
    print("TigZig data check sent to Telegram")


def build():
    """Refresh data, history tables and player metadata without sending anything."""
    from . import build_data, history, stats
    build_data.update()
    con = stats.connect()
    import shutil
    STATE.mkdir(exist_ok=True)
    shutil.copy(ROOT / "data" / "people.csv", STATE / "people.csv")
    refresh_names()
    (STATE / "hist").mkdir(exist_ok=True)
    history.save(history.build(con), STATE / "hist")
    after = stats.career_table(con)
    after[pd.to_datetime(after.last_match) >= pd.Timestamp.now() - pd.Timedelta(days=3 * 365)].to_parquet(CAREERS, index=False)
    print("history built")


def preview():
    """Run the live pass without sending anything; write what would be posted to state/last_preview.log."""
    from . import watch
    import alerts.telegram as T
    sent = {k: v for k, v in load_sent().items() if not k.startswith(("final:", "nug:", "digest:"))}
    alerts = watch.run(pd.read_parquet(CAREERS), sent)
    lines = []
    for a in sorted(alerts, key=lambda a: -a.get("priority", 0)):
        lines.append(f"[{a.get('priority')}] {a.get('family', a.get('kind'))} | {a.get('headline')}\n{a.get('caption')}\n{a.get('note', '')}\n")
    (STATE / "last_preview.log").write_text("\n".join(lines) or "nothing to post")
    print(f"{len(alerts)} alerts previewed")


def status():
    """Send a progress report for the matches listed in state/focus_matches (ESPN event ids)."""
    from . import watch
    ids = (STATE / "focus_matches").read_text().split()
    for msg in watch.status(pd.read_parquet(CAREERS), set(ids)):
        tg.send(msg)
    print(f"sent status for {len(ids)} matches")


def test():
    tg.send("✅ <b>Cricket alerts connected.</b>\nYou'll get record, milestone and watch-list alerts here.",
            [("📝 Example: Post on X", tg.x_intent("Testing my cricket stats alerts 🏏"))])
    print("test message sent to chat(s)", tg.chat_ids())


def listchats():
    """Print every chat (private/group/channel) that has recently messaged the bot, with its chat_id -
    use this to find a group's id after adding the bot and posting a message there."""
    for line in tg.list_chats():
        print(line)


if __name__ == "__main__":
    import os
    import traceback
    try:
        {"daily": daily, "live": live, "test": test, "status": status, "preview": preview, "build": build,
         "morning": morning, "match": match, "checktigzig": checktigzig,
         "datatest": datatest, "listchats": listchats}[sys.argv[1] if len(sys.argv) > 1 else "daily"]()
    except Exception:  # print the error with the bot token removed (logs are public)
        tok = os.environ.get("TELEGRAM_BOT_TOKEN") or "~no-token~"
        print(traceback.format_exc().replace(tok, "***"))
        sys.exit(1)
