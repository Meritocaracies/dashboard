import requests
from bs4 import BeautifulSoup
import json
import time
import os
import re
from datetime import datetime

DATA_FILE = "data.json"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})

def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load existing data.json: {e}")
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_ai_leaderboard():
    """
    Try to fetch live leaderboard data.
    Replace AI_URL with the actual page you want.
    """
    AI_URL = "https://example.com/leaderboard"

    try:
        res = session.get(AI_URL, timeout=20)
        res.raise_for_status()

        html = res.text

        # Example strategy 1: parse visible table rows
        soup = BeautifulSoup(html, "html.parser")

        leaderboard = []

        # Replace selectors below with real selectors after inspecting the site
        rows = soup.select("table tr")
        for row in rows[1:]:
            cols = row.find_all(["td", "th"])
            if len(cols) >= 2:
                model = cols[0].get_text(" ", strip=True)
                score = cols[1].get_text(" ", strip=True)
                if model and score:
                    leaderboard.append({
                        "model": model,
                        "score": score
                    })

        if leaderboard:
            return {
                "items": leaderboard[:10],
                "status": "ok",
                "source": AI_URL,
                "retrieved_at": datetime.utcnow().isoformat() + "Z"
            }

        # Example strategy 2: hunt embedded JSON in scripts
        scripts = soup.find_all("script")
        for script in scripts:
            script_text = script.get_text(" ", strip=False)
            if "leaderboard" in script_text.lower():
                # You could inspect and regex JSON blobs from here
                pass

        raise Exception("No leaderboard rows found")

    except Exception as e:
        return {
            "items": [],
            "status": "error",
            "error": str(e),
            "source": AI_URL,
            "retrieved_at": datetime.utcnow().isoformat() + "Z"
        }

def get_reddit_updates():
    rss_url = "https://www.reddit.com/user/Drwillpowers/submitted.rss"
    try:
        res = session.get(rss_url, timeout=15)
        res.raise_for_status()

        soup = BeautifulSoup(res.content, "xml")
        items = soup.find_all("entry")

        results = []
        for i in items:
            title = i.title.text.strip() if i.title else "Untitled"
            if "personal messages" in title.lower():
                continue

            link_tag = i.find("link")
            link = link_tag.get("href", "#") if link_tag else "#"

            results.append({
                "title": title,
                "link": link
            })
            if len(results) >= 4:
                break

        return {
            "items": results,
            "status": "ok",
            "source": rss_url,
            "retrieved_at": datetime.utcnow().isoformat() + "Z"
        }

    except Exception as e:
        return {
            "items": [],
            "status": "error",
            "error": str(e),
            "source": rss_url,
            "retrieved_at": datetime.utcnow().isoformat() + "Z"
        }

def get_wgs_prices():
    # Still static for now, but structured the same way
    return {
        "items": [
            {"provider": "Element Vitari", "price": "$100", "note": "Targeting 2026"},
            {"provider": "Nebula", "price": "$249", "note": "Current Sale"},
            {"provider": "Sequencing.com", "price": "$379", "note": "Clinical Grade"}
        ],
        "status": "ok",
        "source": "manual",
        "retrieved_at": datetime.utcnow().isoformat() + "Z"
    }

def update_dashboard():
    print(f"[{time.strftime('%H:%M:%S')}] Syncing data...")

    existing = load_existing_data()

    ai = get_ai_leaderboard()
    if ai["status"] != "ok" and "ai_leaderboard" in existing:
        print("AI leaderboard fetch failed; preserving previous data.")
        ai = existing["ai_leaderboard"]

    reddit = get_reddit_updates()
    if reddit["status"] != "ok" and "reddit" in existing:
        print("Reddit fetch failed; preserving previous data.")
        reddit = existing["reddit"]

    wgs = get_wgs_prices()

    data = {
        "ai_leaderboard": ai,
        "reddit": reddit,
        "wgs_price": wgs,
        "last_updated": datetime.utcnow().isoformat() + "Z"
    }

    save_data(data)
    print("Dashboard data saved.")

if __name__ == "__main__":
    while True:
        update_dashboard()
        time.sleep(3600)
