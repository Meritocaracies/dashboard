import requests
from bs4 import BeautifulSoup
import json
import time
import os

def get_ai_leaderboard():
    # April 2026 Live Rankings from Artificial Analysis
    return [
        {"model": "Gemini 3.1 Pro", "elo": "94.4", "status": "Leader"},
        {"model": "GPT-5.4", "elo": "93.8", "status": "Strong"},
        {"model": "Claude 4.6 Opus", "elo": "93.4", "status": "Quality"},
        {"model": "GPT-5.3 Codex", "elo": "89.8", "status": "Coding"},
        {"model": "Qwen 3.6 Plus", "elo": "84.8", "status": "Rising"}
    ]

def get_reddit_updates():
    # Use a specific User-Agent to avoid the 429 Error
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Dashboard/1.0'}
    url = "https://www.reddit.com/user/Drwillpowers/submitted.rss"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "xml")
        entries = soup.find_all("entry")
        
        posts = []
        for e in entries[:4]:
            title = e.title.text
            if "personal messages" in title.lower(): continue
            posts.append({"title": title, "link": e.link['href']})
        return posts
    except Exception as e:
        print(f"Reddit Error: {e}")
        return [{"title": "Feed currently unavailable", "link": "#"}]

def update_dashboard():
    print("Syncing latest data...")
    data = {
        "ai_leaderboard": get_ai_leaderboard(),
        "reddit": get_reddit_updates(),
        "wgs_price": [
            {"provider": "Element VITARI", "price": "$100", "note": "Announced Feb 2026"},
            {"provider": "Ultima UG200", "price": "$100", "note": "High-throughput"},
            {"provider": "Nebula", "price": "$249", "note": "Consumer Leader"}
        ],
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open('data.json', 'w') as f:
        json.dump(data, f)

if __name__ == "__main__":
    update_dashboard()
