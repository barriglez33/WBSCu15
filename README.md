# WBSC U-15 World Cup 2026 News RSS

GitHub-hosted multilingual news monitor for the **WBSC U-15 Baseball World Cup 2026**.

The repository tracks **121 searches** covering tournament terms, venues, national teams, players, Mexico U-15 roster/staff, results, streaming references and hashtags.

## Three-batch rotation

To make each GitHub Actions run lighter, the searches are now split into **three alternating batches**:

- **Batch 1:** 41 searches
- **Batch 2:** 40 searches
- **Batch 3:** 40 searches

The sequence is:

```text
Run 1 → Batch 1
Run 2 → Batch 2
Run 3 → Batch 3
Run 4 → Batch 1
...
```

The workflow still runs every hour.

Rotation state is stored in:

`data/state.json`

## Rolling three-hour window

Because each individual keyword is now searched once every **3 hours**, each run looks back over the previous **3 hours**.

Example:

```text
10:43 → Batch 1 → searches 07:43–10:43
11:43 → Batch 2 → searches 08:43–11:43
12:43 → Batch 3 → searches 09:43–12:43
13:43 → Batch 1 → searches 10:43–13:43
```

This avoids gaps while keeping each Action much lighter than the previous two-batch version.

Google News publication dates are checked before redirect decoding and article extraction.

## Mexico U-15 roster and staff

The repository includes the 30 additional Mexico names with:

- name
- jersey number where available
- role / position
- team: `Mexico U-15`

Players use the `Players` category.

Manager, coaches and support personnel use `Mexico Team Staff`.

## Features

- GDELT + Google News multilingual discovery
- automatic Spanish translation
- source at the beginning of RSS titles
- smart duplicate detection
- stable RSS generation
- automatic `git push` retries
- master RSS
- category feeds
- individual keyword/person feeds

## Workflow

`.github/workflows/update.yml`

Action name:

**Update WBSC U-15 World Cup RSS**

Runs every hour at minute `:43`.

## Generated files

- `docs/feed.xml`
- `docs/categories/*.xml`
- `docs/keywords/*.xml`
- `docs/index.html`
- `data/articles.json`
- `data/state.json`

Do not delete `data/state.json` unless you intentionally want to reset the batch rotation.
