"""Pick what to post: one hook plus up to two supporting lines, from different angle families,
with penalties so no family (or the same player-family pair) keeps coming back."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "state" / "angle_log.json"
SUPPORT_MIN = 75


def _now():
    return datetime.now(timezone.utc)


def load():
    try:
        return json.loads(LOG.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save(log):
    cut = (_now() - timedelta(days=90)).isoformat()
    log = [e for e in log if e["ts"] >= cut]
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text(json.dumps(log, indent=0))


def _h(text):
    return hashlib.md5(text.lower().encode()).hexdigest()[:10]


def adjust(facts, log, pid=None, now=None):
    """Score after freshness penalties. Returns [(adj, raw, family, text)] best first, repeats removed."""
    now = now or _now()
    day = (now - timedelta(days=1)).isoformat()
    three_weeks = (now - timedelta(days=21)).isoformat()
    leads = [e for e in log if e["family"] != "_support"]
    recent_leads = [e["family"] for e in leads[-3:]]
    lead_24h = {}
    for e in leads:
        if e["ts"] >= day:
            lead_24h[e["family"]] = lead_24h.get(e["family"], 0) + 1
    used_texts = {e["hash"] for e in log}
    seen, out = set(), []
    for s, fam, text in facts:
        k = text.lower()
        if k in seen or _h(text) in used_texts:
            continue
        seen.add(k)
        adj = s - 12 * recent_leads.count(fam) - 8 * max(0, lead_24h.get(fam, 0) - 2)
        if pid and any(e.get("pid") == pid and e["family"] == fam and e["ts"] >= three_weeks for e in log):
            adj -= 20
        out.append((adj, s, fam, text))
    return sorted(out, key=lambda x: -x[0])


def pick(facts, log, pid=None, now=None):
    """-> (lead, supports) using family diversity."""
    ranked = adjust(facts, log, pid, now)
    if not ranked:
        return None, []
    lead = ranked[0]
    sup, fams = [], {lead[2]}
    for f in ranked[1:]:
        if f[0] >= SUPPORT_MIN and f[2] not in fams:
            sup.append(f)
            fams.add(f[2])
        if len(sup) == 2:
            break
    return lead, sup


def caption(subject, lead, sup, tag, style_seed=""):
    """A few layouts so posts don't all look the same."""
    hook = lead[3]
    rest = [f[3] for f in sup]
    style = int(hashlib.md5((style_seed or hook).encode()).hexdigest(), 16) % 2
    cap = lambda s: s[0].upper() + s[1:] if s else s  # noqa: E731
    if lead[2] == "form" or (style == 1 and rest):
        body = f"{subject}\n\n{cap(hook)}." + ("".join(f"\n• {cap(r)}" for r in rest) if rest else "")
    else:
        body = f"{subject}: {hook}" if subject else cap(hook)
        if len(rest) == 1:
            body += f". Also {rest[0]}"
        elif len(rest) == 2:
            body += f". Also {rest[0]}, and {rest[1]}"
        body += "."
    return f"{body}\n\n{tag}"


def record(log, lead, pid=None, now=None, supports=()):
    ts = (now or _now()).isoformat()
    log.append({"ts": ts, "family": lead[2], "pid": pid, "hash": _h(lead[3])})
    for s in supports:   # remembered so the same line never repeats, but not counted as a lead
        log.append({"ts": ts, "family": "_support", "pid": pid, "hash": _h(s[3])})
