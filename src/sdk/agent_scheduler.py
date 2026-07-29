"""Agent scheduler — user-global background loop for proactive check-ins.

V1: No tool calls. Pre-computed context across all workspaces.
LLM decides: nudge with 1-2 sentences, or skip ([SKIP]).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from src.app_logging import get_logger
from src.sdk.loop import AgentLoop, RunConfig
from src.sdk.messages import Message
from src.sdk.providers.factory import create_model_from_config
from src.sdk.tools_core.agent_scheduler_db import SchedulerMemoryDB, SchedulerNotificationDB
from src.sdk.workspace_models import list_workspaces

logger = get_logger()

DEFAULT_INTERVAL_MINUTES = 15
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 30
DISMISSAL_STREAK_THRESHOLD = 3

COMPANION_SYSTEM_PROMPT = """You are Assistant's companion personality — a warm, attentive assistant
that checks in throughout the day. You work across ALL the user's workspaces
(projects), maintaining awareness of what's happening everywhere.

Your job: read the context below (time, email urgency, workspace activity,
what you know about the user) and decide to either:

  A) Write ONE brief, warm check-in message (1-2 sentences max)
     if something deserves the user's attention, or

  B) Skip this cycle entirely (just output '[SKIP]')

Tone: warm but professional. Like a great EA, not a chatbot.
Never repeat yourself. Vary your phrasing across cycles.
Morning: energetic. Midday: focused. Evening: reflective.

If the user has urgent emails → gentle nudge.
If specific workspaces have activity → mention which one.
If multiple workspaces have urgent items → pick the most impactful.
If workspaces are quiet → a brief hello or skip.
If late at night → skip.

You are NOT a chatbot. You are an assistant's personal assistant who
happens to check in periodically. Be brief. Be useful. Be warm."""


def _time_of_day(hour: int) -> str:
    if hour < 12:
        return "morning"
    elif hour < 17:
        return "afternoon"
    else:
        return "evening"


class AgentScheduler:
    """Per-user background scheduler for proactive check-ins."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._task: asyncio.Task[None] | None = None
        self._paused = False
        self._stopped = False
        self._error_count = 0
        self._last_check: str | None = None
        self._db = SchedulerNotificationDB(user_id)
        self._memory_db = SchedulerMemoryDB(user_id)
        self._loop: AgentLoop | None = None

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def last_check(self) -> str | None:
        return self._last_check

    @property
    def is_running(self) -> bool:
        return not self._paused and not self._stopped and self._error_count < 5

    async def start(self) -> None:
        self._stopped = False
        self._loop = self._create_loop()
        self._task = asyncio.create_task(self._run())
        logger.info("scheduler.started", {}, user_id=self.user_id)

    async def stop(self) -> None:
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._db.close()
        await self._memory_db.close()
        logger.info("scheduler.stopped", {}, user_id=self.user_id)

    async def pause(self) -> None:
        self._paused = True
        logger.info("scheduler.paused", {}, user_id=self.user_id)

    async def resume(self) -> None:
        self._paused = False
        logger.info("scheduler.resumed", {}, user_id=self.user_id)

    def _create_loop(self) -> AgentLoop:
        provider = create_model_from_config("ollama:minimax-m2.5")
        return AgentLoop(
            provider=provider,
            tools=[],
            system_prompt=COMPANION_SYSTEM_PROMPT,
            middlewares=[],
            run_config=RunConfig(max_llm_calls=2, cost_limit_usd=0.01),
            user_id=self.user_id,
        )

    async def _run(self) -> None:
        while not self._stopped:
            if not self._paused:
                try:
                    await self._cycle()
                    self._last_check = datetime.now(UTC).isoformat()
                    self._error_count = 0
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self._error_count += 1
                    logger.error(
                        "scheduler.cycle_failed",
                        {"error": str(e), "error_count": self._error_count},
                        user_id=self.user_id,
                    )
            interval = await self._next_interval()
            await asyncio.sleep(interval * 60)

    async def _cycle(self) -> None:
        ctx = await self._build_context()
        # Emit AgentEvent through TriggerRegistry instead of calling loop directly
        from src.sdk.loops.events import AgentEvent, get_trigger_registry

        event = AgentEvent(
            trigger_type="cron",
            trigger_id=f"scheduler_{self.user_id}",
            user_id=self.user_id,
            session_id=f"scheduler_{self.user_id}",
            message=ctx,
            metadata={"source": "agent_scheduler"},
        )
        registry = get_trigger_registry()
        try:
            await registry.fire(event)
        except KeyError:
            # No handler registered — fall back to direct loop call
            assert self._loop is not None
            result = await self._loop.run([Message.user(ctx)])
            text = self._extract_response(result)
            if text and text.strip().upper() != "[SKIP]" and len(text.strip()) > 3:
                category, ws_id = self._categorize(text)
                await self._db.insert(text, category, ws_id)
            return

        # If trigger handler ran, check the result from notification DB
        # (the default handler persists via run_sdk_agent, not notification DB)
        # For now, scheduler notifications are only from direct loop path
        # TODO: integrate notification DB with trigger handler results

    async def _build_context(self) -> str:
        now = datetime.now()
        hour = now.hour
        tod = _time_of_day(hour)
        urgent = _count_urgent_emails(self.user_id)

        workspaces = list_workspaces()
        ws_lines: list[str] = []
        for ws in workspaces:
            activity = _summarize_workspace_activity(ws.id)
            if activity:
                ws_lines.append(f"  - {ws.name}: {activity}")
        ws_text = "\n".join(ws_lines) if ws_lines else "  No recent activity across any workspace."

        recent = await self._db.recent_messages(3)
        personality = await self._get_personality_text()

        return f"""## Check-in context

TIME: {now.strftime('%I:%M %p %A %B %d')} ({tod})
URGENT UNREAD EMAILS: {urgent}
WORKSPACES ({len(workspaces)} total):
{ws_text}

WHAT I KNOW ABOUT THE USER:
{personality}

LAST CHECK-IN: {self._last_check or 'first check-in ever'}
PREVIOUS MESSAGES (avoid repeating): {recent}

Decide: nudge the user or skip?"""

    async def _get_personality_text(self) -> str:
        try:
            facts = await self._memory_db.get_all()
            if not facts:
                return "  nothing yet"
            return "\n".join(f"  - {k}: {v}" for k, v in facts.items())
        except Exception:
            return "  (unavailable)"

    @staticmethod
    def _extract_response(result: list[Message]) -> str:
        for msg in reversed(result):
            if msg.role == "assistant" and msg.content:
                content = msg.content if isinstance(msg.content, str) else ""
                return content.strip().strip('"').strip("'")
        return ""

    @staticmethod
    def _categorize(text: str) -> tuple[str, str | None]:
        text_lower = text.lower()
        if any(w in text_lower for w in ["urgent", "asap", "immediately", "critical", "warning"]):
            return ("urgent", None)
        if any(w in text_lower for w in ["email", "mail", "message from", "replied"]):
            return ("email", None)
        if any(w in text_lower for w in ["due", "deadline", "overdue", "due date"]):
            return ("deadline", None)
        if any(w in text_lower for w in ["morning", "afternoon", "evening", "good", "hello", "hi "]):
            return ("checkin", None)
        return ("general", None)

    async def _next_interval(self) -> int:
        try:
            streak = await self._db.dismissal_streak()
        except Exception:
            streak = 0
        if streak >= DISMISSAL_STREAK_THRESHOLD:
            return DEFAULT_INTERVAL_MINUTES * 2
        return DEFAULT_INTERVAL_MINUTES


def _count_urgent_emails(user_id: str) -> int:
    """Count unread emails across all connected email accounts."""
    try:
        from src.sdk.tools_core.email_db import get_engine, load_accounts

        accounts = load_accounts(user_id)
        if not accounts:
            return 0

        engine = get_engine(user_id)
        unread_count = 0
        with engine.connect() as conn:
            for acc_id in accounts:
                from sqlalchemy import text

                result = conn.execute(
                    text(
                        "SELECT COUNT(*) as cnt FROM emails "
                        "WHERE account_id = :acc_id AND read = 0"
                    ),
                    {"acc_id": acc_id},
                )
                row = result.fetchone()
                if row:
                    unread_count += row[0]
        return unread_count
    except Exception as e:
        logger.warning(
            "scheduler.email_count_failed",
            {"error": str(e)},
            user_id=user_id,
        )
        return 0


def _summarize_workspace_activity(workspace_id: str) -> str | None:
    """Get a brief activity summary for a workspace."""
    try:
        from src.storage.messages import get_message_store

        store = get_message_store(workspace_id=workspace_id)
        msgs = store.get_messages(limit=15)
        if not msgs:
            return None

        user_count = sum(1 for m in msgs if m.role == "user")
        assistant_count = sum(1 for m in msgs if m.role == "assistant")
        latest = msgs[0]

        parts: list[str] = []
        parts.append(f"{user_count} user messages, {assistant_count} assistant responses")
        parts.append(f"last activity: {_relative_time(latest.ts)}")
        return "; ".join(parts)
    except Exception as e:
        logger.debug(
            "scheduler.workspace_activity_failed",
            {"workspace_id": workspace_id, "error": str(e)},
            user_id="system",
        )
        return None


def _relative_time(ts: datetime | str) -> str:
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    diff = datetime.now(UTC).replace(tzinfo=None) - ts.replace(tzinfo=None)
    minutes = int(diff.total_seconds() / 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


_agent_schedulers: dict[str, AgentScheduler] = {}


def get_agent_scheduler(user_id: str) -> AgentScheduler:
    if user_id not in _agent_schedulers:
        _agent_schedulers[user_id] = AgentScheduler(user_id)
    return _agent_schedulers[user_id]
