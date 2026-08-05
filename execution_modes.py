from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class ExecutionMode(StrEnum):
    RESEARCH = "research"
    PAPER = "paper"
    LIVE = "live"


class ModeConfigurationError(ValueError):
    """Raised when execution mode configuration is unsafe or ambiguous."""


class LiveExecutionBlocked(RuntimeError):
    """Raised when a live-only operation is requested outside confirmed live mode."""


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    mode: ExecutionMode
    configured_mode: ExecutionMode
    live_confirmed: bool = False

    @property
    def requires_wallet(self) -> bool:
        return self.mode is ExecutionMode.LIVE

    @property
    def label(self) -> str:
        return self.mode.value.upper()


def parse_mode(value: object, *, source: str) -> ExecutionMode:
    if not isinstance(value, str) or not value.strip():
        raise ModeConfigurationError(f"{source} must be research, paper, or live")
    normalized = value.strip().lower()
    try:
        return ExecutionMode(normalized)
    except ValueError as exc:
        raise ModeConfigurationError(
            f"invalid {source} {value!r}; expected research, paper, or live"
        ) from exc


def resolve_execution_context(
    *,
    configured_mode: object,
    cli_mode: str | None,
    confirm_live: bool,
) -> ExecutionContext:
    configured = parse_mode(configured_mode, source="config mode")
    selected = configured if cli_mode is None else parse_mode(cli_mode, source="CLI mode")

    if confirm_live and selected is not ExecutionMode.LIVE:
        raise ModeConfigurationError("--confirm-live is valid only with --mode live")

    if selected is ExecutionMode.LIVE:
        if configured is not ExecutionMode.LIVE:
            raise ModeConfigurationError("live mode requires config.json mode='live'")
        if cli_mode is None or parse_mode(cli_mode, source="CLI mode") is not ExecutionMode.LIVE:
            raise ModeConfigurationError("live mode must be requested explicitly with --mode live")
        if not confirm_live:
            raise ModeConfigurationError("live mode requires the additional --confirm-live flag")

    return ExecutionContext(
        mode=selected,
        configured_mode=configured,
        live_confirmed=selected is ExecutionMode.LIVE and confirm_live,
    )


def require_live(context: ExecutionContext, *, operation: str) -> None:
    if context.mode is not ExecutionMode.LIVE or not context.live_confirmed:
        raise LiveExecutionBlocked(f"{operation} is blocked in {context.mode.value} mode")


def run_live_operation[T](
    context: ExecutionContext,
    *,
    operation: str,
    callback: Callable[[], T],
) -> T:
    """Run a callback only after the confirmed-live gate succeeds."""
    require_live(context, operation=operation)
    return callback()
