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
MAX_SINGLE = 15


def load_sent():
    return json.loads(SENT.read_text()) if SENT.exists() else {}


def save_sent(sent):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    sent = {k: v for k, v in sent.items() if v >= cutoff}
    STATE.mkdir(exist_ok=True)
    SENT.write_text(json.dumps(sent, indent=0, sort_keys=True))


def deliver(alerts, sent):
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
        df = rdata.read_rda(str(tmp))["player_meta"][["cricsheet_id", "name"]].dropna()
        STATE.mkdir(exist_ok=True)
        df.to_csv(f, index=False)
    except Exception as e:  # noqa: BLE001
        print("name refresh failed:", e)
    if not f.exists():
        return {}
    m = pd.read_csv(f, dtype=str).dropna()
    return dict(zip(m.cricsheet_id, m.name))


def daily():
    from . import build_data, stats
    first_run = not (ROOT / "data" / "matches.parquet").exists() or not load_sent()
    new_ids = build_data.update()
    con = stats.connect()
    if first_run:   # nothing delivered yet: alert on the last few days so there's something to see
        new_ids = [r[0] for r in con.execute("SELECT match_id FROM m WHERE start_date >= ?",
                                             [pd.Timestamp.now() - pd.Timedelta(days=4)]).fetchall()]
    if new_ids:
        recent_cut = pd.Timestamp.now() - pd.Timedelta(days=4 if first_run else 10)
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
    keep = after[pd.to_datetime(after.last_match) >= pd.Timestamp.now() - pd.Timedelta(days=3 * 365)]
    STATE.mkdir(exist_ok=True)
    keep.to_parquet(CAREERS, index=False)
    sent = load_sent()
    deliver(alerts, sent)
    save_sent(sent)
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


def test():
    tg.send("✅ <b>Cricket alerts connected.</b>\nYou'll get record, milestone and watch-list alerts here.",
            [("📝 Example: Post on X", tg.x_intent("Testing my cricket stats alerts 🏏"))])
    print("test message sent to chat", tg.chat_id())


if __name__ == "__main__":
    import os
    import traceback
    try:
        {"daily": daily, "live": live, "test": test}[sys.argv[1] if len(sys.argv) > 1 else "daily"]()
    except Exception:  # print the error with the bot token removed (logs are public)
        tok = os.environ.get("TELEGRAM_BOT_TOKEN") or "~no-token~"
        print(traceback.format_exc().replace(tok, "***"))
        sys.exit(1)
