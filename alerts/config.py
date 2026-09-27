"""Settings for the cricket alert bot. Edit these to tune what gets flagged."""

# Full-member nations (men's and women's). Records/rare events in internationals
# are only flagged when at least one side is a full member, to cut associate noise.
FULL_MEMBERS = {
    "Afghanistan", "Australia", "Bangladesh", "England", "India", "Ireland",
    "New Zealand", "Pakistan", "South Africa", "Sri Lanka", "West Indies", "Zimbabwe",
}

# Competitions (labels from label_competition) whose records are worth a post.
MAJOR_LEAGUES = {
    "Indian Premier League", "Big Bash League", "Women's Big Bash League",
    "Pakistan Super League", "Caribbean Premier League", "SA20",
    "International League T20", "Major League Cricket",
    "The Hundred Men's Competition", "The Hundred Women's Competition",
    "Women's Premier League", "T20 Blast", "Bangladesh Premier League",
    "Lanka Premier League", "Super Smash", "Sheffield Shield",
    "County Championship",
}

# A competition needs this much history before "top-N all-time" claims are made.
MIN_MATCHES_FOR_RECORDS = 60
TOP_N = 5                      # "3rd-highest ever" etc. - flag ranks 1..TOP_N

# Career milestone steps per scope type
MILESTONES = {
    # scope kind: (runs step, wickets step)
    "international": (1000, 50),
    "t20_all": (1000, 50),
    "league": (500, 25),
}
# Smallest milestone worth flagging per scope type: (runs, wickets)
MIN_MILESTONE = {
    "international": (1000, 50),
    "t20_all": (5000, 200),
    "league": (1000, 50),
}
# League career milestones only for these (T20 franchise leagues)
T20_LEAGUES = {
    "Indian Premier League", "Big Bash League", "Women's Big Bash League",
    "Pakistan Super League", "Caribbean Premier League", "SA20",
    "International League T20", "Major League Cricket",
    "The Hundred Men's Competition", "The Hundred Women's Competition",
    "Women's Premier League", "T20 Blast", "Bangladesh Premier League",
    "Lanka Premier League", "Super Smash",
}

# Watch list: how close a player must be to a milestone to be listed
WATCH_RUNS = {"Test": 250, "ODI": 150, "T20": 100}
WATCH_WKTS = {"Test": 6, "ODI": 4, "T20": 3}
ACTIVE_DAYS = 450              # player counts as "active" if they played within this window

# CricketData.org API budget guard (free plan). Stop calling above this share of the daily limit.
API_BUDGET_SHARE = 0.85
MAX_PLAYER_LOOKUPS_PER_DAY = 15

# Cricsheet coverage: careers that started before this year may be undercounted.
COVERAGE_START = {"male": 2006, "female": 2011}
