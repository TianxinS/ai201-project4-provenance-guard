# Provenance Guard

## What It Does

Provenance Guard is an AI-text detection API that classifies submitted text as likely AI-generated, likely human-written, or origin uncertain. It combines two independent signals — a Groq-hosted LLM that estimates AI authorship probability and a suite of pure-Python stylometric heuristics — to produce a weighted confidence score and a human-readable transparency label for every submission. Every classification, including all signal scores, is persisted to a local SQLite audit log.

The system is designed to be transparent and fair. Any creator who disputes their classification can file an appeal through a dedicated endpoint, which flags the submission for human review and records their reasoning. A GET endpoint exposes the full audit log — including appeal details — so human reviewers always have the evidence they need to make a final determination. Rate limiting (10 requests/minute, 100 requests/day) prevents abuse while keeping the API usable for normal workflows.

## Architecture

```
Submission flow:

POST /submit
     │
     ▼
[Flask: validate fields & length]
     │
     ├──────────────────────────────────────┐
     ▼                                      ▼
[LLM Signal — signals/llm_signal.py]   [Stylometric Signal — signals/stylometric.py]
 Groq llama-3.1-8b-instant               sentence_uniformity
 prompt → decimal float                  formality_score
     │                                   avg_word_length_score
     │  llm_score (0-1)                      │  sty_score (0-1)
     └──────────────┬───────────────────────┘
                    ▼
        [Confidence Scorer — scoring.py]
         combined = 0.6*llm + 0.4*sty
         attribution → "likely_ai" | "uncertain" | "likely_human"
                    │
                    ▼
        [Label Generator — labels.py]
         select one of 3 label templates
                    │
                    ▼
        [Audit Log — audit.py → SQLite]
                    │
                    ▼
             JSON Response


Appeal flow:

POST /appeal → [Validate content_id] → [Update status: under_review] → [Audit Log] → JSON confirmation
```

A submitted text travels through two independent signal modules in sequence, their scores are blended by the confidence scorer (60% LLM weight, 40% stylometric weight), the result is converted into a transparency label, everything is written to SQLite, and the classification JSON is returned. Appeals are a separate, simpler flow: validate the content_id exists, mark it under review, log the creator's reasoning, and return confirmation.

## Detection Signals

### Signal 1: LLM-Based Attribution

**What it measures:** The semantic and stylistic fingerprint of the text as interpreted by a large language model (`llama-3.1-8b-instant` via Groq). The model has been exposed to vast quantities of both human and AI text during training, giving it an implicit understanding of register, phrasing patterns, and structural habits that differ between the two.

**Why it works:** AI models tend to produce confident, well-structured sentences with balanced clause lengths, precise vocabulary, and an absence of conversational self-correction. Human writing tends to be more irregular: tonal shifts, personal asides, incomplete thoughts, and idiomatic expressions that a language model would be unlikely to generate unprompted.

**Output format:** A float in [0.0, 1.0] where 0.0 = certainly human-written and 1.0 = certainly AI-generated. Returns 0.5 as a neutral fallback on any API error.

---

### Signal 2: Stylometric Heuristics

**What it measures:** Three pure-Python metrics that capture structural and lexical patterns associated with AI writing, computed entirely locally with no external API calls.

**Metric 1 — Sentence Uniformity (weight 40%):** Computes the coefficient of variation (CV = std/mean) of sentence lengths in words. A low CV means sentences are similarly sized, which is characteristic of AI output. Score = `max(0, 1 - CV)`. Higher score = more uniform = more AI-like.

**Metric 2 — Formality Score (weight 35%):** Counts contractions (don't, I've, can't, …) and informal markers (lol, gonna, idk, …) in the text. `formality_score = 1 - (count / max(1, word_count) * 20)`, clamped to [0, 1]. AI models rarely use informal language unprompted, so high formality correlates with AI authorship.

**Metric 3 — Average Word Length Score (weight 25%):** Computes the average character length of words. `score = min(1, max(0, (avg_word_len - 3.5) / 3.5))`. AI models tend toward longer, more precise vocabulary (technical terms, polysyllabic adjectives); casual human writing skews shorter.

**Output format:** `{"score": float, "metrics": {"sentence_uniformity": float, "formality_score": float, "avg_word_length_score": float}}`

**Why each metric differs:** AI models are fine-tuned on instruction-following corpora that reward formality, precision, and structural consistency. Human writers — especially in informal contexts — are not optimizing for those properties.

## Confidence Scoring

The two raw scores are combined with a weighted average:

```
confidence = 0.6 × llm_score + 0.4 × stylometric_score
```

The LLM signal receives higher weight because it captures semantic patterns that the heuristics cannot. The stylometric signal acts as a verification layer that is immune to Groq API outages.

**Thresholds:**
| Confidence Range | Attribution | Meaning |
|-----------------|-------------|---------|
| > 0.68 | `likely_ai` | Both signals agree on strong AI indicators |
| 0.38 – 0.68 | `uncertain` | Signals are split or individually weak |
| < 0.38 | `likely_human` | Both signals show clear human markers |

The thresholds are intentionally asymmetric: the `likely_ai` bar is high (0.68) to minimize false positives against human creators, while the wide uncertain band (0.38–0.68) routes ambiguous cases to human review rather than making an overconfident determination.

---

### Example Submissions

**Example 1 — Clearly AI-Generated (formal essay paragraph)**

Input:
```json
{
  "text": "The implementation of advanced machine learning algorithms represents a paradigm shift in contemporary data science methodology. These sophisticated computational frameworks enable practitioners to extract meaningful insights from vast repositories of structured and unstructured information, thereby facilitating evidence-based decision-making processes across multiple domains.",
  "creator_id": "user_demo_1"
}
```

Expected output:
```json
{
  "content_id": "a3f2e1d0-...",
  "attribution": "likely_ai",
  "confidence": 0.8412,
  "label": "Likely AI-Generated — This content shows strong indicators of AI authorship (confidence: 84%). Our analysis detected uniform sentence structure and formal register patterns characteristic of large language models.",
  "signal_scores": {
    "llm_score": 0.9,
    "stylometric_score": 0.73,
    "stylometric_metrics": {
      "sentence_uniformity": 0.82,
      "formality_score": 1.0,
      "avg_word_length_score": 0.92
    }
  }
}
```

**Example 2 — Clearly Human-Written (informal personal message)**

Input:
```json
{
  "text": "omg i can't believe i forgot to bring my charger AGAIN lol. gonna have to borrow one from sarah idk if she'll be annoyed tbh but whatever i'm desperate rn. this is like the 3rd time this week i'm so bad at this.",
  "creator_id": "user_demo_2"
}
```

Expected output:
```json
{
  "content_id": "b7c4a2f1-...",
  "attribution": "likely_human",
  "confidence": 0.1380,
  "label": "Likely Human-Written — This content shows characteristics consistent with human authorship (confidence: 14% AI probability). Our signals detected natural variation in style and informal language patterns.",
  "signal_scores": {
    "llm_score": 0.05,
    "stylometric_score": 0.27,
    "stylometric_metrics": {
      "sentence_uniformity": 0.42,
      "formality_score": 0.0,
      "avg_word_length_score": 0.18
    }
  }
}
```

## Transparency Label Variants

**likely_ai** — triggers when confidence > 0.68:
> "Likely AI-Generated — This content shows strong indicators of AI authorship (confidence: 84%). Our analysis detected uniform sentence structure and formal register patterns characteristic of large language models."

**likely_human** — triggers when confidence < 0.38:
> "Likely Human-Written — This content shows characteristics consistent with human authorship (confidence: 14% AI probability). Our signals detected natural variation in style and informal language patterns."

**uncertain** — triggers when confidence is between 0.38 and 0.68:
> "Origin Uncertain — Our analysis returned mixed signals for this content (AI probability: 54%). Some indicators suggest AI involvement, but the evidence is not conclusive."

## API Endpoints

### POST /submit

Classifies text and logs the result.

**Request:**
```json
{
  "text": "string (minimum 20 characters)",
  "creator_id": "string"
}
```

**Response (200):**
```json
{
  "content_id": "uuid-string",
  "attribution": "likely_ai | uncertain | likely_human",
  "confidence": 0.8412,
  "label": "Likely AI-Generated — ...",
  "signal_scores": {
    "llm_score": 0.9,
    "stylometric_score": 0.73,
    "stylometric_metrics": {
      "sentence_uniformity": 0.82,
      "formality_score": 1.0,
      "avg_word_length_score": 0.92
    }
  }
}
```

**curl example:**
```bash
curl -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "The utilization of machine learning frameworks enables practitioners to extract meaningful insights from large datasets, thereby facilitating evidence-based decision making across numerous professional domains.", "creator_id": "user123"}'
```

**Error responses:**
- `400` — missing `text` or `creator_id` field, or text shorter than 20 characters
- `429` — rate limit exceeded (10/minute or 100/day)

---

### POST /appeal

Files a creator appeal against an existing classification.

**Request:**
```json
{
  "content_id": "uuid-string",
  "creator_reasoning": "string explaining why the classification is incorrect"
}
```

**Response (200):**
```json
{
  "message": "Appeal received and is under review.",
  "content_id": "uuid-string",
  "status": "under_review"
}
```

**curl example:**
```bash
curl -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{
    "content_id": "a3f2e1d0-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "creator_reasoning": "This is my original research paper. I wrote it over three months and can provide revision history from my university Google Drive."
  }'
```

**Error responses:**
- `400` — missing `content_id` or `creator_reasoning`
- `404` — `content_id` not found in audit log

---

### GET /log

Returns recent audit log entries, optionally limited.

**Query parameters:**
- `limit` (integer, optional, default 20, max 200) — number of entries to return

**Response (200):**
```json
{
  "entries": [
    {
      "content_id": "uuid",
      "creator_id": "user123",
      "timestamp": "2026-06-28T22:45:01.123456+00:00",
      "text_snippet": "First 200 characters of submitted text...",
      "attribution": "likely_ai",
      "confidence": 0.8412,
      "llm_score": 0.9,
      "stylometric_score": 0.73,
      "label": "Likely AI-Generated — ...",
      "status": "classified",
      "appeal_id": null,
      "creator_reasoning": null,
      "appeal_timestamp": null
    }
  ],
  "count": 1
}
```

**curl example:**
```bash
curl "http://localhost:5000/log?limit=5"
```

## Rate Limiting

**Chosen limits:** 10 requests per minute, 100 requests per day (per IP address, in-memory storage).

**Reasoning:** The `/submit` endpoint makes a Groq API call for every request, which has both cost and latency implications. Ten requests per minute gives a single user approximately one classification every 6 seconds — sufficient for genuine review workflows (uploading and checking student essays, reviewing a content batch) while preventing programmatic scraping or stress testing. The 100 per day limit caps total Groq API usage to a manageable level during development. The `/appeal` and `/log` endpoints are not rate-limited because appeals are infrequent by nature and the log endpoint is read-only.

**Rate limit behavior — example output:**

```
# First 10 requests within a minute → 200 OK
$ curl -X POST http://localhost:5000/submit -H "Content-Type: application/json" \
  -d '{"text": "Test text for rate limiting demonstration.", "creator_id": "tester"}'
# → 200 { "content_id": ..., "attribution": ... }

# 11th request within the same minute → 429 Too Many Requests
$ curl -X POST http://localhost:5000/submit -H "Content-Type: application/json" \
  -d '{"text": "Test text for rate limiting demonstration.", "creator_id": "tester"}'
# → 429 { "error": "10 per 1 minute" }
```

## Audit Log

Each log entry represents a single submission joined with its appeal record (if any). When no appeal has been filed, `appeal_id`, `creator_reasoning`, and `appeal_timestamp` are `null`.

**Sample log entry (with appeal):**
```json
{
  "content_id": "a3f2e1d0-5c71-4b2e-9a10-12345678abcd",
  "creator_id": "user_alice",
  "timestamp": "2026-06-28T22:45:01.123456+00:00",
  "text_snippet": "The implementation of advanced machine learning algorithms represents a paradigm shift in contemporary data science methodology...",
  "attribution": "likely_ai",
  "confidence": 0.8412,
  "llm_score": 0.9,
  "stylometric_score": 0.73,
  "label": "Likely AI-Generated — This content shows strong indicators of AI authorship (confidence: 84%)...",
  "status": "under_review",
  "appeal_id": "f7a1b2c3-d4e5-6789-abcd-ef0123456789",
  "creator_reasoning": "This is my original research. I wrote it myself over two months.",
  "appeal_timestamp": "2026-06-28T23:10:44.987654+00:00"
}
```

**Field reference:**
| Field | Meaning |
|-------|---------|
| `content_id` | UUID assigned at submission time; used for appeals |
| `creator_id` | Identifier provided by the submitting creator |
| `timestamp` | UTC ISO-8601 timestamp of the original submission |
| `text_snippet` | First 200 characters of the submitted text |
| `attribution` | Classification: `likely_ai`, `uncertain`, or `likely_human` |
| `confidence` | Weighted combined score in [0, 1] |
| `llm_score` | Raw output from the Groq LLM signal |
| `stylometric_score` | Raw output from the stylometric heuristics |
| `label` | Full human-readable transparency label text |
| `status` | `classified` (initial) or `under_review` (after appeal) |
| `appeal_id` | UUID of the appeal record (null if no appeal) |
| `creator_reasoning` | Creator's free-text appeal explanation (null if no appeal) |
| `appeal_timestamp` | UTC timestamp of the appeal submission (null if no appeal) |

## Known Limitations

1. **Formal human writing scores as AI-like.** Academic prose, legal documents, and technical reports written by humans share many surface-level properties with AI-generated text: long words, no contractions, uniform sentence structure. A PhD thesis will likely score `uncertain` or even `likely_ai` on the stylometric signal regardless of its actual authorship. The LLM signal mitigates this somewhat (the evaluator LLM may pick up on genuine human reasoning patterns), but it is not a reliable fix. Users submitting formal documents should be warned that the uncertainty band is wide for that genre.

2. **The LLM signal is a black box with no ground-truth calibration.** `llm_score()` asks a Groq-hosted model to estimate AI authorship probability, but `llama-3.1-8b-instant` was not trained specifically for detection tasks and has no known false-positive or false-negative rate on benchmarked corpora. The 0.6 weight given to this signal is a reasonable engineering choice, not an empirically validated one. In a production deployment, the weights and thresholds should be recalibrated against a labeled dataset of known human- and AI-written texts before being used for consequential decisions.

## Spec Reflection

**One way the spec helped:** The explicit threshold values (0.68 and 0.38) and the exact label text strings were invaluable — they removed all ambiguity about what the output should look like and allowed `labels.py` and `scoring.py` to be written in minutes. Having concrete, testable output formats is far more useful than abstract behavioral descriptions.

**One way implementation diverged:** The spec describes the stylometric signal's formality score formula as `1 - (count_informal_markers / max(1, word_count) * 20)` with the note to "clamp to [0, 1]." In practice, this formula can produce large negative numbers for texts with even moderate informal marker density (e.g., 5 informal words in a 50-word text gives `1 - (5/50 * 20) = 1 - 2 = -1`), which clamps to 0 and makes the signal useless as a gradient. The implementation follows the formula exactly as written (matching the spec), but in a production system the scaling factor of 20 should be tuned downward — perhaps to 5 or 8 — so that the score degrades more gracefully across the informal-to-formal spectrum rather than flooring to 0 for any text with more than 5% informal markers.

## AI Usage

1. **Generating the Groq prompt wording.** Prompted Claude: "Write a concise instruction for an LLM asking it to return ONLY a single decimal number between 0.0 and 1.0 representing AI authorship probability, with no explanation or extra text." The produced draft said "respond with a number between 0 and 1" — revised to explicitly add "Do NOT include any explanation, label, or extra characters — output the number only" after observing in testing that the model sometimes prefaced the number with "Probability: ".

2. **Drafting the SQLite LEFT JOIN query for `get_log()`.** Prompted Claude: "Write a SQLite query that LEFT JOINs a submissions table and an appeals table on content_id, selects all fields from both (renaming appeals.timestamp to appeal_timestamp to avoid collision), and orders by the submission timestamp descending with a LIMIT clause." The generated query was correct but used `s.*` which would shadow column names; revised to explicitly name every column from submissions and the three appeal fields to guarantee predictable dict keys in the Python layer.

3. **Reviewing Flask-Limiter compound limit syntax.** Asked Claude to confirm whether Flask-Limiter 3.x accepts semicolon-delimited strings like `"10 per minute;100 per day"` in a single `@limiter.limit()` decorator. Confirmed correct — the documentation supports this syntax and no revision was needed.
