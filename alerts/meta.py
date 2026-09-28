"""Player metadata (date of birth, bowling type, batting hand, role) from the cricketdata R package."""
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = [ROOT / "state" / "meta.csv", ROOT / "data" / "player_meta.csv"]


def bowl_type(style):
    """-> (group, detail): group 'pace'|'spin'|None, detail e.g. 'left-arm spin'."""
    s = style.lower().split(",")[0] if isinstance(style, str) else ""
    if not s:
        return None, None
    left = "left" in s
    if "wrist" in s or ("left" in s and "chinaman" in s):
        return "spin", "left-arm wrist-spin"
    if "orthodox" in s or ("slow" in s and left):
        return "spin", "left-arm spin"
    if "leg" in s:
        return "spin", "leg-spin"
    if "off" in s:
        return "spin", "off-spin"
    if "slow" in s and "medium" not in s:
        return "spin", "spin"
    if re.search(r"fast|medium|bowler", s):
        return "pace", ("left-arm pace" if left else "right-arm pace")
    return None, None


def load():
    for f in CANDIDATES:
        if f.exists():
            m = pd.read_csv(f, dtype={"cricsheet_id": str, "cricinfo_id": str})
            break
    else:
        return pd.DataFrame(columns=["player_id", "dob", "btype", "bdetail", "hand", "role", "country", "name"])
    m = m.dropna(subset=["cricsheet_id"]).drop_duplicates("cricsheet_id")
    dob = pd.to_numeric(m.get("dob"), errors="coerce")
    out = pd.DataFrame({
        "player_id": m.cricsheet_id,
        "name": m.get("name"),
        # R stores dates as days since 1970-01-01
        "dob": pd.to_datetime(dob, unit="D", origin="unix", errors="coerce") if dob.notna().any() else pd.NaT,
        "hand": m.get("batting_style").fillna("").str.contains("Left").map({True: "left", False: "right"}),
        "role": m.get("playing_role"),
        "country": m.get("country"),
    })
    t = m.get("bowling_style").map(bowl_type)
    out["btype"] = [x[0] for x in t]
    out["bdetail"] = [x[1] for x in t]
    return out.reset_index(drop=True)
