"""Send alerts to Telegram with 'Post on X' and 'Verify' buttons."""
import html
import os
import time
import urllib.parse
from pathlib import Path

import requests

STATE = Path(__file__).resolve().parents[1] / "state"


def _token():
    t = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return t


def _api(method, **payload):
    try:
        r = requests.post(f"https://api.telegram.org/bot{_token()}/{method}", json=payload, timeout=30)
        js = r.json()
    except Exception as e:  # noqa: BLE001  (hide the URL, it contains the token)
        raise RuntimeError(f"Telegram {method}: network error {type(e).__name__}") from None
    if not js.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {js.get('description')} (code {js.get('error_code')})")
    return js["result"]


def chat_id():
    cid = os.environ.get("TELEGRAM_CHAT_ID") or ""
    f = STATE / "chat_id.txt"
    if not cid and f.exists():
        cid = f.read_text().strip()
    if not cid:
        ups = _api("getUpdates")
        chats = [u["message"]["chat"]["id"] for u in ups if "message" in u and u["message"]["chat"]["type"] == "private"]
        if not chats:
            raise RuntimeError("No chat found. Open your bot in Telegram and press Start, then rerun.")
        cid = str(chats[-1])
        STATE.mkdir(exist_ok=True)
        f.write_text(cid)
    return cid


def x_intent(text):
    return "https://x.com/intent/post?text=" + urllib.parse.quote(text[:280])


def send(text, buttons=None, silent=False):
    payload = dict(chat_id=chat_id(), text=text[:4000], parse_mode="HTML",
                   disable_web_page_preview=True, disable_notification=silent)
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "url": u}] for t, u in buttons]}
    res = _api("sendMessage", **payload)
    time.sleep(1.1)  # stay well inside Telegram rate limits
    return res


def esc(s):
    return html.escape(str(s))


LABEL = {"preview": "🔜 PREVIEW", "onthisday": "🗓 ON THIS DAY", "wrap": "🏁 SERIES WRAP", "leaders": "📈 LEADERBOARD",
         "compare": "⚖️ COMPARISON", "nugget": "📊 STAT", "digest": "🗒 MATCH NOTES", "record": "🏆 RECORD", "rare": "⚡ RARE", "milestone": "🎯 MILESTONE",
         "live": "🔴 LIVE MILESTONE", "watch": "👀 WATCH"}


def send_alert(a):
    lines = [f"<b>{LABEL.get(a['kind'], a['kind'].upper())}</b> · {esc(a.get('competition') or a.get('scope') or '')}",
             f"<b>{esc(a['headline'])}</b>"]
    if a.get("match"):
        lines.append(f"{esc(a['match'])} · {esc(a.get('date', ''))}")
    if a.get("note"):
        n = esc(a["note"])
        for t in ("i", "b"):
            n = n.replace(f"&lt;{t}&gt;", f"<{t}>").replace(f"&lt;/{t}&gt;", f"</{t}>")
        lines.append(n)
    if a.get("needs_check"):
        lines.append("⚠️ <i>Check the official figure before posting. Early career matches may be missing from the data.</i>")
    buttons = []
    if a["kind"] != "digest":
        lines += ["", "<b>Draft post:</b>", esc(a["caption"])]
        buttons.append(("📝 Post on X", x_intent(a["caption"])))
    if a.get("cricinfo_player"):
        buttons.append(("🔎 Player on Cricinfo", f"https://www.espncricinfo.com/ci/content/player/{a['cricinfo_player']}.html"))
    if a.get("match_id") and str(a["match_id"]).isdigit():
        buttons.append(("📋 Scorecard", f"https://www.espncricinfo.com/matches/engine/match/{a['match_id']}.html"))
    return send("\n".join(lines), buttons, silent=bool(a.get("silent")))
