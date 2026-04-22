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
ARTIFICIAL_ANALYSIS_API_KEY = os.getenv("ARTIFICIAL_ANALYSIS_API_KEY")

session = requests.Session()
session.headers.update({
    "User-Agent": "AI-Bio-Command-Center/1.0",
    "Accept": "application/json, application/xml;q=0.9, text/html;q=0.8"
})

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
    if not os.path.exists(DATA_FILE):
        return {}

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return migrate_legacy_data(data)
    except Exception as e:
        print(f"Failed to load existing data: {e}")
        return {}

def wrap_legacy_widget(value, source="legacy_data.json"):
    if isinstance(value, dict) and "items" in value:
        return value

    if isinstance(value, list):
        items = value
    elif value is None:
        items = []
    else:
        items = [value]

    return {
        "status": "ok",
        "items": items,
        "source": source,
        "retrieved_at": now_iso(),
        "meta": {"migrated_from_legacy_format": True}
    }

def migrate_legacy_data(data):
    if not isinstance(data, dict):
        return {}

    migrated = dict(data)

    widget_keys = [
        "ai_leaderboard",
        "ai_free_tier",
        "reddit",
        "reddit_user",
        "reddit_searches",
        "wgs_price",
        "pubmed",
        "arxiv",
        "weather",
        "calendar",
        "bookmarks",
        "gmail_links",
    ]

    for key in widget_keys:
        if key in migrated:
            migrated[key] = wrap_legacy_widget(migrated[key])

    return migrated

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def preserve_previous_if_failed(existing, key, fresh):
    if fresh.get("status") == "ok":
        return fresh

    if key not in existing:
        return fresh

    prev = existing[key]

    # New schema: already a widget dict
    if isinstance(prev, dict) and "items" in prev:
        prev = dict(prev)  # shallow copy
        prev["stale"] = True
        prev["stale_reason"] = fresh.get("error", "unknown error")
        return prev

    # Old schema fallback: wrap legacy values
    return {
        "status": "ok",
        "items": prev if isinstance(prev, list) else [prev],
        "source": "legacy_data.json",
        "retrieved_at": now_iso(),
        "stale": True,
        "stale_reason": fresh.get("error", "unknown error"),
        "meta": {"migrated_from_legacy_format": True}
    }

# ---------------- AI / Artificial Analysis ----------------

def fetch_aa_models():
    if not ARTIFICIAL_ANALYSIS_API_KEY:
        raise RuntimeError("Missing ARTIFICIAL_ANALYSIS_API_KEY")

    url = "https://artificialanalysis.ai/api/v2/data/llms/models"
    res = session.get(url, headers={"x-api-key": ARTIFICIAL_ANALYSIS_API_KEY}, timeout=30)
    res.raise_for_status()
    payload = res.json()

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected API response shape")
    rows = payload.get("data", [])
    if not isinstance(rows, list):
        raise RuntimeError("Unexpected 'data' field type")

    return url, rows

def normalize_ai_model(row):
    creator = row.get("model_creator") or {}
    evaluations = row.get("evaluations") or {}
    pricing = row.get("pricing") or {}

    return {
        "id": row.get("id"),
        "model": row.get("name"),
        "slug": row.get("slug"),
        "provider": creator.get("name"),
        "release_date": row.get("release_date"),
        "score": evaluations.get("artificial_analysis_intelligence_index"),
        "coding_index": evaluations.get("artificial_analysis_coding_index"),
        "math_index": evaluations.get("artificial_analysis_math_index"),
        "price_input": pricing.get("price_1m_input_tokens"),
        "price_output": pricing.get("price_1m_output_tokens"),
        "price_blended": pricing.get("price_1m_blended_3_to_1"),
        "speed_tps": row.get("median_output_tokens_per_second"),
        "ttft": row.get("median_time_to_first_token_seconds"),
    }

def get_ai_leaderboard(limit=5):
    try:
        source, rows = fetch_aa_models()
        items = []

        for row in rows:
            model = normalize_ai_model(row)
            if model["score"] is None:
                continue
            try:
                model["score"] = float(model["score"])
                items.append(model)
            except Exception:
                continue

        items.sort(key=lambda x: x["score"], reverse=True)

        top = [{
            "model": m["model"],
            "provider": m["provider"],
            "score": m["score"],
            "release_date": m["release_date"],
            "price_blended": m["price_blended"],
        } for m in items[:limit]]

        return ok(top, source, meta={"ranking_field": "artificial_analysis_intelligence_index"})
    except Exception as e:
        return err("https://artificialanalysis.ai/api/v2/data/llms/models", e)

def load_free_tier_allowlist():
    path = "free_tier_models.json"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return set(data)
        except Exception:
            pass
    return set()

def looks_free_tier(model):
    return (
        model.get("price_blended") == 0 or
        model.get("price_input") == 0 or
        model.get("price_output") == 0
    )

def get_free_tier_ai_leaderboard(limit=5):
    try:
        source, rows = fetch_aa_models()
        allowlist = load_free_tier_allowlist()
        items = []

        for row in rows:
            model = normalize_ai_model(row)
            if model["score"] is None:
                continue
            try:
                model["score"] = float(model["score"])
            except Exception:
                continue

            if model["model"] in allowlist or looks_free_tier(model):
                items.append(model)

        items.sort(key=lambda x: x["score"], reverse=True)

        top = [{
            "model": m["model"],
            "provider": m["provider"],
            "score": m["score"],
            "price_blended": m["price_blended"],
        } for m in items[:limit]]

        return ok(top, source, meta={"filter": "free_tier_allowlist_or_zero_price"})
    except Exception as e:
        return err("https://artificialanalysis.ai/api/v2/data/llms/models", e)

# ---------------- Reddit ----------------

def get_reddit_user_updates(username="Drwillpowers", limit=4):
    rss_url = f"https://www.reddit.com/user/{username}/submitted.rss"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DashboardBot/1.0)"
    }

    try:
        res = session.get(rss_url, headers=headers, timeout=20)
        res.raise_for_status()

        soup = BeautifulSoup(res.content, "xml")
        entries = soup.find_all("entry")

        items = []
        for entry in entries:
            title_tag = entry.find("title")
            link_tag = entry.find("link")

            title = title_tag.text.strip() if title_tag else "Untitled"
            link = link_tag.get("href", "#") if link_tag else "#"

            if "personal messages" in title.lower():
                continue

            items.append({
                "title": title,
                "link": link
            })

            if len(items) >= limit:
                break

        return ok(items, rss_url, meta={"entry_count": len(entries)})

    except Exception as e:
        return err(rss_url, e)

def get_reddit_searches(searches):
    try:
        groups = []
        for s in searches:
            label = s["label"]
            query = s["query"]
            rss_url = f"https://www.reddit.com/search.rss?q={quote_plus(query)}&sort=new"
            feed = feedparser.parse(rss_url)
            items = [{"title": e.get("title", "Untitled"), "link": e.get("link", "#")} for e in feed.entries[:5]]
            groups.append({"label": label, "query": query, "items": items})
        return ok(groups, "reddit rss")
    except Exception as e:
        return err("reddit rss", e)

# ---------------- WGS ----------------

def get_wgs_prices():
    return ok([
        {"provider": "Element Vitari", "price": "$100", "note": "Targeting 2026", "url": "https://www.elementbiosciences.com/"},
        {"provider": "Nebula", "price": "$249", "note": "Current Sale", "url": "https://nebula.org/whole-genome-sequencing-dna-test/"},
        {"provider": "Sequencing.com", "price": "$379", "note": "Clinical Grade", "url": "https://sequencing.com/"}
    ], "manual")

# ---------------- PubMed ----------------

def get_pubmed_tracker(queries):
    try:
        results = []
        for query in queries:
            search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&sort=pub+date&retmax=5&retmode=json&term={quote_plus(query)}"
            r = session.get(search_url, timeout=20)
            r.raise_for_status()
            ids = r.json().get("esearchresult", {}).get("idlist", [])

            items = []
            if ids:
                summary_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id={','.join(ids)}"
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
            results.append({"query": query, "items": items})
        return ok(results, "pubmed")
    except Exception as e:
        return err("pubmed", e)

# ---------------- arXiv ----------------

def get_arxiv_tracker(queries):
    try:
        results = []
        for query in queries:
            url = f"http://export.arxiv.org/api/query?search_query=all:{quote_plus(query)}&start=0&max_results=5&sortBy=submittedDate&sortOrder=descending"
            feed = feedparser.parse(url)
            items = [{
                "title": e.get("title", "").replace("\n", " ").strip(),
                "link": e.get("link", "#"),
                "published": e.get("published", "")
            } for e in feed.entries]
            results.append({"query": query, "items": items})
        return ok(results, "arxiv")
    except Exception as e:
        return err("arxiv", e)

# ---------------- Weather / Calendar / Bookmarks / Gmail ----------------

def get_weather():
    url = "https://api.open-meteo.com/v1/forecast?latitude=42.3314&longitude=-83.0458&current=temperature_2m,apparent_temperature,weather_code&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
    try:
        r = session.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
        current = data.get("current", {})
        daily = data.get("daily", {})
        return ok([{
            "temperature": current.get("temperature_2m"),
            "feels_like": current.get("apparent_temperature"),
            "weather_code": current.get("weather_code"),
            "today_max": (daily.get("temperature_2m_max") or [None])[0],
            "today_min": (daily.get("temperature_2m_min") or [None])[0]
        }], url)
    except Exception as e:
        return err(url, e)

def get_calendar():
    agenda_file = "agenda.json"
    if os.path.exists(agenda_file):
        try:
            with open(agenda_file, "r", encoding="utf-8") as f:
                return ok(json.load(f), agenda_file)
        except Exception as e:
            return err(agenda_file, e)
    return ok([], agenda_file)

def get_bookmarks():
    path = "bookmarks.json"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return ok(json.load(f), path)
        except Exception as e:
            return err(path, e)

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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sample, f, indent=2)
    return ok(sample, path)

def get_gmail_links():
    return ok([
        {"title": "Important in last 24 hours", "url": "https://mail.google.com/mail/u/0/#search/is%3Aimportant+newer_than%3A1d"},
        {"title": "Unread starred", "url": "https://mail.google.com/mail/u/0/#search/is%3Aunread+is%3Astarred"}
    ], "manual")

# ---------------- Dashboard ----------------

def update_dashboard():
    print(f"[{time.strftime('%H:%M:%S')}] Syncing data...")
    existing = load_existing_data()

    fresh = {
        "ai_leaderboard": get_ai_leaderboard(),
        "ai_free_tier": get_free_tier_ai_leaderboard(),
        "reddit_user": get_reddit_user_updates(),
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

    final = {"last_updated": fresh["last_updated"]}
    for key, value in fresh.items():
        if key == "last_updated":
            continue
        final[key] = preserve_previous_if_failed(existing, key, value)

    save_data(final)
    print("Saved data.json")

if __name__ == "__main__":
    update_dashboard()
