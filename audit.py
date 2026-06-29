import json
import os
import uuid
from datetime import datetime, timezone
from config import LOG_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append(entry: dict) -> None:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def log_submission(content_id, creator_id, text, attribution, confidence, llm_score, stylometric_score, label):
    entry = {
        "type": "submission",
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": _now(),
        "text_snippet": text[:200],
        "attribution": attribution,
        "confidence": round(confidence, 4),
        "llm_score": round(llm_score, 4),
        "stylometric_score": round(stylometric_score, 4),
        "label": label,
        "status": "classified",
    }
    _append(entry)
    print(f'[LOGGED] {entry["timestamp"]} | {attribution} | confidence={confidence:.2f} | "{text[:60]}..."')


def log_appeal(content_id, creator_reasoning):
    entry = {
        "type": "appeal",
        "appeal_id": str(uuid.uuid4()),
        "content_id": content_id,
        "creator_reasoning": creator_reasoning,
        "timestamp": _now(),
        "status": "under_review",
    }
    _append(entry)
    print(f'[APPEAL] {entry["timestamp"]} | content_id={content_id}')


def get_log(limit=20):
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = []
    for line in reversed(lines):
        line = line.strip()
        if line:
            entries.append(json.loads(line))
        if len(entries) >= limit:
            break
    return entries


def get_submission(content_id):
    if not os.path.exists(LOG_FILE):
        return None
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entry = json.loads(line)
                if entry.get("type") == "submission" and entry.get("content_id") == content_id:
                    return entry
    return None
