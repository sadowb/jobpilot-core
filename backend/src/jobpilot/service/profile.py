"""Profile operations behind the shared Services facade."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..boards import adapter_by_id, exceeds_ceiling
from ..domain.countries import normalize_country
from ..entity.profile import ProfileEntry
from ..repository.database import J
from ..repository.profile import ProfileRepository

if TYPE_CHECKING:
    from .application import Services


class ProfileService:
    def __init__(self, application: Services) -> None:
        self.application = application
        self.repository = ProfileRepository(application.db)

    def profile_set(self, key: str, value: str, source: str, *, replan: bool = True) -> None:
        if key in ("country", "nationality", "education_country") and value.strip():
            canon = normalize_country(value)
            if canon is None:
                raise ValueError(f"'{value}' is not a country I recognise; pick one from the list")
            value = canon
        self.repository.save(ProfileEntry(key=key, value=value, source=source))
        self.application.event(None, "profile_updated", "user", {"key": key})
        if replan:
            self.application.replan_blocked()

    def profile_section_set(self, section: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Replace one structured section (education/experience/languages) and refresh the flat keys derived from it."""
        from ..domain.profile import DERIVED_SOURCE, clean_section, derive

        clean = clean_section(section, entries)
        self.repository.insert_profile_for_profile_section_set(section, J(clean))
        for key, value in derive(section, clean).items():
            if value:
                self.repository.save_derived_value(key, J(value), f"{DERIVED_SOURCE}:{section}")
            else:  # clear only what this section derived; a value the owner typed by hand is theirs
                self.repository.delete_profile_for_profile_section_set(key, f"{DERIVED_SOURCE}:{section}")
        self.application.event(None, "profile_section_updated", "user", {"section": section, "entries": len(clean)})
        self.application.replan_blocked()
        return clean

    def profile_clear(self, key: str) -> None:
        self.repository.delete_profile_for_profile_clear(key)
        self.application.event(None, "profile_cleared", "user", {"key": key})
        self.application.replan_blocked()

    def fact_confirm(self, fact_id: str, confirmed: bool, claim: str | None) -> None:
        if not self.repository.find_facts_for_fact_confirm(fact_id):
            raise LookupError(f"unknown fact {fact_id}")
        self.repository.update_facts_for_fact_confirm(confirmed, claim, fact_id)
        self.application.event(None, "fact_updated", "user", {"id": fact_id, "confirmed": confirmed})

    def board_update(
        self,
        board_id: str,
        automation: str | None,
    ) -> None:
        control = self.repository.find_automation_for_board_update()
        if (control and control["enabled"]) or self.repository.find_tasks_for_board_update():
            raise PermissionError("stop automation and drain leases before changing board policy")
        if automation is not None and exceeds_ceiling(board_id, automation):
            raise PermissionError(
                f"{adapter_by_id(board_id).display_name} cannot be set above {adapter_by_id(board_id).max_automation}"
            )
        self.repository.update_boards_for_board_update(automation, board_id)
        self.application.event(None, "board_updated", "user", {"id": board_id, "automation": automation})

    def get_profile(self) -> list[ProfileEntry]:
        return self.repository.find_all()
