# Cricket alerts

Personal bot that sends cricket stat alerts to Telegram, ready to post on X.

| Job | When | What it sends |
|---|---|---|
| **Daily records** (`daily.yml`) | 6:40am and 6:40pm Melbourne | Downloads new matches from Cricsheet, then flags records (top-5 all-time in a competition: scores, figures, fastest 50/100, team totals, chases), rare events (hat-tricks, six sixes, T20I hundreds, ODI doubles, T20 six-fors) and career milestones |
| **Live milestones** (`live.yml`) | Every hour | Checks today's matches on the ESPN/Cricinfo feed. Sends a **watch list** of players close to a milestone before each match, and a **live alert** when one is reached |
| **Test** (`test.yml`) | Manual | Sends a test message |

Each alert has a draft caption, a **Post on X** button (opens X with the text filled in),
and links to the player and scorecard on Cricinfo to check the numbers first.

## What counts as post-worthy (ranked with TypeSafe, 27 Sep 2026)
Scope: international matches between ICC full members (Tests, ODIs, T20Is, men and women).

Recency framing wins: "First West Indian to score an ODI hundred in India since December 2019"
scored 83% against 1% for "only the 6th West Indian ... since 2006". For every hundred, wicket
haul and result the bot looks for the most striking "first since <date>" at these levels:
in that country · against that opponent · at home · for the nation · for the player.

| Tier | Angles | Sent as |
|---|---|---|
| Lead | "first since" (country / opponent / home / nation) · first win in a country since · first home defeat since · winning run ended · star's longest run without a fifty · "no fifty since <date>: N innings" · star duck · passes a big name on the all-time list · star milestone | Own alert with draft post |
| Support | best by the nation vs opponent · maiden hundred/five-for · star's most expensive spell · score sequences · career-best | Added as "Also…" lines |
| Quiet | career counts, venue records, highest totals, small milestones | End-of-match notes |

Weights: `alerts/nuggets.py` (`WEIGHT`, `LEAD_MIN`, `MIN_GAP_DAYS`).

## Setup
1. Repo secret `TELEGRAM_BOT_TOKEN` (from @BotFather). Press **Start** in your bot once.
2. Actions → *Send test message* → Run workflow. The chat ID is found automatically and saved in `state/chat_id.txt`.
3. Actions → *Daily records* → Run workflow for the first data build (about 10 minutes).

## Tuning
Edit `alerts/config.py`: which leagues count, top-N for records, milestone steps,
how close a player must be to make the watch list.

## Accuracy
Career totals come from Cricsheet ball-by-ball data (men from ~2004, women from ~2009, some
matches missing). Alerts marked ⚠️ involve careers that began before full coverage: check the
official figure before posting. Records are worded as "in <competition> (since <year>)".

## Data sources
- Cricsheet (cricsheet.org), Open Data Commons Attribution License.
- ESPN's public cricket feed (behind Cricinfo scorecards): light, low-volume use only.
- Player names: cricketdata R package.
