"""PostgreSQL operations for profile. Transactions are owned by application services."""

from __future__ import annotations

from typing import Any

from ..entity.profile import ProfileEntry
from .database import Database, J, Row


class ProfileRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, entry: ProfileEntry) -> None:
        self.db.run(
            """INSERT INTO profile(key,value,source,confirmed_at) VALUES(%s,%s,%s,now())
               ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, source=EXCLUDED.source, confirmed_at=now()""",
            (entry.key, J(entry.value), entry.source),
        )

    def find_all(self) -> list[ProfileEntry]:
        return [
            ProfileEntry.model_validate(row)
            for row in self.db.all("SELECT key,value,source,confirmed_at FROM profile ORDER BY key")
        ]

    def insert_profile_for_profile_section_set(self, section: str, value: Any) -> int:
        return self.db.run(
            """INSERT INTO profile(key,value,source,confirmed_at) VALUES(%s,%s,'user (dashboard)',now())
               ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, source=EXCLUDED.source, confirmed_at=now()""",
            (section, value),
        )

    def delete_profile_for_profile_clear(self, key: str) -> int:
        return self.db.run("DELETE FROM profile WHERE key=%s", (key,))

    def update_facts_for_fact_confirm(self, confirmed: bool, claim: str | None, fact_id: str) -> int:
        return self.db.run(
            "UPDATE facts SET confirmed_at=CASE WHEN %s THEN now() ELSE NULL END, claim=COALESCE(%s,claim) WHERE id=%s",
            (confirmed, claim, fact_id),
        )

    def find_automation_for_board_update(self) -> Row | None:
        return self.db.one("SELECT enabled FROM automation WHERE singleton FOR UPDATE")

    def update_boards_for_board_update(
        self,
        automation: str | None,
        board_id: str,
    ) -> int:
        return self.db.run(
            "UPDATE boards SET automation=COALESCE(%s::board_automation,automation) WHERE id=%s",
            (automation, board_id),
        )

    def find_facts_for_fact_confirm(self, fact_id: str) -> Row | None:
        return self.db.one("SELECT 1 FROM facts WHERE id=%s", (fact_id,))

    def find_tasks_for_board_update(self) -> Row | None:
        return self.db.one("SELECT 1 FROM tasks WHERE state='leased' AND visible_at>now() LIMIT 1")

    def save_derived_value(self, key: Any, value: Any, value_3: Any) -> int:
        return self.db.run(
            """INSERT INTO profile(key,value,source,confirmed_at) VALUES(%s,%s,%s,now())
                       ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, source=EXCLUDED.source, confirmed_at=now()""",
            (key, value, value_3),
        )

    def delete_profile_for_profile_section_set(self, key: Any, value_2: Any) -> int:
        return self.db.run("DELETE FROM profile WHERE key=%s AND source=%s", (key, value_2))
