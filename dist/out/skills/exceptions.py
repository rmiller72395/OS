# skills/exceptions.py — Execution-layer exception hierarchy (v5.0)
#
# Severity-based handling: RetryableError → retry strategy;
# AlertableError → alert and fail; ExecutionError → generic fail-closed.
# See EXECUTION_LAYER_REFACTOR_PLAN.md.

from __future__ import annotations


class ExecutionError(Exception):
    """Base for all execution-layer errors. Fail-closed: do not execute on error."""

    def __init__(self, message: str, severity: str = "FAIL") -> None:
        super().__init__(message)
        self.message = message
        self.severity = severity  # FAIL | RETRY | ALERT


class ValidationError(ExecutionError):
    """Input/schema validation failed. Do not execute; no retry."""

    def __init__(self, message: str) -> None:
        super().__init__(message, severity="FAIL")


class RetryableError(ExecutionError):
    """Transient failure (e.g. network, timeout). Caller may retry up to N times."""

    def __init__(self, message: str) -> None:
        super().__init__(message, severity="RETRY")


class AlertableError(ExecutionError):
    """Severe or config/schema failure. Log alert and fail; do not retry blindly."""

    def __init__(self, message: str) -> None:
        super().__init__(message, severity="ALERT")
