"""Readable competition labels and scopes."""

BLAST = {"NatWest T20 Blast", "Vitality Blast", "Vitality Blast Men",
         "Friends Provident t20", "Friends Life t20"}
COUNTY = {"Specsavers County Championship", "LV= County Championship", "County Championship"}


def label_competition(m):
    """m: mapping with team_type, match_type, gender, event."""
    tt, mt, g, ev = m.get("team_type"), m.get("match_type"), m.get("gender"), m.get("event")
    sex = "Men's" if g == "male" else "Women's"
    if tt == "international":
        if mt in ("T20", "IT20"):
            return f"{sex} T20I"
        if mt == "ODI":
            return f"{sex} ODI"
        if mt == "Test":
            return f"{sex} Test"
    if ev in BLAST:
        return "T20 Blast"
    if ev in COUNTY:
        return "County Championship"
    return ev or f"{mt} (other)"


def fmt_of(match_type):
    if match_type in ("Test", "MDM"):
        return "Test"
    if match_type in ("ODI", "ODM"):
        return "ODI"
    return "T20"


def short(comp):
    return {
        "Indian Premier League": "IPL", "Big Bash League": "BBL",
        "Women's Big Bash League": "WBBL", "Pakistan Super League": "PSL",
        "Caribbean Premier League": "CPL", "International League T20": "ILT20",
        "Major League Cricket": "MLC", "Women's Premier League": "WPL",
        "Bangladesh Premier League": "BPL", "Lanka Premier League": "LPL",
        "The Hundred Men's Competition": "The Hundred (men)",
        "The Hundred Women's Competition": "The Hundred (women)",
    }.get(comp, comp)


def hashtag(comp):
    tags = {"Indian Premier League": "#IPL", "Big Bash League": "#BBL",
            "Women's Big Bash League": "#WBBL", "Pakistan Super League": "#PSL",
            "Caribbean Premier League": "#CPL", "SA20": "#SA20",
            "The Hundred Men's Competition": "#TheHundred",
            "The Hundred Women's Competition": "#TheHundred",
            "Women's Premier League": "#WPL", "T20 Blast": "#VitalityBlast",
            "Men's Test": "#Tests", "Men's ODI": "#ODIs", "Men's T20I": "#T20Is"}
    return tags.get(comp, "#Cricket")
