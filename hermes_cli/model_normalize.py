# ---------------------------------------------------------------------------
# DeepSeek's API only recognises exactly two model identifiers.  We map
# common aliases and patterns to the canonical names.

_DEEPSEEK_REASONER_KEYWORDS: frozenset[str] = frozenset({
    "reasoner",
    "r1",
    "think",
    "reasoning",
    "cot",
})

_DEEPSEEK_CANONICAL_MODELS: frozenset[str] = frozenset({
    "deepseek-chat",       # V3 on DeepSeek direct and most aggregators
    "deepseek-reasoner",   # R1-family reasoning model
    "deepseek-v4-pro",     # V4 Pro — first-class model ID
    "deepseek-v4-flash",   # V4 Flash — first-class model ID
})

# First-class V-series IDs (``deepseek-v4-pro``, ``deepseek-v4-flash``,
# future ``deepseek-v5-*``, dated variants like ``deepseek-v4-flash-20260423``).
# Verified empirically 2026-04-24: DeepSeek's Chat Completions API returns
# ``provider: DeepSeek`` / ``model: deepseek-v4-flash-20260423`` when called
# with ``model=deepseek/deepseek-v4-flash``, so these names are not aliases
# of ``deepseek-chat`` and must not be folded into it.
_DEEPSEEK_V_SERIES_RE = re.compile(r"^deepseek-v\d+([-.].+)?$")
