"""All-time career totals from Cricinfo Statsguru, for legend comparisons ("only Tendulkar has more").

Cricsheet ball-by-ball starts around 2004, so careers of earlier players are incomplete there.
This snapshot gives full career aggregates for everyone above a qualifying mark. Active players'
current totals = snapshot + innings played after the snapshot date (see legends.py).

Writes state/alltime_batting.csv and state/alltime_bowling.csv. Run weekly.
"""
import html
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
URL = ("https://stats.espncricinfo.com/ci/engine/stats/index.html?class={cls};filter=advanced;"
       "orderby={order};qualmin1={q};qualval1={order};size=200;template=results;type={typ};page={page}")
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/128.0 Safari/537.36", "Accept-Language": "en-GB,en;q=0.9"}
CLASSES = {1: ("Test", "male"), 2: ("ODI", "male"), 3: ("T20I", "male"),
           8: ("Test", "female"), 9: ("ODI", "female"), 10: ("T20I", "female")}
QUAL = {  # (runs, wickets) needed to be listed
    1: (1000, 50), 2: (1000, 40), 3: (500, 25), 8: (300, 15), 9: (500, 25), 10: (500, 25)}
ROW = re.compile(r'<tr class="data1">(.*?)</tr>', re.S)
CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
HDR = re.compile(r'<tr class="headlinks">(.*?)</tr>', re.S)
PID = re.compile(r"/player/(\d+)\.html")
TAG = re.compile(r"<[^>]+>")


def _text(s):
    return html.unescape(TAG.sub("", s)).strip()


def fetch(cls, typ):
    order = "runs" if typ == "batting" else "wickets"
    q = QUAL[cls][0 if typ == "batting" else 1]
    rows, header, page = [], None, 1
    while True:
        r = requests.get(URL.format(cls=cls, order=order, q=q, typ=typ, page=page), headers=HEAD, timeout=60)
        r.raise_for_status()
        body = r.text
        if header is None:
            h = HDR.search(body)
            header = [_text(c) for c in CELL.findall(h.group(1))] if h else None
        found = ROW.findall(body)
        for tr in found:
            cells = CELL.findall(tr)
            m = PID.search(cells[0])
            name = _text(cells[0])
            cc = re.search(r"\(([^)]*)\)\s*$", name)
            rows.append([m.group(1) if m else None, re.sub(r"\s*\([^)]*\)\s*$", "", name),
                         cc.group(1) if cc else ""] + [_text(c) for c in cells[1:]])
        pages = re.search(r"Page\s*<b>?\s*(\d+)\s*</b>?\s*of\s*<b>?\s*(\d+)", body) or re.search(r"Page (\d+) of (\d+)", _text(body))
        if not found or not pages or int(pages.group(1)) >= int(pages.group(2)):
            break
        page += 1
        time.sleep(2)
    cols = ["cricinfo_id", "name", "country"] + [c or f"c{i}" for i, c in enumerate(header[1:])]
    df = pd.DataFrame([r[:len(cols)] for r in rows], columns=cols[:max(len(r) for r in rows)])
    fmt, gender = CLASSES[cls]
    df.insert(0, "format", fmt)
    df.insert(1, "gender", gender)
    return df


def main():
    STATE.mkdir(exist_ok=True)
    for typ in ("batting", "bowling"):
        parts = []
        for cls in CLASSES:
            try:
                d = fetch(cls, typ)
                print(typ, CLASSES[cls], len(d), "rows")
                parts.append(d)
            except Exception as e:  # noqa: BLE001
                print("statsguru failed", typ, cls, e)
            time.sleep(2)
        if parts:
            out = pd.concat(parts, ignore_index=True)
            out = out.loc[:, [c for c in out.columns if not re.fullmatch(r"c\d+", c)]]
            out["snapshot"] = date.today().isoformat()
            out.to_csv(STATE / f"alltime_{typ}.csv", index=False)


if __name__ == "__main__":
    main()
