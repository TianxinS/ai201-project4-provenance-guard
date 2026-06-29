from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import uuid

import config  # loads .env at import time
from llm_signal import llm_score
from stylometric import stylometric_score
from scoring import compute_confidence
from labels import generate_label
from audit import log_submission, log_appeal, get_log, get_submission

app = Flask(__name__)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    """
    Accepts a piece of text and a creator_id, runs the two detection signals,
    computes a confidence score, generates a transparency label, logs everything
    to the audit database, and returns the full classification result.

    Request JSON:
        { "text": str, "creator_id": str }

    Response JSON (200):
        {
            "content_id": str,
            "attribution": str,
            "confidence": float,
            "label": str,
            "signal_scores": {
                "llm_score": float,
                "stylometric_score": float,
                "stylometric_metrics": { ... }
            }
        }

    Errors:
        400 — missing fields or text too short
        429 — rate limit exceeded
    """
    data = request.get_json()
    if not data or "text" not in data or "creator_id" not in data:
        return jsonify({"error": "Missing required fields: text, creator_id"}), 400

    text = data["text"]
    creator_id = data["creator_id"]

    if len(text.strip()) < 20:
        return jsonify({"error": "Text too short for analysis (minimum 20 characters)"}), 400

    content_id = str(uuid.uuid4())

    ls = llm_score(text)
    ss_result = stylometric_score(text)
    ss = ss_result["score"]

    result = compute_confidence(ls, ss)
    label = generate_label(result["confidence"], result["attribution"])

    log_submission(
        content_id,
        creator_id,
        text,
        result["attribution"],
        result["confidence"],
        ls,
        ss,
        label,
    )

    return jsonify(
        {
            "content_id": content_id,
            "attribution": result["attribution"],
            "confidence": round(result["confidence"], 4),
            "label": label,
            "signal_scores": {
                "llm_score": round(ls, 4),
                "stylometric_score": round(ss, 4),
                "stylometric_metrics": ss_result["metrics"],
            },
        }
    )


@app.route("/appeal", methods=["POST"])
def appeal():
    """
    Logs a creator's appeal against an AI classification.

    Request JSON:
        { "content_id": str, "creator_reasoning": str }

    Response JSON (200):
        { "message": str, "content_id": str, "status": "under_review" }

    Errors:
        400 — missing fields
        404 — content_id not found
    """
    data = request.get_json()
    if not data or "content_id" not in data or "creator_reasoning" not in data:
        return jsonify({"error": "Missing required fields: content_id, creator_reasoning"}), 400

    content_id = data["content_id"]
    submission = get_submission(content_id)
    if not submission:
        return jsonify({"error": "content_id not found"}), 404

    log_appeal(content_id, data["creator_reasoning"])

    return jsonify(
        {
            "message": "Appeal received and is under review.",
            "content_id": content_id,
            "status": "under_review",
        }
    )


@app.route("/log", methods=["GET"])
def log():
    """
    Returns the most recent classification and appeal entries from the audit log.

    Optional query parameter:
        limit (int, default 20) — maximum number of entries to return

    Response JSON:
        { "entries": [ ... ], "count": int }
    """
    try:
        limit = int(request.args.get("limit", 20))
        limit = max(1, min(limit, 200))
    except (ValueError, TypeError):
        limit = 20

    entries = get_log(limit=limit)
    return jsonify({"entries": entries, "count": len(entries)})


if __name__ == "__main__":
    app.run(debug=True)
