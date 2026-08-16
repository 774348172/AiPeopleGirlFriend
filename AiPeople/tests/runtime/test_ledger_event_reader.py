from __future__ import annotations

from datetime import datetime, timezone

from runtime._ledger import EventLedger
from runtime.contracts import UserMessage


def test_get_event_returns_committed_record_and_none_for_unknown(tmp_path) -> None:
    ledger = EventLedger.open(tmp_path / "events.sqlite3")
    try:
        appended = ledger.append_user_message(
            UserMessage(
                request_id="request-1",
                conversation_id="conversation-1",
                text="账本证据",
                occurred_at=datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
                timezone="Asia/Shanghai",
                source="typed",
            )
        )
        assert ledger.get_event(appended.event.event_id) == appended.event
        assert ledger.get_event("event-unknown") is None
    finally:
        ledger.close()
