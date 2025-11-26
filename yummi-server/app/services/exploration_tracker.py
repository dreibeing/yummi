from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List

logger = logging.getLogger(__name__)


StreamedIds = Dict[str, List[str]]
PersistCallback = Callable[[StreamedIds], Awaitable[None]]


@dataclass
class ExplorationBackgroundRun:
    session_id: str
    streamed_ids: StreamedIds
    pending_tasks: List[asyncio.Task]
    persist_callback: PersistCallback
    completion_task: asyncio.Task | None = None


_active_runs: Dict[str, ExplorationBackgroundRun] = {}


def register_background_run(
    *,
    session_id: str,
    streamed_ids: StreamedIds,
    pending_tasks: List[asyncio.Task],
    persist_callback: PersistCallback,
) -> None:
    if not pending_tasks:
        return
    if session_id in _active_runs:
        logger.debug("Exploration session %s already has a background run; skipping", session_id)
        return

    run = ExplorationBackgroundRun(
        session_id=session_id,
        streamed_ids=streamed_ids,
        pending_tasks=list(pending_tasks),
        persist_callback=persist_callback,
    )

    async def _monitor_run(background: ExplorationBackgroundRun) -> None:
        try:
            await asyncio.gather(*background.pending_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        finally:
            try:
                await background.persist_callback(background.streamed_ids)
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("Failed to persist final streamed ids for session %s", background.session_id)
            _active_runs.pop(background.session_id, None)

    run.completion_task = asyncio.create_task(_monitor_run(run))
    _active_runs[session_id] = run


async def flush_background_run(session_id: str | None) -> None:
    if not session_id:
        return
    run = _active_runs.get(str(session_id))
    if not run:
        return
    for task in run.pending_tasks:
        task.cancel()
    completion = run.completion_task
    if completion:
        try:
            await completion
        except asyncio.CancelledError:
            pass
    else:
        try:
            await run.persist_callback(run.streamed_ids)
        except Exception:  # pragma: no cover
            logger.exception("Failed to persist streamed ids while flushing session %s", session_id)
        _active_runs.pop(str(session_id), None)
