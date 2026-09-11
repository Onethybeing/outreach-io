"""Per-run usage counters and Langfuse tracing.

Both live in context variables set by the run executor, so LLM and search calls deep inside nodes
report without threading a context object through every function. Outside a run they do nothing.
Tracing failures are logged and swallowed: a Langfuse outage must never fail a run.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langfuse import Langfuse, propagate_attributes
from sqlalchemy.orm import Session

from app import vault

logger = logging.getLogger(__name__)


@dataclass
class RunTelemetry:
    usage: dict[str, float] = field(default_factory=dict)
    langfuse: Langfuse | None = None
    on_notice: Callable[[str], None] | None = None  # e.g. "waiting 20s for Groq rate limit"

    def add(self, key: str, amount: float = 1) -> None:
        self.usage[key] = self.usage.get(key, 0) + amount


_current: ContextVar[RunTelemetry | None] = ContextVar("run_telemetry", default=None)


def current() -> RunTelemetry | None:
    return _current.get()


def count(key: str, amount: float = 1) -> None:
    if (telemetry := _current.get()) is not None:
        telemetry.add(key, amount)


def notice(message: str) -> None:
    telemetry = _current.get()
    if telemetry is not None and telemetry.on_notice is not None:
        telemetry.on_notice(message)


@contextmanager
def activate(telemetry: RunTelemetry) -> Iterator[RunTelemetry]:
    token = _current.set(telemetry)
    try:
        yield telemetry
    finally:
        _current.reset(token)


# --- Langfuse ------------------------------------------------------------------

_client: tuple[tuple[str, str, str], Langfuse] | None = None


def langfuse_client(db: Session) -> Langfuse | None:
    """One client per credential set; rebuilt after a vault rotation. None if not configured."""
    global _client
    try:
        creds = vault.get_credential(db, "langfuse")
    except (vault.VaultError, RuntimeError) as exc:
        logger.info("Tracing off: %s", exc)
        return None
    key = (creds["public_key"], creds["secret_key"], creds["host"])
    if _client is None or _client[0] != key:
        try:
            _client = (key, Langfuse(public_key=key[0], secret_key=key[1], base_url=key[2]))
        except Exception:  # noqa: BLE001
            logger.exception("Tracing off: could not create Langfuse client")
            return None
    return _client[1]


class _Observation:
    """Wraps a Langfuse observation so update() can never raise into agent code."""

    def __init__(self, inner: Any = None) -> None:
        self._inner = inner

    def update(self, **kwargs: Any) -> None:
        if self._inner is None:
            return
        try:
            self._inner.update(**kwargs)
        except Exception:  # noqa: BLE001
            logger.warning("Langfuse update failed", exc_info=True)


@contextmanager
def observation(name: str, as_type: str = "span", **kwargs: Any) -> Iterator[_Observation]:
    telemetry = _current.get()
    client = telemetry.langfuse if telemetry else None
    if client is None:
        yield _Observation()
        return
    try:
        manager = client.start_as_current_observation(name=name, as_type=as_type, **kwargs)
        inner = manager.__enter__()
    except Exception:  # noqa: BLE001
        logger.warning("Langfuse observation %s failed to start", name, exc_info=True)
        yield _Observation()
        return

    wrapped = _Observation(inner)
    try:
        yield wrapped
    except BaseException as exc:
        wrapped.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}"[:500])
        _safe_exit(manager, type(exc), exc, exc.__traceback__)
        raise
    else:
        _safe_exit(manager, None, None, None)


def _safe_exit(manager: Any, *exc_info: Any) -> None:
    try:
        manager.__exit__(*exc_info)
    except Exception:  # noqa: BLE001
        logger.warning("Langfuse observation failed to close", exc_info=True)


@contextmanager
def trace_attributes(**kwargs: Any) -> Iterator[None]:
    telemetry = _current.get()
    if telemetry is None or telemetry.langfuse is None:
        yield
        return
    try:
        manager = propagate_attributes(**kwargs)
        manager.__enter__()
    except Exception:  # noqa: BLE001
        logger.warning("Langfuse trace attributes failed", exc_info=True)
        yield
        return
    try:
        yield
    finally:
        _safe_exit(manager, None, None, None)


def trace_id_for(run_id: str) -> str:
    return Langfuse.create_trace_id(seed=run_id)


def trace_url(client: Langfuse | None, trace_id: str) -> str | None:
    if client is None:
        return None
    try:
        return client.get_trace_url(trace_id=trace_id)
    except Exception:  # noqa: BLE001
        logger.warning("Could not build Langfuse trace URL", exc_info=True)
        return None


def flush(client: Langfuse | None) -> None:
    if client is None:
        return
    try:
        client.flush()
    except Exception:  # noqa: BLE001
        logger.warning("Langfuse flush failed", exc_info=True)
