def compute_confidence(llm_score: float, stylometric_score: float) -> dict:
    """
    Combines the LLM-based signal and the stylometric signal into a single
    confidence score and human-readable attribution label.

    Weights:
        60% LLM signal (primary — model-based reasoning)
        40% Stylometric signal (secondary — heuristic)

    Thresholds:
        > 0.68  → likely_ai
        0.38-0.68 → uncertain
        < 0.38  → likely_human

    Returns:
        {
            "confidence": float,       # combined score in [0, 1]
            "attribution": str,        # "likely_ai" | "uncertain" | "likely_human"
        }
    """
    combined = 0.7 * llm_score + 0.3 * stylometric_score

    if combined > 0.68:
        attribution = "likely_ai"
    elif combined >= 0.38:
        attribution = "uncertain"
    else:
        attribution = "likely_human"

    return {"confidence": combined, "attribution": attribution}
