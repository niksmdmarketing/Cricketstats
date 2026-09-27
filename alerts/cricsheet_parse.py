"""Parse Cricsheet JSON match files into match and delivery rows.
(Same parser as C:\\Cricket Data\\build_database.py.)"""
def phase_for(match_type, over_no, balls_per_over, innings_no):
    """Phase label from the 0-based over number."""
    if match_type in ("Test", "MDM"):
        return None
    o = over_no + 1  # 1-based over
    if balls_per_over == 5:  # The Hundred: 20 sets of 5
        return "powerplay" if o <= 5 else ("death" if o >= 17 else "middle")
    if match_type in ("ODI", "ODM"):
        return "powerplay" if o <= 10 else ("death" if o >= 41 else "middle")
    # T20, IT20 and anything short
    return "powerplay" if o <= 6 else ("death" if o >= 16 else "middle")


def parse_match(match_id, d):
    info = d["info"]
    reg = info.get("registry", {}).get("people", {})
    mt = info.get("match_type")
    bpo = info.get("balls_per_over", 6)
    teams = info.get("teams", [])
    dates = info.get("dates", [])
    outcome = info.get("outcome", {})
    event = info.get("event", {}) or {}
    toss = info.get("toss", {}) or {}
    by = outcome.get("by", {}) or {}
    match = {
        "match_id": match_id,
        "match_type": mt,
        "gender": info.get("gender"),
        "team_type": info.get("team_type"),
        "event": event.get("name"),
        "event_stage": event.get("stage"),
        "match_number": event.get("match_number"),
        "season": str(info.get("season")),
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "venue": info.get("venue"),
        "city": info.get("city"),
        "team1": teams[0] if teams else None,
        "team2": teams[1] if len(teams) > 1 else None,
        "toss_winner": toss.get("winner"),
        "toss_decision": toss.get("decision"),
        "winner": outcome.get("winner"),
        "result": outcome.get("result") or ("win" if outcome.get("winner") else None),
        "win_by_runs": by.get("runs"),
        "win_by_wickets": by.get("wickets"),
        "win_by_innings": by.get("innings"),
        "method": outcome.get("method"),
        "eliminator": outcome.get("eliminator"),
        "player_of_match": "; ".join(info.get("player_of_match", []) or []),
        "overs_scheduled": info.get("overs"),
        "balls_per_over": bpo,
        "umpires": "; ".join((info.get("officials", {}) or {}).get("umpires", []) or []),
        "data_version": d.get("meta", {}).get("data_version"),
    }

    rows = []
    for inn_no, inn in enumerate(d.get("innings", []), start=1):
        bat = inn.get("team")
        bowl = next((t for t in teams if t != bat), None)
        super_over = bool(inn.get("super_over"))
        target = inn.get("target") or {}
        t_runs = target.get("runs")
        t_overs = target.get("overs")
        t_balls = None
        if t_overs is not None:
            whole = int(t_overs)
            part = round((t_overs - whole) * 10)
            t_balls = whole * bpo + part
        score = wkts = legal = 0
        bat_runs, bat_balls = {}, {}
        seq = 0
        for ov in inn.get("overs", []):
            o = ov["over"]
            legal_in_over = 0
            for dl in ov.get("deliveries", []):
                seq += 1
                r = dl.get("runs", {})
                ex = dl.get("extras", {}) or {}
                wides = ex.get("wides", 0)
                nb = ex.get("noballs", 0)
                is_legal = not wides and not nb
                if is_legal:
                    legal += 1
                    legal_in_over += 1
                batter = dl.get("batter")
                rb = r.get("batter", 0)
                score += r.get("total", 0)
                wk = dl.get("wickets", []) or []
                kinds = [w.get("kind") for w in wk]
                bowler_w = any(k not in ("run out", "retired hurt", "retired out",
                                         "retired not out", "obstructing the field")
                               for k in kinds)
                wkts += sum(1 for k in kinds if k not in ("retired hurt", "retired not out"))
                bat_runs[batter] = bat_runs.get(batter, 0) + rb
                if not wides:
                    bat_balls[batter] = bat_balls.get(batter, 0) + 1
                req = balls_left = None
                if t_runs is not None and not super_over:
                    req = t_runs - score
                    if t_balls is not None:
                        balls_left = t_balls - legal
                rows.append({
                    "match_id": match_id,
                    "innings": inn_no,
                    "super_over": super_over,
                    "batting_team": bat,
                    "bowling_team": bowl,
                    "over": o + 1,
                    # legal ball number in the over; a wide/no-ball shares the next number
                    "ball_in_over": legal_in_over if is_legal else legal_in_over + 1,
                    "delivery_seq": seq,
                    "legal_balls_bowled": legal,
                    "batter": batter,
                    "bowler": dl.get("bowler"),
                    "non_striker": dl.get("non_striker"),
                    "batter_id": reg.get(batter),
                    "bowler_id": reg.get(dl.get("bowler")),
                    "runs_batter": rb,
                    "runs_extras": r.get("extras", 0),
                    "runs_total": r.get("total", 0),
                    "non_boundary": bool(r.get("non_boundary")),
                    "wides": wides,
                    "noballs": nb,
                    "byes": ex.get("byes", 0),
                    "legbyes": ex.get("legbyes", 0),
                    "penalty": ex.get("penalty", 0),
                    "is_legal": is_legal,
                    "is_dot": r.get("total", 0) == 0 and is_legal,
                    "is_four": rb == 4 and not r.get("non_boundary"),
                    "is_six": rb == 6 and not r.get("non_boundary"),
                    "is_wicket": bool(wk),
                    "wicket_kind": "; ".join(k for k in kinds if k) or None,
                    "player_out": "; ".join(w.get("player_out", "") for w in wk) or None,
                    "fielders": "; ".join(f.get("name", "") for w in wk
                                          for f in (w.get("fielders") or [])) or None,
                    "bowler_wicket": bowler_w,
                    "team_score": score,
                    "team_wickets": wkts,
                    "batter_runs_so_far": bat_runs[batter],
                    "batter_balls_so_far": bat_balls.get(batter, 0),
                    "phase": phase_for(mt, o, bpo, inn_no),
                    "target_runs": t_runs,
                    "runs_required": req,
                    "balls_remaining": balls_left,
                    "current_run_rate": round(score * bpo / legal, 3) if legal else None,
                    "required_run_rate": round(req * bpo / balls_left, 3)
                    if req is not None and balls_left and balls_left > 0 else None,
                })
    # make ball_in_over simple: legal ball count within the over, extras share the next number
    return match, rows


