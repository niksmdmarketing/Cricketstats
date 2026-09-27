"""Download Cricsheet and keep a local, incrementally updated copy of every match.

data/matches.parquet            one row per match (+ competition label)
data/deliveries/part-*.parquet  one row per ball
Returns the list of match_ids that are new in this run.
"""
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import requests

from .cricsheet_parse import parse_match
from .labels import label_competition

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ZIP_URL = "https://cricsheet.org/downloads/all_json.zip"
RECENT_URL = "https://cricsheet.org/downloads/recently_added_7_json.zip"
PEOPLE_URL = "https://cricsheet.org/register/people.csv"
UA = {"User-Agent": "cricket-alerts personal bot (github.com/niksmdmarketing)"}


def _download(url):
    r = requests.get(url, headers=UA, timeout=300)
    r.raise_for_status()
    return r.content


def update(full=False):
    DATA.mkdir(exist_ok=True)
    (DATA / "deliveries").mkdir(exist_ok=True)
    mpath = DATA / "matches.parquet"
    have = set(pd.read_parquet(mpath, columns=["match_id"]).match_id) if mpath.exists() and not full else set()
    url = ZIP_URL if (full or not have) else RECENT_URL
    print("Downloading", url)
    z = zipfile.ZipFile(io.BytesIO(_download(url)))
    names = [n for n in z.namelist() if n.endswith(".json")]
    new_ids = [Path(n).stem for n in names if Path(n).stem not in have]
    print(f"{len(names)} files in zip, {len(new_ids)} new")
    if not new_ids:
        return []
    matches, rows = [], []
    part = len(list((DATA / "deliveries").glob("part-*.parquet")))

    def flush():
        nonlocal rows, part
        if not rows:
            return
        ddf = pd.DataFrame(rows)
        for c in ("target_runs", "runs_required", "balls_remaining"):
            ddf[c] = pd.to_numeric(ddf[c], errors="coerce").astype("float64")
        ddf.to_parquet(DATA / "deliveries" / f"part-{part:04d}.parquet", index=False)
        part += 1
        rows = []

    for i, mid in enumerate(new_ids, 1):
        try:
            m, r = parse_match(mid, json.loads(z.read(f"{mid}.json")))
        except Exception as e:  # noqa: BLE001
            print("skip", mid, e)
            continue
        matches.append(m)
        rows.extend(r)
        if i % 1500 == 0:
            flush()
    flush()
    mdf = pd.DataFrame(matches)
    mdf["start_date"] = pd.to_datetime(mdf["start_date"])
    mdf["year"] = mdf["start_date"].dt.year
    mdf["competition"] = mdf.apply(label_competition, axis=1)
    for c in ("win_by_runs", "win_by_wickets", "win_by_innings", "match_number", "overs_scheduled"):
        mdf[c] = pd.to_numeric(mdf[c], errors="coerce")
    for c in ("season", "event_stage", "method", "eliminator"):
        mdf[c] = mdf[c].astype("string")
    if have:
        old = pd.read_parquet(mpath)
        mdf = pd.concat([old, mdf], ignore_index=True)
    mdf.drop_duplicates("match_id", keep="last").to_parquet(mpath, index=False)
    # people register (IDs incl. Cricinfo)
    try:
        (DATA / "people.csv").write_bytes(_download(PEOPLE_URL))
    except Exception as e:  # noqa: BLE001
        print("people.csv download failed:", e)
    return [m["match_id"] for m in matches]


if __name__ == "__main__":
    import sys
    print(len(update(full="--full" in sys.argv)), "new matches")
