# DEPRECATED: moved to app/core/logging_sanitizer.py
# Kept for backward compatibility — remove after confirming no external imports.
from app.core.logging_sanitizer import (  # noqa: F401
    MASK,
    SENSITIVE_KEYS,
    SensitiveDataFilter,
    ensure_sanitized_logging,
)
