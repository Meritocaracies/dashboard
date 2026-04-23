import os
import re
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

session = requests.Session()
session.headers.update({
    "User-Agent": "AI-Bio-Command-Center/1.0",
    "Accept": "application/json, application/xml;q=0.9, text/html;q=0.8"
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

def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return migrate_legacy_data(data)
        except Exception as e:
            print(f"Failed to load existing data: {e}")
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def preserve_previous_if_failed(existing, key, fresh):
    if fresh.get("status") == "ok":
        return fresh

    if key not in existing:
        return fresh

    prev = existing[key]

    if isinstance(prev, dict) and "items" in prev:
        prev = dict(prev)
        prev["stale"] = True
        prev["stale_reason"] = fresh.get("error", "unknown error")
        return prev

    return {
        "status": "ok",
        "items": prev if isinstance(prev, list) else [prev],
        "source": "legacy_data.json",
        "retrieved_at": now_iso(),
        "stale": True,
        "stale_reason": fresh.get("error", "unknown error"),
        "meta": {"migrated_from_legacy_format": True}
    }

def fetch_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DashboardBot/1.0)"
    }
    res = session.get(url, headers=headers, timeout=25)
    res.raise_for_status()
    return res.text


# -------------------------
# Artificial Analysis / AI
# -------------------------

def fetch_aa_models():
    api_key = os.getenv("ARTIFICIAL_ANALYSIS_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ARTIFICIAL_ANALYSIS_API_KEY")

    url = "https://artificialanalysis.ai/api/v2/data/llms/models"
    res = session.get(url, headers={"x-api-key": api_key}, timeout=30)
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
            "display_score": round(m["score"]),
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
            "display_score": round(m["score"]),
            "price_blended": m["price_blended"],
        } for m in items[:limit]]

        return ok(top, source, meta={"filter": "free_tier_allowlist_or_zero_price"})
    except Exception as e:
        return err("https://artificialanalysis.ai/api/v2/data/llms/models", e)


# -------------------------
# Reddit
# -------------------------

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

def get_reddit_user_comments(username="Drwillpowers", limit=5):
    rss_url = f"https://www.reddit.com/user/{username}/comments.rss"
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
            updated_tag = entry.find("updated")
            content_tag = entry.find("content")

            title = title_tag.text.strip() if title_tag else "Untitled"
            link = link_tag.get("href", "#") if link_tag else "#"
            updated = updated_tag.text.strip() if updated_tag else None

            snippet = ""
            if content_tag:
                snippet_html = content_tag.text.strip()
                snippet_soup = BeautifulSoup(snippet_html, "html.parser")
                snippet = snippet_soup.get_text(" ", strip=True)
                snippet = snippet[:220] + ("..." if len(snippet) > 220 else "")

            items.append({
                "title": title,
                "link": link,
                "updated": updated,
                "snippet": snippet
            })

            if len(items) >= limit:
                break

        return ok(items, rss_url, meta={"entry_count": len(entries)})
    except Exception as e:
        return err(rss_url, e)


# -------------------------
# WGS 30x watchlist
# -------------------------

def extract_prices_from_text(text):
    matches = re.findall(r'\$\s?(\d+(?:\.\d{1,2})?)', text or "")
    prices = []
    for m in matches:
        try:
            prices.append(float(m))
        except ValueError:
            pass
    return sorted(set(prices))

def extract_prices_from_json_ld(html):
    """
    Parse JSON-LD blocks for offers.price fields.
    """
    prices = []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            stack = data if isinstance(data, list) else [data]
            while stack:
                obj = stack.pop()
                if isinstance(obj, dict):
                    # direct price
                    if "price" in obj:
                        try:
                            prices.append(float(obj["price"]))
                        except Exception:
                            pass
                    # nested offers
                    if "offers" in obj:
                        offers = obj["offers"]
                        if isinstance(offers, list):
                            stack.extend(offers)
                        else:
                            stack.append(offers)
                    # recurse all values
                    for v in obj.values():
                        if isinstance(v, (dict, list)):
                            stack.append(v)
                elif isinstance(obj, list):
                    stack.extend(obj)
        except Exception:
            continue
    return sorted(set(prices))

def extract_prices_from_meta(html):
    """
    Extract possible price values from meta tags and common attributes.
    """
    prices = []
    soup = BeautifulSoup(html, "html.parser")

    meta_candidates = []
    for tag in soup.find_all("meta"):
        for attr in ("content", "value"):
            val = tag.get(attr)
            if val:
                meta_candidates.append(val)

    for value in meta_candidates:
        matches = re.findall(r'(\d+(?:\.\d{1,2})?)', value)
        for m in matches:
            try:
                p = float(m)
                if 50 <= p <= 2000:
                    prices.append(p)
            except Exception:
                pass

    return sorted(set(prices))

def choose_best_price(prices, expected=None, low=100, high=2000):
    """
    Prefer plausible prices and, if expected is known, choose the closest one.
    """
    candidates = sorted(set(p for p in prices if low <= p <= high))
    if not candidates:
        return None

    if expected is None:
        return min(candidates)

    return min(candidates, key=lambda p: abs(p - expected))

def gather_price_candidates(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    prices = []
    prices.extend(extract_prices_from_json_ld(html))
    prices.extend(extract_prices_from_meta(html))
    prices.extend(extract_prices_from_text(text))

    # also scan raw html for simple numeric price patterns
    raw_matches = re.findall(r'["\']price["\']\s*[:=]\s*["\']?(\d+(?:\.\d{1,2})?)', html, re.IGNORECASE)
    for m in raw_matches:
        try:
            prices.append(float(m))
        except Exception:
            pass

    return sorted(set(prices))

def normalize_wgs_result(provider, url, price, notes="", status="ok", candidates=None):
    return {
        "provider": provider,
        "product_name": "30x Whole Genome Sequencing",
        "coverage": "30x",
        "price_usd": price,
        "display_price": f"${int(price)}" if isinstance(price, (int, float)) else "Unknown",
        "url": url,
        "notes": notes,
        "status": status,
        "candidates": candidates or []
    }

def scrape_wgs_provider(provider, url, expected_price, notes):
    try:
        html = fetch_html(url)
        candidates = gather_price_candidates(html)
        print(f"[WGS] {provider} candidate prices: {candidates[:20]}")

        price = choose_best_price(candidates, expected=expected_price)

        if price is None:
            raise RuntimeError("No plausible price found")

        return normalize_wgs_result(
            provider=provider,
            url=url,
            price=price,
            notes=notes,
            status="ok",
            candidates=candidates[:10]
        )
    except Exception as e:
        return normalize_wgs_result(
            provider=provider,
            url=url,
            price=None,
            notes=f"Failed to scrape: {e}",
            status="error",
            candidates=[]
        )

def get_umn_wgs_price():
    return scrape_wgs_provider(
        provider="UMN Genomics",
        url="https://genomics.umn.edu/service/human-whole-genome-sequencing",
        expected_price=199,
        notes="Human whole genome sequencing page"
    )

def get_tellmegen_wgs_price():
    return scrape_wgs_provider(
        provider="tellmeGen",
        url="https://shop.tellmegen.com/en/products/ultra-wgs-dna-kit",
        expected_price=299,
        notes="Ultra WGS DNA Kit"
    )

def get_sequencing_com_wgs_price():
    return scrape_wgs_provider(
        provider="Sequencing.com",
        url="https://sequencing.com/order/special-dna-day-wgs-bundle",
        expected_price=379,
        notes="Special DNA Day WGS bundle"
    )

def get_dantelabs_wgs_price():
    return scrape_wgs_provider(
        provider="Dante Labs",
        url="https://dantelabs.com/genome/",
        expected_price=449,
        notes="Genome product page"
    )

def get_wgs_prices():
    source = "tracked 30x WGS provider pages"
    try:
        items = [
            get_umn_wgs_price(),
            get_tellmegen_wgs_price(),
            get_sequencing_com_wgs_price(),
            get_dantelabs_wgs_price(),
        ]

        valid = [x for x in items if x.get("price_usd") is not None]
        invalid = [x for x in items if x.get("price_usd") is None]

        valid.sort(key=lambda x: x["price_usd"])
        sorted_items = valid + invalid

        cheapest = valid[0]["provider"] if valid else None

        return ok(sorted_items, source, meta={
            "coverage": "30x",
            "cheapest_provider": cheapest
        })
    except Exception as e:
        return err(source, e)

# -------------------------
# PubMed
# -------------------------

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


# -------------------------
# arXiv
# -------------------------

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


# -------------------------
# Weather / Calendar / Bookmarks / Gmail
# -------------------------

def get_weather():
    url = "https://api.open-meteo.com/v1/forecast?latitude=34.0522&longitude=-118.2437&current=temperature_2m,apparent_temperature,weather_code&daily=temperature_2m_max,temperature_2m_min&timezone=auto"
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


# -------------------------
# Dashboard updater
# -------------------------

def update_dashboard():
    print(f"[{time.strftime('%H:%M:%S')}] Syncing data...")
    print("AA key present in runtime:", bool(os.getenv("ARTIFICIAL_ANALYSIS_API_KEY")))
    existing = load_existing_data()

    fresh = {
        "ai_leaderboard": get_ai_leaderboard(limit=5),
        "ai_free_tier": get_free_tier_ai_leaderboard(limit=5),
        "reddit_user": get_reddit_user_comments(limit=5),
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
