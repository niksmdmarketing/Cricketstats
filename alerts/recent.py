"""Innings from ESPN scorecards of finished matches, so history is current while Cricsheet catches up
(Cricsheet usually lags real matches by a week or more)."""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
FILES = {"hist_bat": "recent_bat.parquet", "hist_bowl": "recent_bowl.parquet", "hist_team": "recent_team.parquet"}
KEEP_DAYS = 60


def store(rows_bat, rows_bowl, rows_team):
    for key, rows in (("hist_bat", rows_bat), ("hist_bowl", rows_bowl), ("hist_team", rows_team)):
        if not rows:
            continue
        f = STATE / FILES[key]
        new = pd.DataFrame(rows)
        old = pd.read_parquet(f) if f.exists() else None
        df = new if old is None else pd.concat([old[~old.match_id.isin(set(new.match_id))], new], ignore_index=True)
        df["date"] = pd.to_datetime(df["date"])
        df = df[df.date >= pd.Timestamp.now() - pd.Timedelta(days=KEEP_DAYS)]
        df.to_parquet(f, index=False)


def merge(hist):
    """Add recent ESPN innings for matches Cricsheet doesn't have yet (same date, teams and format)."""
    out = dict(hist)
    for key, fname in FILES.items():
        f = STATE / fname
        base = hist.get(key)
        if base is None or not f.exists():
            continue
        rec = pd.read_parquet(f)
        rec["date"] = pd.to_datetime(rec["date"])
        b = base.assign(_d=pd.to_datetime(base.date))
        have = set(zip(b._d.dt.normalize(), b.team, b.opp, b.format))
        have |= {(d - pd.Timedelta(days=1), t, o, fm) for d, t, o, fm in have}
        keep = rec[[(d.normalize(), t, o, fm) not in have for d, t, o, fm in zip(rec.date, rec.team, rec.opp, rec.format)]]
        if len(keep):
            out[key] = pd.concat([base, keep[[c for c in keep.columns if c in base.columns]]], ignore_index=True)
    return out
