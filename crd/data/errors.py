"""Owner: Nilay (P2). Exceptions shared across data sources."""


class SourceUnavailable(Exception):
    """Network/5xx/timeout after retries, 401/403, or forced via CRD_FAIL_SOURCES."""


class CreditsExhausted(Exception):
    """HTTP 402 from Crustdata. Never retried."""


class BudgetExceeded(Exception):
    """Our own cap from config.limits was hit."""


class LLMUnavailable(Exception):
    """No API key, or the model call failed; callers must use their rule fallback."""
