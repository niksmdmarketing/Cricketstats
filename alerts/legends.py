"""All-time career comparisons ("moves past Tendulkar", "only Kallis has more") from the Statsguru snapshot.

Snapshot totals are exact as of the snapshot date. A player's total right now is the snapshot figure
plus innings recorded in our history after that date (ESPN/Cricsheet), so active players stay current
between weekly refreshes.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
CODE = {"IND": "India", "AUS": "Australia", "ENG": "England", "SA": "South Africa", "NZ": "New Zealand",
        "PAK": "Pakistan", "SL": "Sri Lanka", "WI": "West Indies", "BAN": "Bangladesh", "AFG": "Afghanistan",
        "IRE": "Ireland", "ZIM": "Zimbabwe", "SCOT": "Scotland", "NL": "Netherlands", "NEPAL": "Nepal",
        "UAE": "United Arab Emirates", "OMAN": "Oman", "NAM": "Namibia", "USA": "United States of America",
        "CAN": "Canada", "HKG": "Hong Kong", "PNG": "Papua New Guinea", "KENYA": "Kenya"}
# stat -> (snapshot table, column(s) summed)
STAT = {"runs": ("bat", ["Runs"]), "hundreds": ("bat", ["100"]), "fifties": ("bat", ["50"]),
        "fifty_plus": ("bat", ["100", "50"]), "sixes": ("bat", ["6s"]),
        "wkts": ("bowl", ["Wkts"]), "fivefors": ("bowl", ["5"]), "fourplus": ("bowl", ["4", "5"])}
NOUN = {"runs": ("run", "runs"), "hundreds": ("hundred", "hundreds"), "fifties": ("fifty", "fifties"),
        "fifty_plus": ("50+ score", "50+ scores"), "sixes": ("six", "sixes"), "wkts": ("wicket", "wickets"),
        "fivefors": ("five-wicket haul", "five-wicket hauls"), "fourplus": ("four-wicket haul", "four-wicket hauls")}


def _country(code):
    for c in str(code).replace("-W", "").split("/"):
        if c in CODE:
            return CODE[c]
    return None


class Legends:
    def __init__(self, people=None):
        self.ok = False
        try:
            bat = pd.read_csv(STATE / "alltime_batting.csv", dtype={"cricinfo_id": str})
            bowl = pd.read_csv(STATE / "alltime_bowling.csv", dtype={"cricinfo_id": str})
        except FileNotFoundError:
            return
        for d in (bat, bowl):
            for c in ("Runs", "100", "50", "6s", "Wkts", "5", "4", "Mat", "Inns"):
                if c in d:
                    d[c] = pd.to_numeric(d[c].astype(str).str.replace("*", "", regex=False).str.replace("-", "0"),
                                         errors="coerce").fillna(0).astype(int)
            d["team"] = d.country.map(_country)
            d["last_year"] = pd.to_numeric(d.Span.astype(str).str[-4:], errors="coerce")
        self.snapshot = pd.Timestamp(bat.snapshot.iloc[0])
        self.t = {"bat": bat, "bowl": bowl}
        # cricinfo id -> cricsheet id
        self.ci2cs = {}
        if people is not None:
            for col in [c for c in people.columns if c.startswith("key_cricinfo")]:
                for pid, ci in zip(people.identifier, people[col]):
                    if isinstance(ci, str) and ci:
                        self.ci2cs[ci.split(".")[0]] = pid
        for d in self.t.values():
            d["player_id"] = d.cricinfo_id.map(self.ci2cs)
        self.ok = True

    # ------------------------------------------------------------------ totals
    def table(self, stat, fmt, gender, hb=None, hw=None, exclude_match=None):
        """All-time list for a stat: player_id/cricinfo_id, name, team, value (snapshot + later innings)."""
        src, cols = STAT[stat]
        d = self.t[src]
        d = d[(d.format == fmt) & (d.gender == gender)].copy()
        d["value"] = d[cols].sum(axis=1)
        # add innings after the snapshot for players we can link
        h = hb if src == "bat" else hw
        if h is not None and len(h):
            x = h[(h.format == fmt) & (h.gender == gender) & (pd.to_datetime(h.date) > self.snapshot)]
            if exclude_match is not None:
                x = x[x.match_id.astype(str) != str(exclude_match)]
            if len(x):
                inc = {"runs": x.groupby("player_id").runs.sum() if src == "bat" else None}
                if src == "bat":
                    g = x.groupby("player_id")
                    inc = {"runs": g.runs.sum(), "hundreds": g.runs.apply(lambda r: (r >= 100).sum()),
                           "fifties": g.runs.apply(lambda r: ((r >= 50) & (r < 100)).sum()),
                           "fifty_plus": g.runs.apply(lambda r: (r >= 50).sum()), "sixes": g.sixes.sum()}
                else:
                    g = x.groupby("player_id")
                    inc = {"wkts": g.wkts.sum(), "fivefors": g.wkts.apply(lambda w: (w >= 5).sum()),
                           "fourplus": g.wkts.apply(lambda w: (w >= 4).sum())}
                d["value"] = d.value + d.player_id.map(inc[stat]).fillna(0).astype(int)
        return d[["player_id", "cricinfo_id", "name", "team", "value", "last_year"]].sort_values("value", ascending=False)

    def value(self, pid, stat, fmt, gender, hb=None, hw=None, exclude_match=None):
        t = self.table(stat, fmt, gender, hb, hw, exclude_match)
        r = t[t.player_id == pid]
        return (int(r.value.iloc[0]) if len(r) else None), t

    # ------------------------------------------------------------------ facts
    def facts(self, pid, name, team, fmt, gender, stat, gained, hb, hw, match_id, fact, names=None, match_date=None):
        """gained: this match's contribution to the stat. Returns fact tuples via fact(angle, text, bonus)."""
        if not self.ok or gained <= 0:
            return []
        if match_date is not None and pd.Timestamp(match_date).normalize() <= self.snapshot:
            return []   # snapshot may already include this match
        before, t = self.value(pid, stat, fmt, gender, hb, hw, exclude_match=match_id)
        if before is None:
            return []
        now = before + gained
        short = t["name"].map(_surname)
        dup = set(short[short.duplicated()])

        def nm(r):
            s_ = _surname(r["name"])
            return r["name"] if s_ in dup else s_
        others = t[t.player_id != pid]
        one, many = NOUN[stat]
        P = {"Test": "Test", "ODI": "ODI", "T20I": "T20I"}[fmt]
        sex = "" if gender == "male" else "women's "
        out = []
        passed = others[(others.value >= before) & (others.value < now)]
        equal = others[others.value == now]
        above = others[others.value > now]
        rank = len(above) + 1
        nat = others[others.team == team]
        nat_above = nat[nat.value > now]
        nat_rank = len(nat_above) + 1
        big = {"runs": 1000, "hundreds": 5, "fifties": 10, "fifty_plus": 15, "sixes": 30, "wkts": 50,
               "fivefors": 3, "fourplus": 5}[stat]
        if now < big:
            return []

        def lst(rows, n=3):
            rows = rows.head(n)
            names_ = [f"{nm(r)} ({r.value:,})" for _, r in rows.iterrows()]
            return ", ".join(names_[:-1]) + (" and " if len(names_) > 1 else "") + names_[-1] if names_ else ""

        nat_passed = passed[passed.team == team]
        if len(passed) and rank <= 15:
            top = passed.sort_values("value", ascending=False).iloc[0]
            bonus = 20 if rank <= 3 else 10 if rank <= 10 else 0
            out.append(fact("legend_pass", f"moves past {nm(top)}'s {top.value:,} {sex}{P} {many} into {ordn(rank)} on the all-time list", bonus))
        elif len(nat_passed) and nat_rank <= 5:
            top = nat_passed.sort_values("value", ascending=False).iloc[0]
            tie = len(nat[nat.value == now])
            out.append(fact("legend_pass", f"moves past {nm(top)} ({top.value:,}) into {'joint-' if tie else ''}{ordn(nat_rank)} on {team}'s all-time {sex}{P} {one} list",
                            5 if nat_rank <= 2 else 0 if nat_rank == 3 else -12))
        if len(equal) and not len(passed):
            e = equal.iloc[0]
            txt = f"draws level with {nm(e)} on {now:,} {sex}{P} {many}"
            if 0 < len(above) <= 3:
                txt += f"; only {lst(above)} {'has' if len(above) == 1 else 'have'} more"
            out.append(fact("legend_equal", txt, 15 if rank <= 5 else 0))
        if rank == 1 and not len(passed) and not len(equal):
            out.append(fact("legend_record", f"extends his all-time {sex}{P} record to {now:,} {many}", 15))
        if not len(passed) and not len(equal) and 1 <= len(above) <= 3 and rank <= 4:
            out.append(fact("legend_only", f"only {lst(above)} {'has' if len(above) == 1 else 'have'} more {sex}{P} {many}", 10))
        # joining a club at a round number
        for mark in _marks(stat):
            if before < mark <= now:
                club = others[others.value >= mark].sort_values("value", ascending=False)
                n = len(club) + 1
                if n == 1:
                    out.append(fact("legend_club", f"the first player ever to {mark:,} {sex}{P} {many}", 40))
                elif n <= 4:
                    out.append(fact("legend_club", f"joins {lst(club, 3)} as the only players with {mark:,} {sex}{P} {many}", 25))
                elif n <= 15:
                    out.append(fact("legend_club", f"only the {ordn(n)} player to reach {mark:,} {sex}{P} {many}", 10))
                nclub = club[club.team == team]
                if len(nclub) == 0 and n > 1:
                    out.append(fact("legend_club", f"the first {team} player to reach {mark:,} {sex}{P} {many}", 20))
                elif 1 <= len(nclub) <= 2:
                    out.append(fact("legend_club", f"joins {lst(nclub, 2)} as the only {team} players with {mark:,} {sex}{P} {many}", 12))
        # national list position for counting stats (four-fors, hundreds, ...)
        if stat in ("hundreds", "fivefors", "fourplus", "fifty_plus") and 1 < nat_rank <= 3 and not len(passed):
            tie = len(nat[nat.value == now])
            lead = nat_above.sort_values("value").iloc[-1] if len(nat_above) else None
            txt = (f"{'joint-' if tie else ''}{ordn(nat_rank)} on {team}'s all-time list with {now} {sex}{P} {many}"
                   + (f", behind {nm(lead)} ({lead.value})" if lead is not None else ""))
            out.append(fact("nation_list", txt, 8 if nat_rank == 1 else 0))
        return out


def _surname(name):
    """Cricket-style short name: 'SR Tendulkar' -> 'Tendulkar'; 'Kuldeep Yadav' stays."""
    parts = str(name).split()
    if len(parts) > 1 and parts[0].isupper() and len(parts[0]) <= 4 and parts[0].isalpha():
        return " ".join(parts[1:])
    return str(name)


def _marks(stat):
    if stat == "runs":
        return list(range(1000, 20001, 1000))
    if stat == "wkts":
        return list(range(100, 801, 50))
    if stat in ("hundreds", "fivefors"):
        return [10, 20, 25, 30, 40, 50]
    if stat == "fourplus":
        return [10, 15, 20, 25, 30]
    if stat == "sixes":
        return [50, 100, 150, 200, 250, 300, 350]
    return [25, 50, 75, 100, 125]


def ordn(n):
    if n in (1, 2, 3):
        return {1: "1st", 2: "2nd", 3: "3rd"}[n]
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
