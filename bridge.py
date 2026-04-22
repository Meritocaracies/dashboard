import os
import json
import time
import requests
import feedparser
from datetime import datetime, timezone
from urllib.parse import quote_plus
from dotenv import load_dotenv

load_dotenv()

DATA_FILE = "data.json"
ARTIFICIAL_ANALYSIS_API_KEY = os.getenv("ARTIFICIAL_ANALYSIS_API_KEY")

session = requests.Session()
session.headers.update({
    "User-Agent": "AI-Bio-Command-Center/1.0",
    "Accept": "application/json"
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

def fetch_aa_models():
    if not ARTIFICIAL_ANALYSIS_API_KEY:
        raise RuntimeError("Missing ARTIFICIAL_ANALYSIS_API_KEY")

    url = "https://artificialanalysis.ai/api/v2/data/llms/models"
    headers = {
        "x-api-key": ARTIFICIAL_ANALYSIS_API_KEY
    }

    res = session.get(url, headers=headers, timeout=30)
    res.raise_for_status()
    payload = res.json()

    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected API response shape")

    rows = payload.get("data", [])
    if not isinstance(rows, list):
        raise RuntimeError("API 'data' field is not a list")

    return url, rows

def normalize_ai_model(row):
    evaluations = row.get("evaluations") or {}
    pricing = row.get("pricing") or {}
    creator = row.get("model_creator") or {}

    intelligence = evaluations.get("artificial_analysis_intelligence_index")
    coding = evaluations.get("artificial_analysis_coding_index")
    math_idx = evaluations.get("artificial_analysis_math_index")

    return {
        "id": row.get("id"),
        "model": row.get("name"),
        "slug": row.get("slug"),
        "provider": creator.get("name"),
        "release_date": row.get("release_date"),
        "score": intelligence,
        "coding_index": coding,
        "math_index": math_idx,
        "price_input": pricing.get("price_1m_input_tokens"),
        "price_output": pricing.get("price_1m_output_tokens"),
        "price_blended": pricing.get("price_1m_blended_3_to_1"),
        "speed_tps": row.get("median_output_tokens_per_second"),
        "ttft": row.get("median_time_to_first_token_seconds"),
    }

def get_ai_leaderboard(limit=10):
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

        top = []
        for m in items[:limit]:
            top.append({
                "model": m["model"],
                "provider": m["provider"],
                "score": m["score"],
                "release_date": m["release_date"],
                "price_blended": m["price_blended"]
            })

        return ok(top, source, meta={"ranking_field": "evaluations.artificial_analysis_intelligence_index"})
    except Exception as e:
        return err("https://artificialanalysis.ai/api/v2/data/llms/models", e)
