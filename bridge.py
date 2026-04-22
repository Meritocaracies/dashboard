import requests
from bs4 import BeautifulSoup
import json
import time

def get_ai_leaderboard():
    """Simulates real-time intelligence rankings based on Artificial Analysis trends."""
    # Note: Scrapers often need a User-Agent to look like a browser
    return [
        {"model": "Claude 3.5 Sonnet", "elo": "1271", "status": "Best Overall"},
        {"model": "GPT-4o", "elo": "1252", "status": "Leader"},
        {"model": "Gemini 1.5 Pro", "elo": "1245", "status": "Strong"},
        {"model": "Llama 3 70B", "elo": "1210", "status": "Top Open"},
        {"model": "Claude 3 Opus", "elo": "1200", "status": "Quality"}
    ]

def get_reddit_updates():
    """Pulls the latest posts, filtering out common sticky posts if possible."""
    rss_url = "https://www.reddit.com/user/Drwillpowers/submitted.rss"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(rss_url, headers=headers)
        soup = BeautifulSoup(res.content, "xml")
        items = soup.find_all("entry")
        
        results = []
        for i in items:
            title = i.title.text
            # Skip the specific 'stop messaging me' post if it appears
            if "personal messages" in title.lower():
                continue
            results.append({"title": title, "link": i.link['href']})
            if len(results) >= 4: break
        return results
    except:
        return [{"title": "Reddit unreachable", "link": "#"}]

def update_dashboard():
    print(f"[{time.strftime('%H:%M:%S')}] Syncing data...")
    data = {
        "ai_leaderboard": get_ai_leaderboard(),
        "reddit": get_reddit_updates(),
        "wgs_price": [
            {"provider": "Element Vitari", "price": "$100", "note": "Targeting 2026"},
            {"provider": "Nebula", "price": "$249", "note": "Current Sale"},
            {"provider": "Sequencing.com", "price": "$379", "note": "Clinical Grade"}
        ],
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open('data.json', 'w') as f:
        json.dump(data, f)

if __name__ == "__main__":
    while True:
        update_dashboard()
        time.sleep(3600) # Wait 1 hour