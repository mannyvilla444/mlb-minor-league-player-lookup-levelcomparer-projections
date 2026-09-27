# MLB Level Stats

Type a hitter's name in your browser; get PA, OPS, SLG, BB% and K% for MLB and
every affiliated minor-league level, on one screen.

This is step one of a larger project on how a bat's numbers change as it climbs
levels (especially the first ~900 MLB PA). **It does not translate anything** —
it is a reliable data pull plus a readable display, which is what the model will
sit on top of later.

Data comes only from the official [MLB Stats API](https://statsapi.mlb.com) via
the [`MLB-StatsAPI`](https://pypi.org/project/MLB-StatsAPI/) package. Nothing is
scraped. No API key, no account, no secrets.

---

## Start it by double-clicking

**Windows.** Open the project folder and double-click:

```
Start Level Stats.bat
```

That is the whole procedure. On the first double-click it finds your Python,
builds a private `.venv`, installs the dependencies and starts the app — a
minute or two. Every double-click after that goes straight to starting it, which
takes a couple of seconds. Your browser opens on its own once the server is
actually answering.

A console window stays open while the app runs. **Leave it open** — that window
*is* the server. Close it (or press Ctrl-C in it) to stop the app.

Want it on your desktop? Double-click **`Create Desktop Shortcut.bat`** once.
That puts a "Level Stats" shortcut with the app icon on your desktop, and from
then on that is the only thing you need to click.

**macOS / Linux.** Double-click `start-level-stats.command` (on macOS the first
time, right-click -> Open, to get past the unidentified-developer warning). If
double-clicking does nothing, the executable bit was lost in transit:

```bash
chmod +x start-level-stats.command
```

### What the launcher handles for you

| Situation | What happens |
| --- | --- |
| No Python environment yet | Builds `.venv` and installs everything, then starts |
| A dependency was added in a newer version of the project | Detected by an import probe; installs the difference automatically |
| Port 8000 already in use | Moves to the next free port and says so — no cryptic bind error |
| An older copy of the app still running | Same thing: new port, with a note pointing at the old window |
| Zip extracted one level too deep | Refuses to run and tells you to look for the nested folder |
| No usable Python installed | Says exactly that, with the download link and the version range |

Every failure path keeps the window open with the reason, so a double-click can
never just flash and vanish.

It always runs from its own folder, so it does not matter where you extracted
the project or what directory anything else is in.

---

## Or start it from a terminal

Requires Python 3.11-3.13 (pandas 2.2.3 has no wheel for 3.14 yet).

**Windows (PowerShell)**

```powershell
cd mlb-level-stats
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

**macOS / Linux**

```bash
cd mlb-level-stats
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Then open <http://127.0.0.1:8000> (`run.py` opens it for you).

Useful flags: `--port 9000`, `--no-browser`, `--reload` (auto-restart on code
changes, for development), `--strict-port` (fail instead of moving to the next
free port).

Equivalent, if you prefer uvicorn directly:

```bash
uvicorn app.main:app --reload --port 8000
```

### Example search

Type **`Elly De La Cruz`** and hit *Look up*. You should land on
`/player/682829` and see MLB rows alongside AAA / AA / A+ / Rookie rows, each
with PA, OPS, SLG, BB% and K%, plus a career-by-level summary underneath.

Other things worth trying:

| Search | What it shows off |
| --- | --- |
| `Will Smith` | the disambiguation page — several players, pick one |
| `Bobby Witt Jr.` | a fast climb through the minors |
| `zzzz` | the empty state (“No match. Try last name, first name.”) |
| `682829` | a bare MLBAM id goes straight to the player page |

### Which build am I running?

Every page shows the version in the masthead (top right) and the footer, and
`run.py` prints it plus the folder it is serving from on startup. If the version
is not what you expect, you are running an older copy from a different folder —
the usual cause is a nested `mlb-level-stats\mlb-level-stats` from Windows
"Extract All", or a server process still up from before.

```powershell
# what the running server actually is
Invoke-RestMethod http://127.0.0.1:8000/healthz
```

Returns the version, the absolute folder it loaded from, and whether the Compare
tab is present. Stop the old server with Ctrl-C in its terminal before starting
a new one; a stale process holding port 8000 will keep serving old pages.

---

## The Compare tab

`/compare` takes a list of hitters and differences two levels across the whole
group — the aggregate view the translation model will eventually be fit on.

Paste names one per line, pick a level pair, hit **Compare**. For each player it
pulls the career line at both levels and subtracts **higher minus lower**:

| Column | Meaning |
| --- | --- |
| Δ OPS, Δ SLG | difference in points, e.g. `-.109` |
| Δ BB%, Δ K% | difference in **percentage points**, e.g. `-1.3` |

A negative Δ OPS on MLB − AAA is the expected direction: the bat got worse
against better pitching. Green means the player was *better* at the higher
level, red worse — and because a lower strikeout rate is the good outcome, the
colours flip on the K% column.

Under the table: **mean**, **median**, and **n** for each of the four
differentials. `n` is per column, since a player can have a usable Δ OPS but no
Δ BB% if the API omitted walks on one side.

### Spread, precision and the box-plot summary

Under the mean and median, the footer reports for each of the four differentials:

| Row | What it is | Why both |
| --- | --- | --- |
| **SD** | sample standard deviation (`ddof=1`) of the per-player deltas | how spread out *the players* are |
| **SE of mean** | `SD / sqrt(n)` | how precisely *the average itself* is pinned down |
| **SE of median** | bootstrap: 2,000 resamples, SD of their medians | same idea for the median, without assuming normality |
| **Q1 / Q3** | 25th and 75th percentiles | the middle half of the players |
| **IQR** | `Q3 - Q1` | spread that ignores the tails |

SD and SE answer different questions and are easy to confuse. A large SD with a
small SE means a genuinely varied population whose average is nonetheless well
estimated — exactly the situation you want to know about before fitting anything
to these numbers. The median's standard error has no clean closed form unless you
assume a normal distribution, so it is **bootstrapped** instead; the generator is
seeded, so reloading the page never changes the figure.

Quartiles use **linear interpolation** (numpy's default, type 7), which matches
Excel's `QUARTILE.INC` and R's `type=7`, so a spot-check in a spreadsheet agrees.

### The box plots

Below the table, one box-and-whisker panel per metric, server-rendered as inline
SVG — no JavaScript, no charting library.

Each panel has **its own x-axis**, on purpose: OPS and SLG differences are in
points and BB%/K% in percentage points, so a shared axis would imply a comparison
that does not exist. Small multiples are the honest form.

Per panel: the outlined box spans Q1 to Q3; the heavy line is the **median**; the
rust diamond is the **mean**; whiskers reach the most extreme player still within
1.5 x IQR; anything past that is a hollow **outlier** dot — flagged for the eye but
**still counted** in the mean, median and SD. A faint dot below the box marks each
individual player, so a six-player sample cannot masquerade as a smooth
distribution. The grey hairline is **zero** — no difference between the levels —
which is the reference the whole question is asked against.

Hovering any mark reads out its exact value. Nothing is hidden behind the hover:
the same numbers are in the table above and in the readout line under each panel.

### Level pairs

`MLB vs AAA` (default), `MLB vs AA`, `MLB vs A+`, `AAA vs AA`, `AA vs A+`.
Add more in `LEVEL_PAIRS` in `app/config.py` — a pair is just two sportIds.

### The PA filter

The checkbox excludes players below a PA threshold (default 120) **at the lower
level only**. That is the deliberate choice: a player with 900 MLB PA next to 40
Triple-A PA has a differential worth nothing, and the small side is always the
binding constraint. Change the number in the box, or set `MLB_MIN_PA`.

Excluded players stay visible in the table, greyed out, with the reason shown
(`40 AAA PA < 120`). Nothing vanishes silently. Players missing one of the two
levels entirely are also shown, marked, and left out of the summary.

### Ambiguous names

`Will Smith` matches several players, so it is **never guessed at**. It lands in
an "Unresolved" panel with the candidates and their MLBAM ids; paste the id you
meant back into the list. Ids and names can be mixed freely in the same list.

Input parsing splits on **newlines only** — never commas — so `Smith, Will` is
one name rather than two bogus lookups. Bullets and `1.` numbering are stripped.

### Request volume

A run of *N* players costs *N* name lookups plus *N × 2* stat requests, all
flowing through the same 3-at-a-time cap and the same 12-hour cache. A second
run over the same players, or a toggle of the checkbox, is served entirely from
cache. `MLB_MAX_BULK_PLAYERS` (default 60) caps one submission.

---

### Run the tests

```bash
pytest
```

218 tests, all offline — every MLB API response is mocked.

---

## The Projection tab

`/projection` takes a list of hitters and turns each one's minor-league record
into a projected MLB line, using the league deltas from the Translations tab.

For each player:

1. Fetch MLB, Triple-A, Double-A and High-A.
2. Bucket every minor-league split **by league** — not by level, because the
   deltas are per league. A player who split Triple-A between the International
   and Pacific Coast leagues gets two separate lines.
3. Pick the league with the most plate appearances. That is the dominant league.
4. Take the **career** line in that league (all seasons summed, rates recomputed
   from the totals) and add that league's mean delta to each metric:

   ```
   projected MLB stat = career stat in the dominant league + that league's mean Δ
   ```

### Reading the table

Three blocks: the career line in the dominant league, the projection, and the
player's **actual** MLB line where one exists. When at least one player has real
MLB numbers, the footer scores the projections against them:

| Row | Question it answers |
| --- | --- |
| **Mean residual** (actual − projected) | Is the translation systematically high or low? |
| **Mean absolute residual** | How far off is it for any one player? |

Those are different questions. A translation can have near-zero bias and still be
wrong about everybody — the mean residual cancels, the mean absolute residual
does not.

### What it will not do

* **It will not guess a league.** A league the table does not recognise (Mexican
  League, NPB, anything below High-A) is named on the row and the player sits
  out, rather than being given a level-average substitute.
* **It will not hide a close call.** If the runner-up league is within 15% of the
  winner's PA, the row is flagged and an "Other leagues on record" panel shows
  what was chosen over what.
* **It will not print impossible rates.** A 1.0% walk rate minus 3.3 points
  clamps at zero, and the row says it clamped.
* **It will not pretend the four metrics are one slash line.** Each is projected
  independently, so the projected OPS is not rebuilt from the projected SLG. The
  source page makes the same point when it warns against subtracting both changes
  from OPS.

This is a league-average adjustment, **not a forecast**: no age, park,
competition or playing-time model is involved.

### Keeping the deltas honest

The numbers live in `app/services/translations.py` because the app computes with
them; the Translations tab is the human-readable copy. `tests/test_translations_data.py`
parses the deltas back out of that HTML and asserts they match the Python table,
so the two cannot drift apart.

League names are matched by name, not id, with aliases for the 2021 placeholder
names (`Triple-A East`, `High-A West`, …) that MiLB used for one season before
the historic names returned. Without those, a 2021 stint would silently fail to
match its league.

---

## The Translations tab

`/translations` displays `app/static/minor-league-to-mlb-stat-translations.html`
— an author-supplied reference page of mean OPS / SLG / BB% / K% changes from
eight minor leagues to MLB.

It is served **byte-for-byte**: the app does not parse, recompute or validate
anything in it, and nothing on the Compare tab reads from it. It is a document
sitting next to the tool, useful as a sanity check against your own numbers.

The file renders inside an iframe rather than being pasted into a template. That
keeps one source of truth (edit the file, the tab updates), lets it keep its own
dark styling without fighting the app's stylesheet, and still leaves the app's
navigation in place around it. A same-origin script sizes the frame to its
content; with JavaScript off it falls back to a tall fixed frame. "Open on its
own" links to the raw file.

**To swap in a different document**, drop it in `app/static/` and point
`TRANSLATIONS_FILENAME` in `app/config.py` at it. A missing file returns a 404
page naming what it expected, rather than an empty frame.

---

## Stats and how they are computed

Each row is one **season × level**.

| Column | Source |
| --- | --- |
| Season | `split.season` |
| Level | derived from the `sportId` used for the request (see map below) |
| Team | `split.team.name`; a season split across clubs at one level shows “2 teams” |
| PA | `stat.plateAppearances` |
| OPS | `stat.ops` |
| SLG | `stat.slg` |
| BB% | `stat.baseOnBalls / stat.plateAppearances`, shown to one decimal |
| K% | `stat.strikeOuts / stat.plateAppearances`, shown to one decimal |
| Cum. MLB PA | running total of MLB PA through that season (MLB rows only) |

Rules the code holds to:

* **Nothing is invented.** A field the API omits renders as `—`, and the rest of
  the row still shows.
* **Zero-PA rows are dropped** entirely — the rates would be meaningless.
* A season split between two clubs *at the same level* is folded into one row
  with counting stats summed, and OPS/SLG recomputed from the component totals
  rather than averaged.

### Career by level

The second table sums PA per level and reports the rate stats:

* **BB% and K% are always exact** — total walks (or strikeouts) over total PA.
* **OPS and SLG are recomputed from component totals** when the API supplies
  AB, TB, H, BB, HBP and SF: `SLG = TB / AB`, `OBP = (H + BB + HBP) / (AB + BB +
  HBP + SF)`, `OPS = OBP + SLG`. This is a true career rate, not an average of
  averages.
* **If those components are missing**, it falls back to a **PA-weighted average**
  of the season values. Every row labels which method produced it, so you never
  have to guess.

### The “first 900 MLB PA” marker

The results page shows a running MLB PA total per season and flags the season in
which the total first crosses 900 (configurable via `MLB_PA_MILESTONE`).

This is **season granularity only**. The `yearByYear` hydration has no game-level
detail, so the app will not pretend to split a season at exactly 900 PA. Getting
a true first-900-PA line would mean pulling game logs
(`type=[gameLog]`) per season and accumulating — a reasonable next step, but
deliberately out of scope here.

---

## sportId map

These are the values sent as `sportId` in the stats hydration. They come from
`https://statsapi.mlb.com/api/v1/sports`.

| sportId | Level | Shown as |
| --- | --- | --- |
| 1 | Major League Baseball | `MLB` |
| 11 | Triple-A | `AAA` |
| 12 | Double-A | `AA` |
| 13 | High-A (Class A Advanced) | `A+` |
| 14 | Single-A / Low-A (Class A) | `A` |
| 15 | Class A Short Season (existed through 2020) | `A-` |
| 16 | Rookie and complex leagues (ACL, FCL, DSL) | `R` |

Deliberately **excluded**: 17 (winter leagues), 21 (independent), 22 (college),
23 (high school), 51 (international). They are not part of the affiliated ladder
this project is about. Add them in `app/config.py` if you want them — one
`Level(...)` entry is all it takes.

---

## How the data is pulled

Two endpoints, both through `statsapi.get`:

1. **Name → person id.** First try `/api/v1/people/search?names=…`, which finds
   retired players too. If that endpoint is unavailable, fall back to scanning
   each level's season roster (`/api/v1/sports/players`) with the same substring
   matching `statsapi.lookup_player` uses.

   *Why not call `statsapi.lookup_player` directly?* It accepts neither a
   timeout nor a cache, and it fires an extra `latest_season` request per call.
   The client here hits the same endpoint with the same matching rule, so
   behaviour matches, but requests are cached and bounded. `app/services/mlb_client.py`
   is the only place this lives.

2. **Person id → stats.** One request per sportId:

   ```
   GET /api/v1/people?personIds=682829
       &hydrate=stats(group=[hitting],type=[yearByYear],sportId=11)
   ```

   Everything joins on the MLBAM person id.

Reliability details:

* **Timeout** on every request (12s default).
* **Retries with exponential backoff** (3 attempts, 0.75s base).
* **Bounded concurrency** — at most 3 requests in flight, never an unbounded fan-out.
* **A level that fails does not sink the page.** The other levels render and a
  banner names the sportIds that did not load.
* **Total failure is loud**, not silent: an error page with the upstream message
  and a retry button.

### Cache

Raw API JSON is cached in SQLite (`cache.sqlite3` in the project root), keyed by
`people:{person_id}:{sport_id}` for stats and `roster:{sport_id}:{season}` /
`search:{name}` for lookups. TTL is 12 hours. Delete the file to force a refresh.

### Configuration

All optional environment variables, read in `app/config.py`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MLB_TIMEOUT` | `12` | per-request timeout, seconds |
| `MLB_MAX_RETRIES` | `3` | attempts before giving up |
| `MLB_RETRY_BACKOFF` | `0.75` | base backoff, seconds (doubles each retry) |
| `MLB_MAX_CONCURRENCY` | `3` | max simultaneous API requests |
| `MLB_CACHE_TTL` | `43200` | cache lifetime, seconds (12h) |
| `MLB_CACHE_PATH` | `./cache.sqlite3` | where the cache lives |
| `MLB_PA_MILESTONE` | `900` | the MLB PA threshold flagged on the results page |
| `MLB_MIN_PA` | `120` | default lower-level PA cutoff on the Compare tab |
| `MLB_MAX_BULK_PLAYERS` | `60` | most players accepted in one Compare run |

---

## Project layout

```
mlb-level-stats/
├── app/
│   ├── main.py               FastAPI routes + Jinja formatting filters
│   ├── config.py             sportId map, level pairs, timeouts, cache settings
│   ├── services/
│   │   ├── mlb_client.py     the only code that talks to the MLB API
│   │   ├── transforms.py     JSON → season×level rows, career rollups (pandas)
│   │   ├── compare.py        level differentials, mean/median across a list
│   │   ├── projection.py     dominant league + delta -> projected MLB line
│   │   ├── translations.py   the eight league deltas, with name matching
│   │   ├── distribution.py   SD, SE, quartiles, IQR, Tukey whiskers
│   │   ├── boxplot.py        inline SVG box plots (no JS, no chart library)
│   │   ├── formatting.py     number formats shared by tables and charts
│   │   ├── cache.py          SQLite JSON cache
│   │   └── models.py         dataclasses passed between layers
│   ├── templates/            base, index, matches, results, compare,
│   │                         translations, error
│   └── static/               styles.css, levelstats.ico, the translations doc
├── tests/
│   ├── fixtures.py           hand-built API payloads
│   ├── test_mlb_client.py    lookup, multi-match, retries, caching, bulk fetch
│   ├── test_transforms.py    BB%/K%, zero PA, missing fields, level combining
│   ├── test_compare.py       deltas, PA filter, mean/median, ambiguity
│   ├── test_routes.py        every page, including empty and error states
│   ├── test_compare_routes.py  the Compare tab end to end
│   ├── test_translations_routes.py  the static doc and the tab nav
│   ├── test_translations_data.py    Python deltas vs the published HTML
│   ├── test_projection.py           dominant league, deltas, clamping, residuals
│   ├── test_projection_routes.py    the Projection tab end to end
│   ├── test_run.py           launcher: port picking, browser timing
│   ├── test_distribution.py  SD/SE, quartiles, whiskers, degenerate cases
│   ├── test_boxplot.py       SVG marks, ticks, escaping, no NaN in markup
│   └── _visual_check.py      dev-only: screenshot each page with stubbed data
├── Start Level Stats.bat        double-click launcher (Windows)
├── Create Desktop Shortcut.bat optional: desktop shortcut with icon
├── start-level-stats.command   double-click launcher (macOS/Linux)
├── tools/create-shortcut.ps1   used by the shortcut launcher
├── run.py                      port picking, browser timing, banner
├── requirements.txt
└── pytest.ini
```

The layers are strictly separated: `mlb_client` knows about HTTP and nothing
about stats; `transforms` knows about stats and nothing about HTTP; `compare`
knows about neither beyond a two-method protocol it can be stubbed against;
`main` just renders. That is what makes the translation model easy to bolt on
later — it will consume `transforms.build_report()` and
`compare.build_comparison()` output, not JSON.

---

## Not included, on purpose

Pitching, fielding, Statcast, wRC+ or any FanGraphs metric, projections, auth,
Docker, cloud deploy, and the historical translation coefficients themselves.
