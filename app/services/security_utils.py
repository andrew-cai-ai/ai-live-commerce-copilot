from __future__ import annotations

import hmac


def safe_compare_digest(provided: str, expected: str) -> bool:
    """Constant-time compare that returns False on length mismatch (no ValueError)."""
    if not expected:
        return False
    if len(provided) != len(expected):
        return False
    return hmac.compare_digest(provided, expected)
