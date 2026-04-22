import os
import json
import time
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()

DATA_FILE = "data.json"
BOOKMARKS_FILE = "bookmarks.json"

ARTIFICIAL_ANALYSIS_API_KEY = os.getenv("ARTIFICIAL_ANALYSIS_API_KEY")

session = requests.Session()
session.headers.update({
    "User-Agent": "AI-Bio-Command-Center/1.0",
    "Accept": "application/json, text/html;q=0.9, application/xml;q=0.8"
})


# -------------------------
# Generic helpers
# -------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def ok(items, source, meta=None):
    return {
        "status": "ok",
        "items": items,
        "source": source,
        "retrieved_at": now_iso(),
        "meta": meta or {}
    }

def err(source, message, items=None):
    return {
        "status": "error",
        "items": items or [],
        "source": source,
        "retrieved_at": now_iso(),
        "error": str(message)
    }

def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Could not load existing data: {e}")
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def preserve_previous_if_failed(existing, key, fresh):
    if fresh.get("status") == "ok":
        return fresh
    if key in existing:
        print(f"[warn] {key} failed, preserving previous good data")
        prev = existing[key]
        prev["stale"] = True
        prev["stale_reason"] = fresh.get("error", "unknown error")
        return prev
    return fresh


# -------------------------
# Artificial Analysis API
# -------------------------

def normalize_ai_model(row):
    """
    Normalize a model object from AA API into a consistent structure.

    Because API schemas can vary, this function checks multiple possible keys.
    Adjust once you confirm the exact response schema from /models.
    """
    name = (
        row.get("name")
        or row.get("model_name")
        or row.get("slug")
        or "Unknown model"
    )

    # Try multiple possible score fields
    score = (
        row.get("intelligence_score")
        or row.get("index_score")
        or row.get("score")
        or row.get("arena_score")
        or row.get("quality_score")
    )

    provider = row.get("provider") or row.get("lab") or row.get("company")
    pricing = row.get("pricing") or {}
    free_tier = (
        row.get("free_tier")
        or row.get("has_free_tier")
        or pricing.get("free")
        or False
    )

    return {
        "model": name,
        "score": score,
        "provider": provider,
        "free_tier": bool(free_tier),
        "raw": row
    }

def get_ai_leaderboard():
    """
    Pulls model list from Artificial Analysis API and sorts by the best available score field.
    You may need to adjust endpoint path or score field names based on the exact docs/schema.
    """
    if not ARTIFICIAL_ANALYSIS_API_KEY:
        return err("Artificial Analysis API", "Missing ARTIFICIAL_ANALYSIS_API_KEY")

    url = "https://artificialanalysis.ai/api/v2/models"  # adjust if docs specify different path
    headers = {
        "Authorization": f"Bearer {ARTIFICIAL_ANALYSIS_API_KEY}"
    }

    try:
        res = session.get(url, headers=headers, timeout=30)
        res.raise_for_status()
        payload = res.json()

        # Handle common response shapes:
        # payload could be a list, or {"data": [...]}, or {"models": [...]}
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("data") or payload.get("models") or payload.get("results") or []
        else:
            rows = []

        items = [normalize_ai_model(r) for r in rows]

        # Keep only models with a numeric-ish score
        scored = []
        for item in items:
            score = item.get("score")
            try:
                if score is not None:
                    item["score_num"] = float(score)
                    scored.append(item)
            except Exception:
                pass

        scored.sort(key=lambda x: x["score_num"], reverse=True)

        top = [
            {
                "model": x["model"],
                "score": x["score_num"],
                "provider": x.get("provider"),
                "free_tier": x.get("free_tier", False),
            }
            for x in scored[:10]
        ]

        return ok(top, url, meta={"total_models": len(rows)})

    except Exception as e:
        return err(url, e)

def get_free_tier_ai_leaderboard():
    base = get_ai_leaderboard()
    if base["status"] != "ok":
        return base

    free_items = [x for x in base["items"] if x.get("free_tier")]
    return ok(free_items[:10], base["source"], meta={"filter": "free_tier"})


# -------------------------
# Reddit feeds / saved searches
# -------------------------

def get_reddit_user_updates(username="Drwillpowers", limit=5):
    rss_url = f"https://www.reddit.com/user/{username}/submitted.rss"
    try:
        feed = feedparser.parse(rss_url)
        items = []
        for entry in feed.entries[:limit * 2]:
            title = entry.get("title", "Untitled")
            if "personal messages" in title.lower():
                continue
            items.append({
                "title": title,
                "link": entry.get("link", "#")
            })
            if len(items) >= limit:
                break
        return ok(items, rss_url)
    except Exception as e:
        return err(rss_url, e)

def get_reddit_searches(searches):
    """
    searches: list of dicts like:
    [
      {"label": "Longevity", "query": "longevity OR rapamycin"},
      {"label": "Biohacking", "query": "biohacking testosterone"}
    ]
    """
    all_items = []
    source = "Reddit search RSS"

    try:
        for s in searches:
            label = s["label"]
            query = s["query"]
            rss_url = f"https://www.reddit.com/search.rss?q={quote_plus(query)}&sort=new"

            feed = feedparser.parse(rss_url)
            entries = []
            for entry in feed.entries[:5]:
                entries.append({
                    "title": entry.get("title", "Untitled"),
                    "link": entry.get("link", "#"),
                    "label": label
                })

            all_items.append({
                "label": label,
                "query": query,
                "items": entries
            })

        return ok(all_items, source)
    except Exception as e:
        return err(source, e)


# -------------------------
# PubMed tracker
# -------------------------

def get_pubmed_tracker(queries):
    """
    queries: list of strings
    Uses NCBI E-utilities
    """
    source = "NCBI PubMed E-utilities"
    try:
        results = []

        for query in queries:
            search_url = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                f"?db=pubmed&sort=pub+date&retmax=5&retmode=json&term={quote_plus(query)}"
            )
            r = session.get(search_url, timeout=20)
            r.raise_for_status()
            ids = r.json().get("esearchresult", {}).get("idlist", [])

            items = []
            if ids:
                summary_url = (
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
                    f"?db=pubmed&retmode=json&id={','.join(ids)}"
                )
                s = session.get(summary_url, timeout=20)
                s.raise_for_status()
                summary = s.json().get("result", {})

                for pid in ids:
                    obj = summary.get(pid, {})
                    items.append({
                        "title": obj.get("title", "Untitled"),
                        "link": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                        "pubdate": obj.get("pubdate", "")
                    })

            results.append({
                "query": query,
                "items": items
            })

        return ok(results, source)
    except Exception as e:
        return err(source, e)


# -------------------------
# arXiv tracker
# -------------------------

def get_arxiv_tracker(queries):
    source = "arXiv API"
    try:
        results = []

        for query in queries:
            url = (
                "http://export.arxiv.org/api/query?"
                f"search_query=all:{quote_plus(query)}&start=0&max_results=5&sortBy=submittedDate&sortOrder=descending"
            )
            feed = feedparser.parse(url)
            items = []
            for entry in feed.entries:
                items.append({
                    "title": entry.get("title", "").replace("\n", " ").strip(),
                    "link": entry.get("link", "#"),
                    "published": entry.get("published", "")
                })

            results.append({
                "query": query,
                "items": items
            })

        return ok(results, source)
    except Exception as e:
        return err(source, e)


# -------------------------
# WGS price watch
# -------------------------

def get_wgs_prices():
    """
    For now this is semi-manual. Best next step is to create provider-specific scrapers.
    """
    items = [
        {"provider": "Element Vitari", "price": "$100", "note": "Targeting 2026", "url": "https://www.elementbiosciences.com/"},
        {"provider": "Nebula", "price": "$249", "note": "Current Sale", "url": "https://nebula.org/whole-genome-sequencing-dna-test/"},
        {"provider": "Sequencing.com", "price": "$379", "note": "Clinical Grade", "url": "https://sequencing.com/"}
    ]
    return ok(items, "manual")


# -------------------------
# Weather
# -------------------------

def get_weather(latitude=42.3314, longitude=-83.0458):
    """
    Default: Detroit
    No API key needed via open-meteo
    """
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}"
        "&current=temperature_2m,apparent_temperature,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min"
        "&timezone=auto"
    )
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
        current = data.get("current", {})
        daily = data.get("daily", {})

        items = [{
            "temperature": current.get("temperature_2m"),
            "feels_like": current.get("apparent_temperature"),
            "weather_code": current.get("weather_code"),
            "today_max": (daily.get("temperature_2m_max") or [None])[0],
            "today_min": (daily.get("temperature_2m_min") or [None])[0],
        }]
        return ok(items, url)
    except Exception as e:
        return err(url, e)


# -------------------------
# Calendar placeholder
# -------------------------

def get_calendar():
    """
    Safe public-github approach:
    - don't connect your real Google Calendar directly in a public client
    - instead use a local/private ICS URL or a manually maintained agenda.json
    """
    agenda_file = "agenda.json"
    if os.path.exists(agenda_file):
        try:
            with open(agenda_file, "r", encoding="utf-8") as f:
                items = json.load(f)
            return ok(items, "agenda.json")
        except Exception as e:
            return err("agenda.json", e)
    return ok([], "agenda.json", meta={"note": "No local agenda configured"})


# -------------------------
# Bookmarks
# -------------------------

def get_bookmarks():
    if not os.path.exists(BOOKMARKS_FILE):
        sample = {
            "AI": [
                {"title": "Artificial Analysis", "url": "https://artificialanalysis.ai/"},
                {"title": "OpenRouter", "url": "https://openrouter.ai/"}
            ],
            "Bio": [
                {"title": "PubMed", "url": "https://pubmed.ncbi.nlm.nih.gov/"},
                {"title": "arXiv q-bio", "url": "https://arxiv.org/list/q-bio/new"}
            ]
        }
        with open(BOOKMARKS_FILE, "w", encoding="utf-8") as f:
            json.dump(sample, f, indent=2)
        return ok(sample, BOOKMARKS_FILE, meta={"note": "Sample bookmarks file created"})

    try:
        with open(BOOKMARKS_FILE, "r", encoding="utf-8") as f:
            bookmarks = json.load(f)
        return ok(bookmarks, BOOKMARKS_FILE)
    except Exception as e:
        return err(BOOKMARKS_FILE, e)


# -------------------------
# Gmail links
# -------------------------

def get_gmail_links():
    """
    Safe compromise for public repo:
    Keep ONLY generic Gmail search URLs here.
    Don't include personal identifiers, exact addresses, or sensitive search terms in public code if they reveal private info.
    """
    items = [
        {
            "title": "Important in last 24 hours",
            "url": "https://mail.google.com/mail/u/0/#search/is%3Aimportant+newer_than%3A1d"
        },
        {
            "title": "Unread starred",
            "url": "https://mail.google.com/mail/u/0/#search/is%3Aunread+is%3Astarred"
        }
    ]
    return ok(items, "manual")


# -------------------------
# Main updater
# -------------------------

def update_dashboard():
    print(f"[{time.strftime('%H:%M:%S')}] Syncing data...")
    existing = load_existing_data()

    fresh = {
        "ai_leaderboard": get_ai_leaderboard(),
        "ai_free_tier": get_free_tier_ai_leaderboard(),
        "reddit_user": get_reddit_user_updates("Drwillpowers", limit=4),
        "reddit_searches": get_reddit_searches([
            {"label": "Longevity", "query": "longevity OR rapamycin"},
            {"label": "Hormones", "query": "testosterone OR enclomiphene OR hcg"},
            {"label": "Genome", "query": "\"whole genome sequencing\" OR WGS"}
        ]),
        "wgs_price": get_wgs_prices(),
        "pubmed": get_pubmed_tracker([
            "rapamycin longevity",
            "whole genome sequencing cost",
            "enclomiphene testosterone"
        ]),
        "arxiv": get_arxiv_tracker([
            "AI agents",
            "genomics foundation model",
            "longevity"
        ]),
        "weather": get_weather(),
        "calendar": get_calendar(),
        "bookmarks": get_bookmarks(),
        "gmail_links": get_gmail_links(),
        "last_updated": now_iso()
    }

    # preserve previous on failure for each widget
    final = {"last_updated": fresh["last_updated"]}
    for key, value in fresh.items():
        if key == "last_updated":
            continue
        final[key] = preserve_previous_if_failed(existing, key, value)

    save_data(final)
    print("Saved data.json")


if __name__ == "__main__":
    while True:
        update_dashboard()
        time.sleep(900)  # every 15 min
