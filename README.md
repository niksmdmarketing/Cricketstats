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

## Stat angles (how posts are framed)
`alerts/angles.py` looks at every big (or bad) performance from 14 angle families, modelled on what the
biggest cricket stats accounts post:

| Family | Example |
|---|---|
| form | "his last 5 Test innings: 68, 47, 23, 58*, 88 — 284 runs at 71.0"; series totals; streaks |
| legend | "moves past Kallis into 3rd on the all-time list", "only Tendulkar has more" (weekly Statsguru snapshot) |
| company | "joins A (2012) and B (2019) as the only players to...", "the only other time was..." |
| drought | "the first West Indian to score an ODI hundred in India since Hope in December 2019" |
| context | city, opponent, batting position, chases, best figures vs an opponent |
| leaderboard | "now leads the 2026 T20I wickets chart", "3 of the top 4 are Pakistanis" |
| shift | "since Aug 2025 he strikes at 183, up from 148 before" |
| comeback | "his first Test hundred in 2 years, 166 days" |
| quirk | "the 5th time he's been dismissed for 71 in Tests", birthdays, 100th match |
| lone_hand | highest score in a defeat, "20 wickets and lost every match" |
| split | pace vs spin, bowling types, powerplay/death wickets |
| duel | "Siraj has now dismissed Head 9 times in internationals" |
| age | "at 35, the oldest England player to score a T20I hundred" |
| slump | big players' droughts, ducks, expensive days |

`alerts/rotation.py` picks one hook plus up to two support lines from different families, and penalises a
family that led any of the last 3 posts, was used more than twice in 24 hours, or was used for the same
player in the last 3 weeks. Exact lines are never repeated. Every alert also lists the other angles found, so
you can swap in a different one before posting.

Coverage: men's and women's internationals between full members, plus the leagues in `NUGGET_LEAGUES`
(IPL, WPL, BBL, WBBL, PSL, SA20, CPL, ILT20, MLC, The Hundred). "Ever" is only claimed where our ball-by-ball
data covers the whole history (men's T20Is and the leagues); otherwise lines say "in records since 2006" (2011 for women).

Jobs: `build.yml` (refresh data only), `preview.yml` (live pass without sending; see `state/last_preview.log`),
`statsguru.yml` (weekly all-time snapshot).
