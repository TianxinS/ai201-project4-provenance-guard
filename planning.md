# Provenance Guard — Planning Document

## Architecture Narrative

When a POST request arrives at `/submit`, Flask validates that both `text` and `creator_id` fields are present and that the text is at least 20 characters long. A unique `content_id` (UUID4) is minted for the submission. The text is then passed in parallel conceptually to two independent signal modules: `llm_signal.llm_score()` calls the Groq API with `llama-3.1-8b-instant`, asking it to return a single float representing the probability the input was AI-generated; `stylometric.stylometric_score()` performs three pure-Python heuristic calculations — sentence-length uniformity, formality/informality marker counting, and average word length — then combines them into a single float and a metrics breakdown dict. Both raw scores flow into `scoring.compute_confidence()`, which applies a weighted average (60 % LLM, 40 % stylometric) to produce a `confidence` float and an `attribution` string. That pair is passed to `labels.generate_label()`, which selects one of three pre-written human-readable transparency labels. Finally, `audit.log_submission()` persists every field to a local SQLite database (`audit_log.db`), and the response dict — containing `content_id`, `attribution`, `confidence`, `label`, and the individual `signal_scores` — is returned as JSON. Appeals arrive at `/appeal`, are validated, and `audit.log_appeal()` updates the submission row's status to `under_review` and appends a record to the `appeals` table. The `/log` endpoint reads from a LEFT JOIN of both tables and returns recent entries for human review.

## Architecture Diagram

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
         attribution = "likely_ai" | "uncertain" | "likely_human"
                    │  confidence (0-1)
                    ▼
        [Label Generator — labels.py]
         select one of 3 label templates
                    │  label text
                    ▼
        [Audit Log — audit.py → SQLite]
         INSERT INTO submissions
                    │
                    ▼
             JSON Response
  { content_id, attribution, confidence,
    label, signal_scores }


Appeal flow:

POST /appeal
     │
     ▼
[Flask: validate content_id & creator_reasoning]
     │
     ▼
[Validate content_id exists — audit.get_submission()]
     │
     ▼
[Update status: under_review — audit.log_appeal()]
  UPDATE submissions SET status='under_review'
  INSERT INTO appeals
     │
     ▼
JSON confirmation
  { message, content_id, status }
```

## Detection Signals

### Signal 1: LLM-Based Attribution (Groq)

**What it measures:** The semantic and syntactic fingerprint of the text as perceived by a large language model that was itself trained on both human and AI-generated content. The model is prompted to reason about patterns in phrasing, hedging language, lexical choice, and structural coherence to estimate AI authorship probability.

**Why it differs between human and AI writing:** AI models tend to produce text with consistent, confident phrasing, balanced sentence construction, and minimal hedging or self-correction. They rarely use highly idiomatic expressions, unconventional punctuation, or stream-of-consciousness structure. Human writing tends to have more irregular rhythm, personal asides, emotional coloring, and occasional grammatical imprecision.

**Output format:** float 0.0–1.0 where 0.0 = certainly human-written, 1.0 = certainly AI-generated. Falls back to 0.5 on any parsing or network error.

**Blind spots:** The LLM evaluator may be fooled by AI text that has been lightly edited by a human (paraphrasing passes), or by formal human writing that mimics AI register (academic prose, legal drafts). The model also cannot verify factual content, so a hallucinated-fact-free AI passage is harder to detect than one with fabricated citations.

---

### Signal 2: Stylometric Heuristics

**What it measures:** Three metrics — sentence length uniformity, formality score (absence of contractions and informal markers), and average word length score — computed entirely locally without any API call.

**Why each metric differs between human and AI writing:**
- *Sentence uniformity*: AI models sample from a distribution that tends to produce sentences of similar length within a passage; human writers vary sentence length dramatically to create rhythm, build tension, or shift tone. Low coefficient of variation (CV) of sentence lengths is a reliable heuristic for AI output.
- *Formality score*: AI models are fine-tuned on instruction-following and helpful responses, producing output in a professional register. They rarely include contractions (don't, I've) or informal words (gonna, lol, idk) unless explicitly prompted to. Human writing — especially informal messages or personal essays — contains these markers naturally.
- *Average word length*: AI models tend to select longer, more precise vocabulary because they are rewarded for accuracy and formality. Human casual writing skews shorter. A high average word length score is associated with AI-generated formal content.

**Output format:** `{"score": float (0–1), "metrics": {"sentence_uniformity": float, "formality_score": float, "avg_word_length_score": float}}`

**Blind spots:** The formality score is sensitive to genre: a human-written academic paper will score very high in formality and appear AI-like. The sentence uniformity metric degrades on very short texts (fewer than 3 sentences). Average word length is easily gamed by a human who knows the heuristic. None of the three metrics individually is sufficient; they are meaningful only in combination.

## Uncertainty Representation

**Score of 0.6 means:** The combined signal puts this text in the uncertain band (0.38–0.68). The weighted blend of the LLM assessment and the stylometric heuristics both pointed toward moderate AI-likeness, but neither was strong enough to confidently classify the content. A score of 0.6 might represent clean professional writing that lacks AI's hallmark verbosity, or it might represent lightly edited AI output. A human reviewer should examine the full text and the per-signal breakdown before making a final determination.

**Thresholds:**
- > 0.68: `likely_ai` — both signals agree on strong AI indicators
- 0.38–0.68: `uncertain` — signals are split or individually weak
- < 0.38: `likely_human` — both signals show clear human markers

**Calibration approach:** The thresholds were chosen asymmetrically to err on the side of uncertainty rather than false positives. A content creator incorrectly labeled "likely_ai" faces reputational harm; therefore the `likely_ai` threshold is high (0.68) while the `likely_human` threshold is relatively tight (0.38). The wide uncertain band (0.38–0.68) captures ambiguous cases and routes them to the appeals workflow rather than making a confident — potentially wrong — call.

## Transparency Label Variants

**likely_ai** (e.g., confidence = 0.85):
> "Likely AI-Generated — This content shows strong indicators of AI authorship (confidence: 85%). Our analysis detected uniform sentence structure and formal register patterns characteristic of large language models."

**likely_human** (e.g., confidence = 0.22):
> "Likely Human-Written — This content shows characteristics consistent with human authorship (confidence: 22% AI probability). Our signals detected natural variation in style and informal language patterns."

**uncertain** (e.g., confidence = 0.54):
> "Origin Uncertain — Our analysis returned mixed signals for this content (AI probability: 54%). Some indicators suggest AI involvement, but the evidence is not conclusive."

## Appeals Workflow

**Who can appeal:** Any creator who submitted the original content, identified by `creator_id`. (Currently the system does not verify that the `creator_id` submitting the appeal matches the original submitter — a production system should add authentication here.)

**What they provide:** `content_id` (the UUID returned at submission time) plus `creator_reasoning` (a free-text field where the creator explains why they believe the classification is incorrect — e.g., "This is a personal essay I wrote over two weeks; I can provide drafts.").

**What the system does:** Looks up the `content_id` in the `submissions` table; if found, generates a new `appeal_id` UUID, sets `submissions.status` to `'under_review'`, and inserts a record into the `appeals` table containing the appeal_id, content_id, creator_reasoning, and timestamp.

**What a human reviewer would see:** Via `GET /log`, they receive a joined list of submissions with appeal fields: the original classification (`attribution`, `confidence`), the label text, the first 200 characters of the submitted text, the appeal reason, and both timestamps. They have all the context needed to make a manual determination.

## Anticipated Edge Cases

1. **Very short but padded text**: A creator sends exactly 20 characters (e.g., `"This is a short text"`). The stylometric signal will return low-confidence metrics because there is only one sentence and very few words. The LLM signal may also return noisy results on minimal input. The system will still process the text but the resulting label should be treated with low confidence. A minimum character threshold of 20 was chosen to reject clearly trivial inputs; a production system might raise this to 100–200 characters.

2. **Highly formal human writing (academic prose)**: A human-authored PhD thesis excerpt will score high on formality_score and avg_word_length_score and may also receive a high llm_score because it reads similarly to AI-generated academic content. This false positive is a known limitation of heuristic-based detection. The appeals workflow exists precisely for this scenario: the creator can supply an explanation, and a human reviewer can examine the original drafts.

3. **Multilingual or code-mixed text**: If a creator submits text that mixes English with another language or includes code snippets, the stylometric heuristics (which are English-specific in their contraction and informal-word lists) will produce meaningless scores. The LLM signal is more robust to this case but may still behave unexpectedly. The system will still return a result, but it should not be trusted for non-English content.

4. **Groq API unavailability**: If the Groq API is unreachable or returns an error, `llm_score()` catches the exception and returns 0.5, which is the neutral midpoint. This means the combined confidence is `0.6 * 0.5 + 0.4 * ss = 0.3 + 0.4 * ss`. For average stylometric scores (~0.5), this yields a confidence of ~0.5, landing in the uncertain band. The system degrades gracefully rather than failing, but audit logs can be inspected to see when the LLM signal was neutralized.

## API Surface

- **POST /submit**: accepts `{"text": str, "creator_id": str}`, returns `{"content_id": str, "attribution": str, "confidence": float, "label": str, "signal_scores": {"llm_score": float, "stylometric_score": float, "stylometric_metrics": {...}}}`. Rate-limited to 10 requests/minute and 100 requests/day per IP.

- **POST /appeal**: accepts `{"content_id": str, "creator_reasoning": str}`, returns `{"message": str, "content_id": str, "status": "under_review"}`. No rate limit on appeals (appeals are rare and legitimate).

- **GET /log**: accepts optional `?limit=N` query param (default 20, max 200), returns `{"entries": [...], "count": int}` where each entry is a joined row from submissions and appeals.

## AI Tool Plan

### M3 (submission + first signal)

Use AI assistance to generate the initial Groq prompt for `llm_signal.py` — specifically the wording of the system instruction that asks for a decimal-only response. Prompt used: "Write a concise single-turn user prompt that instructs an LLM to return only a decimal between 0.0 and 1.0 estimating AI authorship probability, with no explanation." Review and revise the generated prompt to ensure it discourages verbose responses by explicitly saying "output the number only."

Also use AI assistance to scaffold the Flask `submit()` route from the spec, then manually add the `?limit` query parameter handling on `/log` and the error handling for non-JSON request bodies.

### M4 (second signal + confidence scoring)

Use AI assistance to implement the coefficient of variation calculation in `stylometric.py`. Prompt: "Implement a Python function that computes the coefficient of variation of a list of sentence word-counts and converts it to a 0–1 score where low variation = high score." Review output carefully — the spec's formula `max(0, 1 - CV)` is simple but the AI initially suggested using sample std (n-1 denominator); corrected to population std (n denominator) to match the spec's intent for short texts.

Use AI assistance for the `audit.py` LEFT JOIN query in `get_log()`. Prompt: "Write a SQLite query that LEFT JOINs submissions and appeals on content_id, returns all fields with appeal fields as NULL when no appeal exists, ordered by submission timestamp descending." Verified output against SQLite documentation and tested with both joined and unjoined rows.

### M5 (production layer)

Use AI assistance to draft the Flask-Limiter configuration for the compound rate limit `"10 per minute;100 per day"`. Prompt: "Show me how to apply two rate limits simultaneously using Flask-Limiter's @limiter.limit decorator." Output confirmed correct — the semicolon-delimited string syntax is the documented approach for compound limits in Flask-Limiter 3.x. No revision needed.

Use AI assistance to review the full `app.py` for missing edge cases. AI suggested adding the `limit` query parameter bounds-checking on `GET /log` (which was added: `max(1, min(limit, 200))`).
