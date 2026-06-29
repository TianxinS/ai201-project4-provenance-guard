import re
import math

_CONTRACTIONS = {
    "don't", "i've", "it's", "you're", "we're", "they're", "i'm",
    "can't", "won't", "isn't", "aren't", "wasn't", "weren't",
    "couldn't", "shouldn't", "wouldn't", "didn't", "doesn't",
    "haven't", "hasn't", "hadn't", "i'll", "you'll", "we'll",
    "they'll", "he'll", "she'll",
}

_INFORMAL_WORDS = {
    "lol", "omg", "tbh", "rn", "bc", "cuz", "gonna", "wanna",
    "gotta", "kinda", "sorta", "idk", "ngl",
}

_INFORMAL_MARKERS = _CONTRACTIONS | _INFORMAL_WORDS


def _split_sentences(text: str) -> list:
    parts = re.split(r"[.!?]+", text)
    return [p.strip() for p in parts if p.strip()]


def _sentence_uniformity(sentences: list) -> float:
    if len(sentences) < 2:
        return 0.5
    lengths = [len(s.split()) for s in sentences]
    mean = sum(lengths) / len(lengths)
    if mean == 0:
        return 0.5
    variance = sum((l - mean) ** 2 for l in lengths) / len(lengths)
    cv = math.sqrt(variance) / mean
    return max(0.0, 1.0 - cv)


def _formality_score(text: str, word_count: int) -> float:
    tokens = re.findall(r"[a-z']+", text.lower())
    count = sum(1 for t in tokens if t in _INFORMAL_MARKERS)
    return max(0.0, min(1.0, 1.0 - (count / max(1, word_count) * 8)))


def _avg_word_length_score(words: list) -> float:
    alpha_words = [w for w in words if re.search(r"[a-zA-Z]", w)]
    if not alpha_words:
        return 0.0
    avg_len = sum(len(w) for w in alpha_words) / len(alpha_words)
    return min(1.0, max(0.0, (avg_len - 3.5) / 3.5))


def stylometric_score(text: str) -> dict:
    """
    Computes stylometric properties of the text without any external API calls.
    Returns {"score": float (0-1, higher = more AI-like), "metrics": {...}}
    """
    sentences = _split_sentences(text)
    words = text.split()
    word_count = len(words)

    su = _sentence_uniformity(sentences)
    fs = _formality_score(text, word_count)
    aws = _avg_word_length_score(words)

    combined = 0.4 * su + 0.35 * fs + 0.25 * aws

    return {
        "score": combined,
        "metrics": {
            "sentence_uniformity": round(su, 4),
            "formality_score": round(fs, 4),
            "avg_word_length_score": round(aws, 4),
        },
    }
