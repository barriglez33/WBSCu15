import json
import re
import time
import hashlib
import html
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlsplit, urlunsplit, parse_qsl, urlencode

import feedparser
import requests
import trafilatura
from googlenewsdecoder import gnewsdecoder
from deep_translator import GoogleTranslator, MyMemoryTranslator
from langdetect import detect as detect_language

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA = ROOT / "data/articles.json"
STATE = ROOT / "data/state.json"
DOCS = ROOT / "docs"
KEYWORD_DIR = DOCS / "keywords"
CATEGORY_DIR = DOCS / "categories"

def norm(s):
    return re.sub(
        r"\s+",
        " ",
        "".join(
            c for c in unicodedata.normalize("NFKD", str(s))
            if not unicodedata.combining(c)
        ).lower()
    ).strip()

def slug(s):
    s = norm(s).replace("#", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "feed"

def strip_hashtag(s):
    return str(s).lstrip("#").strip()

def clean_url(u):
    try:
        p = urlsplit(u)
        q = [
            (k, v) for k, v in parse_qsl(p.query)
            if not k.lower().startswith("utm_")
            and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
        ]
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))
    except Exception:
        return u

def domain(u):
    try:
        return urlsplit(u).netloc.removeprefix("www.")
    except Exception:
        return ""

def parse_dt(s):
    if not s:
        return datetime.now(timezone.utc)
    for fmt in (
        "%Y%m%dT%H%M%SZ",
        "%Y%m%d%H%M%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            d = datetime.strptime(str(s), fmt)
            return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
        except Exception:
            pass
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)

def feed_dt(entry):
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc)

def query_for(item):
    kw = strip_hashtag(item["keyword"])
    quoted = f'"{kw}"'

    if item["category"] == "Players":
        return f'{quoted} (WBSC OR "U-15" OR U15 OR "Sub-15" OR baseball OR beisbol OR béisbol)'

    if item["category"] == "Hashtags":
        if norm(kw) in {"baseball", "wbsc"}:
            return f'{quoted} ("U-15" OR U15 OR "Sub-15" OR "Baseball World Cup" OR Yucatan OR Yucatán)'
        return f'{quoted} (WBSC OR baseball OR beisbol OR béisbol OR U15 OR "U-15")'

    return quoted

def decode_google(u):
    if "news.google.com" not in u:
        return clean_url(u)
    try:
        r = gnewsdecoder(
            u,
            interval=CFG["settings"].get("google_decode_interval_seconds", 0.05)
        )
        if isinstance(r, dict) and r.get("status") and r.get("decoded_url"):
            return clean_url(r["decoded_url"])
    except Exception as exc:
        print("Google decode failed:", exc)
    return None

def discover_gdelt(item):
    try:
        r = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": query_for(item),
                "mode": "artlist",
                "maxrecords": CFG["settings"]["gdelt_results_per_keyword"],
                "timespan": f'{CFG["settings"]["max_age_hours"]}h',
                "sort": "datedesc",
                "format": "json",
            },
            timeout=CFG["settings"].get("request_timeout_seconds", 25),
            headers={"User-Agent": "WBSCU15WorldCupRSS/1.0"},
        )
        r.raise_for_status()
        arr = r.json().get("articles", [])
    except Exception as exc:
        print("  GDELT error:", exc)
        return []

    out = []
    for x in arr:
        u = clean_url(x.get("url", ""))
        if not u:
            continue
        out.append({
            "url": u,
            "title": x.get("title", ""),
            "source": x.get("domain", ""),
            "published": parse_dt(x.get("seendate")),
            "language": x.get("language", ""),
            "country": x.get("sourcecountry", ""),
            "via": "GDELT",
            "keyword": item["keyword"],
            "category": item["category"],
        })
    return out

def discover_google(item):
    out = []
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=float(CFG["settings"].get("max_age_hours", 2))
    )
    q = query_for(item)

    for ed in CFG["google_news_editions"]:
        url = (
            "https://news.google.com/rss/search?"
            f'q={quote_plus(q)}&hl={quote_plus(ed["hl"])}'
            f'&gl={quote_plus(ed["gl"])}&ceid={quote_plus(ed["ceid"])}'
        )
        feed = feedparser.parse(url)

        for entry in list(getattr(feed, "entries", []))[:CFG["settings"]["google_results_per_edition"]]:
            published = feed_dt(entry)

            # Reject old items before decoding the Google News redirect URL.
            if published < cutoff:
                continue

            u = decode_google(getattr(entry, "link", ""))
            if not u:
                continue

            src = ""
            try:
                src = entry.source.get("title", "") if getattr(entry, "source", None) else ""
            except Exception:
                pass

            out.append({
                "url": u,
                "title": getattr(entry, "title", ""),
                "source": src or domain(u),
                "published": published,
                "language": "",
                "country": ed["label"],
                "via": "Google News",
                "keyword": item["keyword"],
                "category": item["category"],
            })
    return out

def extract_article(u):
    try:
        raw = trafilatura.fetch_url(u)
        if not raw:
            return None
        result = trafilatura.extract(
            raw,
            url=u,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        if not result:
            return None
        d = json.loads(result)
        body = (d.get("text") or "").strip()
        if not body:
            return None
        return {
            "title": (d.get("title") or "").strip(),
            "author": (d.get("author") or "").strip(),
            "body": body,
        }
    except Exception as exc:
        print("  Extraction failed:", exc)
        return None

def contains_keyword(text, item):
    n = norm(text)
    kw = norm(strip_hashtag(item["keyword"]))

    # Full phrase first.
    if kw and kw in n:
        return True

    # A small amount of tolerance for accents/punctuation/name variants.
    parts = [x for x in re.findall(r"[a-z0-9]+", kw) if len(x) > 1]
    if item["category"] == "Players":
        # For a person, require almost the full name.
        return len(parts) >= 2 and all(p in n for p in parts)

    if len(parts) >= 3:
        hits = sum(1 for p in parts if p in n)
        return hits >= max(2, len(parts) - 1)

    return False

def has_event_context(text):
    n = norm(text)
    return any(norm(term) in n for term in CFG["event_context_terms"])

def relevant(text, item):
    if not contains_keyword(text, item):
        return False

    # Player names and generic hashtags need tournament/baseball context.
    if item["category"] in {"Players", "Hashtags"}:
        return has_event_context(text)

    return True

def text_chunks(text, size):
    out = []
    current = ""
    for para in str(text).split("\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > size:
            cut = para.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            piece, para = para[:cut], para[cut:].strip()
            if current:
                out.append(current)
                current = ""
            out.append(piece)
        add = para if not current else "\n\n" + para
        if len(current) + len(add) <= size:
            current += add
        else:
            if current:
                out.append(current)
            current = para
    if current:
        out.append(current)
    return out

def translate_text(text):
    if not text:
        return text, True

    size = CFG["translation"].get("chunk_size", 2200)
    translated = []

    for chunk in text_chunks(text, size):
        ok = False
        last_error = None

        for attempt in range(CFG["translation"].get("max_retries", 2)):
            try:
                value = GoogleTranslator(source="auto", target="es").translate(chunk)
                translated.append(value or chunk)
                ok = True
                break
            except Exception as exc:
                last_error = exc
                time.sleep(CFG["translation"].get("retry_delay_seconds", 1))

        if not ok:
            try:
                value = MyMemoryTranslator(source="auto", target="es-ES").translate(chunk)
                translated.append(value or chunk)
                ok = True
            except Exception as exc:
                last_error = exc

        if not ok:
            print("  Translation fallback:", last_error)
            translated.append(chunk)

    output = "\n\n".join(translated)
    return output, output != text or len(text) < 3

def ensure_translation(article):
    article["original_title"] = article.get("original_title") or article.get("title", "")
    article["original_body"] = article.get("original_body") or article.get("body", "")

    if (
        article.get("translation_status") in {"translated", "already_spanish"}
        and article.get("rss_title")
        and article.get("rss_body")
    ):
        return

    try:
        detected = detect_language(article["original_body"][:1800])
    except Exception:
        detected = ""

    if detected == "es":
        article["rss_title"] = article["original_title"]
        article["rss_body"] = article["original_body"]
        article["translation_status"] = "already_spanish"
        article["translated_to"] = "es"
        article["source_detected_language"] = "es"
        return

    article["rss_title"], ok1 = translate_text(article["original_title"])
    article["rss_body"], ok2 = translate_text(article["original_body"])
    article["translation_status"] = "translated" if ok1 and ok2 else "partial_or_fallback"
    article["translated_to"] = "es"
    article["source_detected_language"] = detected
    article["translation_last_attempt"] = datetime.now(timezone.utc).isoformat()

def tokens(s):
    return {
        w for w in re.findall(r"[a-z0-9]+", norm(s))
        if len(w) > 2
    }

def similarity(a, b):
    if not a or not b:
        return 0
    return SequenceMatcher(None, norm(a), norm(b)).ratio()

def token_overlap(a, b):
    x, y = tokens(a), tokens(b)
    if not x or not y:
        return 0
    return len(x & y) / len(x | y)

def article_dt(a):
    try:
        return datetime.fromisoformat(a["published_iso"]).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)

def duplicate(a, b):
    d = CFG["deduplication"]

    if abs((article_dt(a) - article_dt(b)).total_seconds()) / 3600 > d["max_hours_apart"]:
        return False

    ta = a.get("rss_title") or a.get("title", "")
    tb = b.get("rss_title") or b.get("title", "")
    ba = a.get("rss_body") or a.get("body", "")
    bb = b.get("rss_body") or b.get("body", "")

    ts = similarity(ta, tb)
    ov = token_overlap(ta, tb)
    bs = similarity(
        ba[:d["body_lead_characters"]],
        bb[:d["body_lead_characters"]],
    )

    return (
        ts >= d["title_similarity_threshold"]
        or ov >= d["title_token_overlap_threshold"]
        or (ts >= 0.52 and bs >= d["body_lead_similarity_threshold"])
        or bs >= 0.86
    )

def completeness_score(a):
    score = min(len(a.get("rss_body") or a.get("body", "")), 30000)
    score += min(len(a.get("rss_title") or a.get("title", "")), 180) * 2
    if a.get("author"):
        score += 500
    if a.get("source"):
        score += 250
    if a.get("translation_status") in {"translated", "already_spanish"}:
        score += 150
    return score

def merge_metadata(winner, loser):
    for key in ("matched_keywords", "categories", "discovery_sources", "source_languages", "source_countries"):
        winner.setdefault(key, [])
        for value in loser.get(key, []):
            if value and value not in winner[key]:
                winner[key].append(value)

    winner.setdefault("alternate_sources", [])
    alt = {
        "source": loser.get("source", ""),
        "url": loser.get("url", ""),
        "title": loser.get("title", ""),
        "body_characters": len(loser.get("rss_body") or loser.get("body", "")),
    }
    if alt["url"] and not any(x.get("url") == alt["url"] for x in winner["alternate_sources"]):
        winner["alternate_sources"].append(alt)
    winner["duplicate_versions_removed"] = len(winner["alternate_sources"])

def deduplicate(articles):
    kept = []
    for article in sorted(articles, key=lambda x: x.get("published_iso", ""), reverse=True):
        match_index = next((i for i, existing in enumerate(kept) if duplicate(article, existing)), None)

        if match_index is None:
            kept.append(article)
        elif completeness_score(article) > completeness_score(kept[match_index]):
            merge_metadata(article, kept[match_index])
            kept[match_index] = article
        else:
            merge_metadata(kept[match_index], article)

    return kept

def source_label(a):
    return (a.get("source") or domain(a.get("url", "")) or "Fuente desconocida").strip()

def display_title(a):
    return f'[{source_label(a)}] {a.get("rss_title") or a.get("title", "")}'

def cdata(value):
    return "<![CDATA[" + str(value).replace("]]>", "]]]]><![CDATA[>") + "]]>"

def stable_build_date(articles):
    if not articles:
        return format_datetime(datetime(2026, 1, 1, tzinfo=timezone.utc))
    newest = max((article_dt(a) for a in articles), default=datetime(2026, 1, 1, tzinfo=timezone.utc))
    return format_datetime(newest)

def rss_xml(articles, title, description):
    ordered = sorted(
        articles,
        key=lambda x: x.get("published_iso", ""),
        reverse=True
    )[:CFG["settings"]["max_feed_items"]]

    parts = []
    for a in ordered:
        body = a.get("rss_body") or a.get("body", "")
        body_html = "<p>" + html.escape(body).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
        cats = "\n".join(
            f"      <category>{html.escape(x)}</category>"
            for x in a.get("matched_keywords", [])
        )
        creator = (
            f"      <dc:creator>{cdata(a['author'])}</dc:creator>\n"
            if a.get("author") else ""
        )
        pub_date = a.get("published_rfc2822") or format_datetime(article_dt(a))
        article_id = a.get("id") or hashlib.sha256(a.get("url", "").encode()).hexdigest()[:20]

        parts.append(f"""    <item>
      <title>{cdata(display_title(a))}</title>
      <link>{html.escape(a.get("url", ""))}</link>
      <guid isPermaLink="false">{article_id}</guid>
      <pubDate>{pub_date}</pubDate>
      <source>{cdata(a.get("source", ""))}</source>
{creator}      <description>{cdata(body[:500])}</description>
      <content:encoded>{cdata(body_html)}</content:encoded>
{cats}
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
<title>{cdata(title)}</title>
<link>{CFG["feed"]["site_url"]}</link>
<description>{cdata(description)}</description>
<lastBuildDate>{stable_build_date(ordered)}</lastBuildDate>
{''.join(parts)}
</channel>
</rss>
"""

def write_if_changed(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True

def generate(articles):
    changed = 0

    changed += write_if_changed(
        DOCS / "feed.xml",
        rss_xml(articles, CFG["feed"]["title"], CFG["feed"]["description"])
    )

    for item in CFG["keywords"]:
        subset = [
            a for a in articles
            if item["keyword"] in a.get("matched_keywords", [])
        ]
        changed += write_if_changed(
            KEYWORD_DIR / f'{slug(item["keyword"])}.xml',
            rss_xml(
                subset,
                f'{item["keyword"]} — WBSC U-15 World Cup 2026',
                f'Noticias relacionadas con {item["keyword"]}.'
            )
        )

    categories = sorted({item["category"] for item in CFG["keywords"]})
    for category in categories:
        subset = [
            a for a in articles
            if category in a.get("categories", [])
        ]
        changed += write_if_changed(
            CATEGORY_DIR / f'{slug(category)}.xml',
            rss_xml(
                subset,
                f'{category} — WBSC U-15 World Cup 2026',
                f'Noticias de la categoría {category}.'
            )
        )

    cards = []
    for a in sorted(articles, key=lambda x: x.get("published_iso", ""), reverse=True)[:250]:
        kws = ", ".join(a.get("matched_keywords", [])[:5])
        cards.append(
            f'<article><h2><a href="{html.escape(a.get("url",""))}">'
            f'{html.escape(display_title(a))}</a></h2>'
            f'<p>{html.escape(kws)}</p></article>'
        )

    index_html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>WBSC U-15 World Cup 2026</title></head><body>"
        "<h1>WBSC U-15 World Cup 2026 News</h1>"
        "<p><a href='feed.xml'>RSS general</a></p>"
        + "".join(cards)
        + "</body></html>"
    )
    changed += write_if_changed(DOCS / "index.html", index_html)

    print("Generated files changed:", changed)

def load_state():
    if not STATE.exists():
        return {"next_batch": 1}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"next_batch": 1}

def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def select_batch():
    state = load_state()
    batch = 1 if int(state.get("next_batch", 1)) == 1 else 2
    selected = [
        item for item in CFG["keywords"]
        if int(item.get("batch", 1)) == batch
    ]
    print(f"Batch {batch}: {len(selected)} keywords")
    print(f"Rolling window: last {CFG['settings']['max_age_hours']} hours")
    return selected, batch, state

def main():
    articles = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
    by_url = {a.get("url"): a for a in articles if a.get("url")}

    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=float(CFG["settings"].get("max_age_hours", 2))
    )

    selected, batch, state = select_batch()
    new_ids = []

    for item in selected:
        print("SEARCH:", item["id"], item["category"], "/", item["keyword"])

        candidates = discover_gdelt(item) + discover_google(item)
        unique = {
            c["url"]: c
            for c in candidates
            if c.get("url") and c["published"] >= cutoff
        }

        for c in sorted(unique.values(), key=lambda x: x["published"], reverse=True):
            if c["url"] in by_url:
                a = by_url[c["url"]]

                if item["keyword"] not in a.setdefault("matched_keywords", []):
                    a["matched_keywords"].append(item["keyword"])

                if item["category"] not in a.setdefault("categories", []):
                    a["categories"].append(item["category"])

                if c.get("via") and c["via"] not in a.setdefault("discovery_sources", []):
                    a["discovery_sources"].append(c["via"])

                continue

            ex = extract_article(c["url"])
            if not ex or len(ex["body"]) < CFG["settings"]["minimum_body_characters"]:
                continue

            text = ex["title"] + "\n" + ex["body"]
            if not relevant(text, item):
                continue

            # Tag all configured keywords that actually appear in the accepted article.
            matched = [
                x["keyword"] for x in CFG["keywords"]
                if contains_keyword(text, x)
            ]
            if item["keyword"] not in matched:
                matched.append(item["keyword"])

            categories = sorted({
                x["category"] for x in CFG["keywords"]
                if x["keyword"] in matched
            }) or [item["category"]]

            pub = c["published"]
            article = {
                "id": hashlib.sha256(c["url"].encode()).hexdigest()[:20],
                "title": ex["title"] or c["title"],
                "source": c["source"] or domain(c["url"]),
                "author": ex["author"],
                "url": c["url"],
                "published_iso": pub.isoformat(),
                "published_rfc2822": format_datetime(pub),
                "body": ex["body"],
                "matched_keywords": matched,
                "categories": categories,
                "source_languages": [c["language"]] if c["language"] else [],
                "source_countries": [c["country"]] if c["country"] else [],
                "discovery_sources": [c["via"]],
            }

            articles.append(article)
            by_url[article["url"]] = article
            new_ids.append(article["id"])

    articles = sorted(
        articles,
        key=lambda x: x.get("published_iso", ""),
        reverse=True
    )[:CFG["settings"]["max_stored_articles"]]

    new_set = set(new_ids)
    new_articles = [a for a in articles if a.get("id") in new_set]

    print("New accepted articles:", len(new_articles))
    for article in new_articles:
        ensure_translation(article)

    repair_limit = int(CFG["settings"].get("old_translation_repairs_per_run", 10))
    repairs = 0

    for article in articles:
        if repairs >= repair_limit:
            break
        if article.get("id") in new_set:
            continue
        if article.get("translation_status") not in {"translated", "already_spanish"}:
            ensure_translation(article)
            repairs += 1

    print(f"Older translation repairs: {repairs}/{repair_limit}")

    articles = deduplicate(articles)

    DATA.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    generate(articles)

    # Advance batch only after processing + file generation succeeds.
    state["last_completed_batch"] = batch
    state["last_completed_at"] = datetime.now(timezone.utc).isoformat()
    state["next_batch"] = 2 if batch == 1 else 1
    save_state(state)

    print("Unique stories:", len(articles))
    print("Next batch:", state["next_batch"])

if __name__ == "__main__":
    main()
