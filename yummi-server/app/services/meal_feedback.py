from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Sequence

from sqlalchemy import delete, select

from ..db import get_session
from ..models import MealFeedbackEvent

MAX_FEEDBACK_EVENTS_PER_USER = 100


class MealFeedbackReaction(str, Enum):
    LIKE = "like"
    DISLIKE = "dislike"


class MealFeedbackSource(str, Enum):
    RECOMMENDATION_FEED = "recommendation_feed"
    EXPLORATION = "exploration"
    SHOPPING_SELECTION = "shopping_selection"
    CART_SELECTION = "cart_selection"
    ORDER_REVIEW = "order_review"
    HISTORY = "history"
    SYSTEM = "system"


@dataclass
class MealFeedbackEntry:
    meal_id: str
    reaction: MealFeedbackReaction
    source: MealFeedbackSource
    occurred_at: datetime
    context: Dict[str, Any]


@dataclass
class MealFeedbackSummary:
    liked: List[MealFeedbackEntry]
    disliked: List[MealFeedbackEntry]
    latest_by_meal: Dict[str, MealFeedbackEntry]

    @property
    def declined_meal_ids(self) -> List[str]:
        return [entry.meal_id for entry in self.disliked]

    def serialize_for_prompt(self) -> Dict[str, Any]:
        return {
            "likedMeals": summarize_feedback_entries(self.liked),
            "dislikedMeals": summarize_feedback_entries(self.disliked),
        }


async def record_meal_feedback_events(
    *,
    user_id: str,
    likes: Sequence[str] | None = None,
    dislikes: Sequence[str] | None = None,
    source: MealFeedbackSource = MealFeedbackSource.SYSTEM,
    metadata: Dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    likes = [mid for mid in (likes or []) if mid]
    dislikes = [mid for mid in (dislikes or []) if mid]
    if not likes and not dislikes:
        return
    when = occurred_at or datetime.now(timezone.utc)
    safe_metadata = _json_safe(metadata)
    events: List[MealFeedbackEvent] = []
    for meal_id in likes:
        events.append(
            MealFeedbackEvent(
                user_id=user_id,
                meal_id=str(meal_id),
                reaction=MealFeedbackReaction.LIKE.value,
                source=source.value,
                occurred_at=when,
                context=safe_metadata,
            )
        )
    for meal_id in dislikes:
        events.append(
            MealFeedbackEvent(
                user_id=user_id,
                meal_id=str(meal_id),
                reaction=MealFeedbackReaction.DISLIKE.value,
                source=source.value,
                occurred_at=when,
                context=safe_metadata,
            )
        )
    async with get_session() as session:
        session.add_all(events)
        await session.commit()
        if MAX_FEEDBACK_EVENTS_PER_USER and MAX_FEEDBACK_EVENTS_PER_USER > 0:
            stmt = (
                select(MealFeedbackEvent.id)
                .where(MealFeedbackEvent.user_id == user_id)
                .order_by(
                    MealFeedbackEvent.occurred_at.desc(),
                    MealFeedbackEvent.created_at.desc(),
                )
                .offset(MAX_FEEDBACK_EVENTS_PER_USER)
            )
            result = await session.execute(stmt)
            stale_ids = [row[0] for row in result.all()]
            if stale_ids:
                delete_stmt = delete(MealFeedbackEvent).where(MealFeedbackEvent.id.in_(stale_ids))
                await session.execute(delete_stmt)
                await session.commit()


async def record_single_meal_feedback(
    *,
    user_id: str,
    meal_id: str,
    reaction: MealFeedbackReaction,
    source: MealFeedbackSource,
    metadata: Dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    safe_metadata = _json_safe(metadata)
    await record_meal_feedback_events(
        user_id=user_id,
        likes=[meal_id] if reaction == MealFeedbackReaction.LIKE else None,
        dislikes=[meal_id] if reaction == MealFeedbackReaction.DISLIKE else None,
        source=source,
        metadata=safe_metadata,
        occurred_at=occurred_at,
    )


async def load_feedback_summary(user_id: str) -> MealFeedbackSummary:
    async with get_session() as session:
        stmt = (
            select(MealFeedbackEvent)
            .where(MealFeedbackEvent.user_id == user_id)
            .order_by(MealFeedbackEvent.occurred_at.desc(), MealFeedbackEvent.created_at.desc())
        )
        result = await session.execute(stmt)
        records = list(result.scalars())

    latest: Dict[str, MealFeedbackEntry] = {}
    ordered_entries: List[MealFeedbackEntry] = []
    for record in records:
        entry = MealFeedbackEntry(
            meal_id=record.meal_id,
            reaction=MealFeedbackReaction(record.reaction),
            source=MealFeedbackSource(record.source),
            occurred_at=record.occurred_at,
            context=record.context or {},
        )
        if record.meal_id not in latest:
            latest[record.meal_id] = entry
        ordered_entries.append(entry)

    liked: List[MealFeedbackEntry] = []
    disliked: List[MealFeedbackEntry] = []
    # Iterate latest map preserving inserted order
    for meal_id, entry in latest.items():
        if entry.reaction == MealFeedbackReaction.LIKE:
            liked.append(entry)
        elif entry.reaction == MealFeedbackReaction.DISLIKE:
            disliked.append(entry)

    liked.sort(key=lambda entry: entry.occurred_at, reverse=True)
    disliked.sort(key=lambda entry: entry.occurred_at, reverse=True)

    return MealFeedbackSummary(
        liked=liked,
        disliked=disliked,
        latest_by_meal=latest,
    )


async def clear_user_feedback(user_id: str) -> None:
    async with get_session() as session:
        stmt = delete(MealFeedbackEvent).where(MealFeedbackEvent.user_id == user_id)
        await session.execute(stmt)
        await session.commit()


def summarize_feedback_entries(entries: Iterable[MealFeedbackEntry]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for entry in entries:
        serialized.append(
            {
                "mealId": entry.meal_id,
                "reaction": entry.reaction.value,
                "source": entry.source.value,
                "occurredAt": entry.occurred_at.isoformat(),
                "context": entry.context,
            }
        )
    return serialized


def _json_safe(payload: Any) -> Dict[str, Any] | None:
    if payload is None:
        return None
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    try:
        return json.loads(json.dumps(payload))
    except (TypeError, ValueError):
        return {"value": str(payload)}
