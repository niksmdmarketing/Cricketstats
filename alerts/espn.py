"""Live/upcoming matches and scorecards from ESPN's public cricket feed (the data behind
Cricinfo scorecards). No key needed. Keep request volume low: one list call plus one
summary call per relevant match per run."""
import time

import requests

HEADER = "https://site.web.api.espn.com/apis/v2/scoreboard/header"
SUMMARY = "https://site.web.api.espn.com/apis/site/v2/sports/cricket/{league}/summary"
UA = {"User-Agent": "Mozilla/5.0 (personal cricket stats alerts; low volume)"}
_calls = {"n": 0}
MAX_CALLS_PER_RUN = 25


def _get(url, **params):
    if _calls["n"] >= MAX_CALLS_PER_RUN:
        raise RuntimeError("request cap for this run reached")
    _calls["n"] += 1
    r = requests.get(url, params=params, headers=UA, timeout=30)
    r.raise_for_status()
    time.sleep(1.5)
    return r.json()


def events():
    """Current, recent and upcoming matches: list of dicts."""
    js = _get(HEADER, sport="cricket", lang="en", region="au")
    out = []
    for sp in js.get("sports", []):
        for lg in sp.get("leagues", []):
            for e in lg.get("events", []) or []:
                cls = e.get("class") or {}
                teams = [c.get("displayName") or (c.get("team") or {}).get("displayName") for c in e.get("competitors") or []]
                out.append(dict(
                    id=str(e.get("id")), league_id=str(lg.get("id")), league=lg.get("name") or "",
                    name=e.get("name") or "", description=e.get("description") or "",
                    date=e.get("date") or "", status=e.get("status") or "",   # pre / in / post
                    summary=e.get("summary") or "",
                    intl_class=str(cls.get("internationalClassId") or "0"),
                    event_type=cls.get("eventType") or e.get("eventType") or "",
                    general=cls.get("generalClassCard") or "",
                    teams=[t for t in teams if t],
                    link=e.get("link") or "",
                    location=e.get("location") or "",
                    city=(e.get("location") or "").split(",")[-1].strip(),
                    short=e.get("shortName") or "",
                    home=next((c.get("displayName") for c in e.get("competitors") or [] if c.get("homeAway") == "home"), None),
                ))
    return out


_cache = {}


def detail(ev):
    """Full match detail (cached per run):
    players: {cricinfo_id: {name, team, runs, wkts, balls, conceded, bat: [(inn, runs, balls, out)], bowl: [(inn, wkts, conceded)]}}
    innings: [(team, period, runs, wickets, overs, target)], winner: team name or None"""
    if ev["id"] in _cache:
        return _cache[ev["id"]]
    js = _get(SUMMARY.format(league=ev["league_id"]), event=ev["id"])
    players = {}
    for t in js.get("rosters") or []:
        team = (t.get("team") or {}).get("displayName")
        for p in t.get("roster") or []:
            a = p.get("athlete") or {}
            pid = str(a.get("id") or "")
            if not pid:
                continue
            rec = players.setdefault(pid, {"name": a.get("displayName") or a.get("name"), "team": team,
                                           "runs": 0, "wkts": 0, "balls": 0, "conceded": 0, "bat": [], "bowl": []})
            for per in p.get("linescores") or []:
                inn = per.get("period")
                for ls in per.get("linescores") or []:
                    st = {}
                    for c in ((ls.get("statistics") or {}).get("categories") or []):
                        for s_ in c.get("stats") or []:
                            st[s_.get("name")] = s_.get("value")
                    if "runs" in st and "ballsFaced" in st and int(float(st.get("batted") or 0)):
                        r, b = int(float(st.get("runs") or 0)), int(float(st.get("ballsFaced") or 0))
                        rec["runs"] += r
                        rec["balls"] += b
                        rec["bat"].append((inn, r, b, int(float(st.get("outs") or 0))))
                    if "wickets" in st and "conceded" in st and float(st.get("balls") or 0) > 0:
                        w, c_ = int(float(st.get("wickets") or 0)), int(float(st.get("conceded") or 0))
                        rec["wkts"] += w
                        rec["conceded"] += c_
                        rec["bowl"].append((inn, w, c_))
    innings, winner = [], None
    for comp in ((js.get("header") or {}).get("competitions") or [])[:1]:
        for c in comp.get("competitors") or []:
            tname = (c.get("team") or {}).get("displayName")
            if c.get("winner") in (True, "true"):
                winner = tname
            for ls in c.get("linescores") or []:
                if ls.get("score") or int(ls.get("runs") or 0) > 0:
                    innings.append((tname, ls.get("period"), int(ls.get("runs") or 0), int(ls.get("wickets") or 0),
                                    ls.get("overs"), int(ls.get("target") or 0)))
    out = {"players": players, "innings": innings, "winner": winner}
    _cache[ev["id"]] = out
    return out


def scorecard(ev):
    """{cricinfo_player_id: {'name','team','runs','wkts','balls',...}} summed across innings."""
    return detail(ev)["players"]
