# WBSC U-15 World Cup 2026 News RSS

GitHub-hosted multilingual news monitor for the **WBSC U-15 Baseball World Cup 2026**.

The repository tracks **91 keywords and hashtags** covering the tournament, venues, national teams, players, results, Super Round coverage, streaming references, and social hashtags.

## Rotation

To keep GitHub Actions fast, the 91 searches are split into two alternating batches:

- **Batch 1:** 46 keywords
- **Batch 2:** 45 keywords

The workflow runs every hour. After a successful run, the next execution uses the other batch.

Rotation state is stored in:

`data/state.json`

## Rolling two-hour window

Every run searches only stories from the previous **2 hours**.

Because batches alternate hourly, the two-hour window provides overlap while avoiding unnecessary work on old results.

Google News publication dates are checked **before** Google redirect decoding and article extraction.

## Discovery

The project uses:

- GDELT DOC API
- Google News RSS

Google News editions currently include:

- United States / English
- Mexico / Spanish
- Japan / Japanese
- Taiwan / Traditional Chinese

GDELT adds broader multilingual discovery.

## Translation

Accepted stories are automatically translated into **Spanish**.

Articles already detected as Spanish are kept as-is.

Both original and RSS-ready translated text are retained in `data/articles.json`.

## Smart duplicate detection

The monitor compares:

- titles
- title token overlap
- article-body leads
- publication time

When multiple publishers cover essentially the same story, the most complete version is retained and alternate URLs can be stored in `alternate_sources`.

## RSS title format

Every title begins with its source:

`[WBSC] ...`

`[MLB.com] ...`

`[USA Baseball] ...`

## Generated feeds

Master feed:

`docs/feed.xml`

Category feeds:

`docs/categories/`

Keyword feeds:

`docs/keywords/`

Dashboard:

`docs/index.html`

## Stable file generation

RSS feeds use the publication time of their newest article as `lastBuildDate`.

If a feed has no actual content change, the file is not rewritten. This reduces unnecessary Git commits and large pushes.

## GitHub push protection

The workflow automatically retries `git push` up to **5 times** if GitHub returns a temporary remote/server error.

## GitHub Actions

Workflow:

`.github/workflows/update.yml`

Action name:

**Update WBSC U-15 World Cup RSS**

Schedule:

Every hour at minute `:43`.

## GitHub Pages

For a public repository:

**Settings → Pages → Deploy from a branch → main → /docs**

Your master feed will normally be:

`https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/feed.xml`

## Important files

```text
main.py
config.json
requirements.txt
README.md

.github/
└── workflows/
    └── update.yml

data/
├── articles.json
└── state.json   # generated automatically after first successful run

docs/
├── feed.xml
├── index.html
├── categories/
└── keywords/
```
