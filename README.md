# Provenance Guard

## What It Does

Provenance Guard is an AI-text detection API that classifies submitted text as likely AI-generated, likely human-written, or origin uncertain. It combines two independent signals — a Groq-hosted LLM that estimates AI authorship probability and a suite of pure-Python stylometric heuristics — to produce a weighted confidence score and a human-readable transparency label for every submission. Every classification is persisted to a structured JSONL audit log.

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
[LLM Signal — llm_signal.py]          [Stylometric Signal — stylometric.py]
 Groq llama-3.3-70b-versatile           sentence_uniformity
 prompt → decimal float                 formality_score
     │                                  avg_word_length_score
     │  llm_score (0-1)                     │  sty_score (0-1)
     └──────────────┬──────────────────────┘
                    ▼
        [Confidence Scorer — scoring.py]
         combined = 0.7*llm + 0.3*sty
         attribution → "likely_ai" | "uncertain" | "likely_human"
                    │
                    ▼
        [Label Generator — labels.py]
         select one of 3 label templates
                    │
                    ▼
        [Audit Log — audit.py → logs/audit.jsonl]
                    │
                    ▼
             JSON Response


Appeal flow:

POST /appeal → [Validate content_id] → [Update status: under_review] → [Audit Log] → JSON confirmation
```

A submitted text travels through two independent signal modules, their scores are blended by the confidence scorer (70% LLM weight, 30% stylometric weight), the result is converted into a transparency label, everything is written to `logs/audit.jsonl`, and the classification JSON is returned. Appeals are a separate, simpler flow: validate the content_id exists, mark it under review, log the creator's reasoning, and return confirmation.

## Detection Signals

### Signal 1: LLM-Based Attribution

**What it measures:** The semantic and stylistic fingerprint of the text as interpreted by `llama-3.3-70b-versatile` via Groq. The model has been exposed to vast quantities of both human and AI text during training, giving it an implicit understanding of register, phrasing patterns, and structural habits that differ between the two.

**Why it works:** AI models tend to produce confident, well-structured sentences with balanced clause lengths, precise vocabulary, and an absence of conversational self-correction. Human writing tends to be more irregular: tonal shifts, personal asides, incomplete thoughts, and idiomatic expressions that a language model would be unlikely to generate unprompted.

**Output format:** A float in [0.0, 1.0] where 0.0 = certainly human-written and 1.0 = certainly AI-generated. Returns 0.5 as a neutral fallback on any API error.

**What it misses:** AI text that has been lightly edited by a human may pass as human-written. Conversely, very formal human writing (academic papers, legal briefs) can be mistaken for AI output because it shares structural properties with LLM-generated text.

---

### Signal 2: Stylometric Heuristics

**What it measures:** Three pure-Python metrics that capture structural and lexical patterns associated with AI writing, computed entirely locally with no external API calls.

**Metric 1 — Sentence Uniformity (weight 40%):** Computes the coefficient of variation (CV = std/mean) of sentence lengths in words. A low CV means sentences are similarly sized, which is characteristic of AI output. Score = `max(0, 1 - CV)`. Higher score = more uniform = more AI-like.

**Metric 2 — Formality Score (weight 35%):** Counts contractions (don't, I've, can't, …) and informal markers (lol, gonna, idk, …) in the text. `formality_score = 1 - (count / max(1, word_count) * 8)`, clamped to [0, 1]. AI models rarely use informal language unprompted, so high formality correlates with AI authorship.

**Metric 3 — Average Word Length Score (weight 25%):** Computes the average character length of words. `score = min(1, max(0, (avg_word_len - 3.5) / 3.5))`. AI models tend toward longer, more precise vocabulary; casual human writing skews shorter.

**Output format:** `{"score": float, "metrics": {"sentence_uniformity": float, "formality_score": float, "avg_word_length_score": float}}`

**What it misses:** Formal human writing (academic prose, legal documents) will score high on formality and word length regardless of authorship. The signal degrades on very short texts (fewer than 3 sentences). These heuristics are meaningful only when combined with the LLM signal.

## Confidence Scoring

The two raw scores are combined with a weighted average:

```
confidence = 0.7 × llm_score + 0.3 × stylometric_score
```

The LLM signal receives higher weight (70%) because it captures semantic patterns the heuristics cannot. The stylometric signal (30%) acts as a verification layer that is immune to Groq API outages.

**Thresholds:**
| Confidence Range | Attribution | Meaning |
|-----------------|-------------|---------|
| > 0.68 | `likely_ai` | Strong AI indicators from both signals |
| 0.38 – 0.68 | `uncertain` | Signals are split or individually weak |
| < 0.38 | `likely_human` | Clear human markers detected |

The thresholds are intentionally asymmetric: the `likely_ai` bar is high (0.68) to minimize false positives against human creators, while the wide uncertain band routes ambiguous cases to human review.

---

### Example Submissions

**Example 1 — Clearly AI-Generated**

```bash
curl -X POST http://127.0.0.1:5000/submit -H "Content-Type: application/json" \
  -d '{"text": "Artificial intelligence represents a transformative paradigm shift in modern society. It is important to note that while the benefits are numerous, stakeholders must collaborate to ensure responsible deployment.", "creator_id": "test-ai"}'
```

Actual output:
```json
{
  "attribution": "likely_ai",
  "confidence": 0.8165,
  "content_id": "ce42b0f5-5867-47de-af82-c87ca46d8464",
  "label": "Likely AI-Generated — This content shows strong indicators of AI authorship (confidence: 82%). Our analysis detected uniform sentence structure and formal register patterns characteristic of large language models.",
  "signal_scores": {
    "llm_score": 0.8,
    "stylometric_metrics": {
      "avg_word_length_score": 0.8776,
      "formality_score": 1.0,
      "sentence_uniformity": 0.7143
    },
    "stylometric_score": 0.8551
  }
}
```

**Example 2 — Clearly Human-Written**

```bash
curl -X POST http://127.0.0.1:5000/submit -H "Content-Type: application/json" \
  -d '{"text": "ok so I finally tried that ramen place downtown and honestly underwhelming. the broth was fine but way too salty and I was thirsty for hours after. probably wont go back.", "creator_id": "test-human"}'
```

Actual output:
```json
{
  "attribution": "likely_human",
  "confidence": 0.3329,
  "content_id": "c9bd8dbd-5a63-41cf-a6ec-ac6c8252fd56",
  "label": "Likely Human-Written — This content shows characteristics consistent with human authorship (confidence: 33% AI probability). Our signals detected natural variation in style and informal language patterns.",
  "signal_scores": {
    "llm_score": 0.2,
    "stylometric_metrics": {
      "avg_word_length_score": 0.2903,
      "formality_score": 1.0,
      "sentence_uniformity": 0.5507
    },
    "stylometric_score": 0.6429
  }
}
```

The confidence scores differ significantly: **0.82 vs 0.33**, producing different labels and different signal profiles.

## Transparency Label Variants

**likely_ai** — triggers when confidence > 0.68:
> "Likely AI-Generated — This content shows strong indicators of AI authorship (confidence: 82%). Our analysis detected uniform sentence structure and formal register patterns characteristic of large language models."

**likely_human** — triggers when confidence < 0.38:
> "Likely Human-Written — This content shows characteristics consistent with human authorship (confidence: 33% AI probability). Our signals detected natural variation in style and informal language patterns."

**uncertain** — triggers when confidence is between 0.38 and 0.68:
> "Origin Uncertain — Our analysis returned mixed signals for this content (AI probability: 54%). Some indicators suggest AI involvement, but the evidence is not conclusive."

## API Endpoints

### POST /submit

Classifies text and logs the result. Rate-limited to 10/minute and 100/day.

**Request:**
```json
{ "text": "string (minimum 20 characters)", "creator_id": "string" }
```

**Response (200):**
```json
{
  "content_id": "uuid-string",
  "attribution": "likely_ai | uncertain | likely_human",
  "confidence": 0.8165,
  "label": "Likely AI-Generated — ...",
  "signal_scores": {
    "llm_score": 0.8,
    "stylometric_score": 0.855,
    "stylometric_metrics": {
      "sentence_uniformity": 0.71,
      "formality_score": 1.0,
      "avg_word_length_score": 0.88
    }
  }
}
```

**Error responses:** `400` missing fields or text too short · `429` rate limit exceeded

---

### POST /appeal

Files a creator appeal against an existing classification.

**Request:**
```json
{ "content_id": "uuid-string", "creator_reasoning": "explanation" }
```

**Actual response:**
```json
{
  "content_id": "ce42b0f5-5867-47de-af82-c87ca46d8464",
  "message": "Appeal received and is under review.",
  "status": "under_review"
}
```

**Error responses:** `400` missing fields · `404` content_id not found

---

### GET /log

Returns recent audit log entries.

**Query parameters:** `limit` (int, default 20, max 200)

**Response:** `{ "entries": [...], "count": int }`

## Rate Limiting

**Chosen limits:** 10 requests per minute, 100 requests per day (per IP address, in-memory).

**Reasoning:** The `/submit` endpoint makes a Groq API call for every request. Ten per minute allows a creator to check their own work at a natural pace while preventing automated flooding. 100 per day caps Groq API usage during development. `/appeal` and `/log` are not rate-limited — appeals are rare by nature and `/log` is read-only.

**Actual rate limit test output** (12 rapid requests — limit is 10/minute):

```
200
200
200
200
200
200
200
200
200
200
429
429
```

Requests 11 and 12 received `429 Too Many Requests` as expected.

## Audit Log

Log entries are written to `logs/audit.jsonl` as newline-delimited JSON. Submissions and appeals are separate entry types linked by `content_id`.

**Sample submission entry:**
```json
{
  "type": "submission",
  "content_id": "ce42b0f5-5867-47de-af82-c87ca46d8464",
  "creator_id": "test-ai",
  "timestamp": "2026-06-29T06:32:49.070682Z",
  "text_snippet": "Artificial intelligence represents a transformative paradigm shift...",
  "attribution": "likely_ai",
  "confidence": 0.8165,
  "llm_score": 0.8,
  "stylometric_score": 0.8551,
  "label": "Likely AI-Generated — ...",
  "status": "classified"
}
```

**Sample appeal entry** (same content_id, status updated to under_review):
```json
{
  "type": "appeal",
  "appeal_id": "d8dfe940-35e8-4359-a126-5dda06eb6db8",
  "content_id": "ce42b0f5-5867-47de-af82-c87ca46d8464",
  "creator_reasoning": "I wrote this myself. I am a non-native English speaker and my writing style may appear more formal than typical.",
  "timestamp": "2026-06-29T06:41:27.473532Z",
  "status": "under_review"
}
```

**Field reference:**
| Field | Meaning |
|-------|---------|
| `content_id` | UUID assigned at submission time; used for appeals |
| `creator_id` | Identifier provided by the submitting creator |
| `timestamp` | UTC ISO-8601 timestamp |
| `text_snippet` | First 200 characters of submitted text |
| `attribution` | `likely_ai`, `uncertain`, or `likely_human` |
| `confidence` | Weighted combined score in [0, 1] |
| `llm_score` | Raw output from the Groq LLM signal |
| `stylometric_score` | Raw output from the stylometric heuristics |
| `status` | `classified` (initial) or `under_review` (after appeal) |

## Known Limitations

1. **Formal human writing scores as AI-like.** Academic prose, legal documents, and technical reports written by humans share surface-level properties with AI text: long words, no contractions, uniform sentence structure. A PhD thesis will likely score `uncertain` or `likely_ai` on the stylometric signal regardless of actual authorship. The LLM signal mitigates this somewhat but is not a reliable fix for this genre.

2. **The LLM signal has no ground-truth calibration.** `llm_score()` asks `llama-3.3-70b-versatile` to estimate AI authorship probability, but this model was not trained specifically for detection and has no known false-positive or false-negative rate on benchmarked corpora. The 70% weight is a reasonable engineering choice, not an empirically validated one. In production, weights and thresholds should be recalibrated against a labeled dataset before use in consequential decisions.

## Spec Reflection

**One way the spec helped:** The explicit threshold values (0.68 and 0.38) and exact label text strings removed all ambiguity about output format and allowed `labels.py` and `scoring.py` to be written quickly against a concrete contract.

**One way implementation diverged:** The original spec used `llama-3.1-8b-instant` and a stylometric formality multiplier of 20. In testing, the 8b model scored nearly all text above 0.7 regardless of content, and the ×20 multiplier made the formality score collapse to 0 with even one contraction in a 20-word text. Both were changed during implementation: the model was upgraded to `llama-3.3-70b-versatile` for better discrimination, and the multiplier was reduced to 8 so the formality score degrades more gradually across the informal-to-formal spectrum.

## AI Usage

1. **Generating the Groq prompt wording.** Prompted Claude: "Write a concise instruction for an LLM asking it to return ONLY a single decimal number between 0.0 and 1.0 representing AI authorship probability, with no explanation." The draft said "respond with a number between 0 and 1" — revised to add "Do NOT include any explanation, label, or extra characters — output the number only" after the model sometimes prefaced the number with "Probability:".

2. **Drafting the JSONL audit log structure.** Prompted Claude to design the log entry schema and the `get_log()` function that reads entries in reverse order. The initial output used `json.load()` on the whole file — corrected to read line-by-line (`readlines()`) to handle the newline-delimited format correctly and avoid loading the entire log into memory.

3. **Flask-Limiter compound limit syntax.** Asked Claude to confirm whether Flask-Limiter 3.x accepts semicolon-delimited strings like `"10 per minute;100 per day"` in a single `@limiter.limit()` decorator. Confirmed correct — no revision needed, but the `storage_uri="memory://"` parameter was added after discovering it was required in Limiter ≥ 3.x to avoid a startup warning.
