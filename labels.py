def generate_label(confidence: float, attribution: str) -> str:
    """
    Generates a human-readable transparency label from a confidence score and
    attribution string.

    Args:
        confidence:  Float in [0, 1] representing AI-authorship probability.
        attribution: One of "likely_ai", "uncertain", or "likely_human".

    Returns:
        A descriptive string label suitable for display to end-users.
    """
    if attribution == "likely_ai":
        return (
            f"Likely AI-Generated — This content shows strong indicators of AI authorship "
            f"(confidence: {confidence * 100:.0f}%). Our analysis detected uniform sentence "
            f"structure and formal register patterns characteristic of large language models."
        )
    elif attribution == "likely_human":
        return (
            f"Likely Human-Written — This content shows characteristics consistent with human "
            f"authorship (confidence: {confidence * 100:.0f}% AI probability). Our signals "
            f"detected natural variation in style and informal language patterns."
        )
    else:  # uncertain
        return (
            f"Origin Uncertain — Our analysis returned mixed signals for this content "
            f"(AI probability: {confidence * 100:.0f}%). Some indicators suggest AI involvement, "
            f"but the evidence is not conclusive."
        )
