"""Form-answer planning: label aliases, derived values and answer precedence; pure domain code, no database."""

import unittest
from typing import Any

from jobpilot.domain.answers import SKIPPED, Known, PlanItem, build_plan, profile_key_for
from jobpilot.domain.canonical import norm

PROFILE = {"first_name": "Ada", "last_name": "Lovelace"}
DERIVED_SOURCE = "derived:profile name"
JOB_ID = "job1"
COMPANY = "acme"


def field(label: str, *, required: bool = True, ftype: str = "text", **extra: Any) -> dict[str, Any]:
    return {"key": f"k:{label}", "label": label, "type": ftype, "required": required, **extra}


def plan_for(
    label: str,
    profile: dict[str, str] | None = None,
    answers: dict[str, dict[str, str]] | None = None,
    **kwargs: Any,
) -> tuple[list[PlanItem], list[str]]:
    known = Known(profile=PROFILE if profile is None else profile, answers=answers or {})
    return build_plan([field(label, **kwargs)], known, JOB_ID, COMPANY)


class OptionalQuestionTests(unittest.TestCase):
    def test_unmatched_optional_answer_is_not_missing(self) -> None:
        for required in (False, True):
            with self.subTest(required=required):
                plan, missing = plan_for(
                    "School",
                    profile={"university": "Unknown University"},
                    ftype="custom-select",
                    required=required,
                    unmatchedValue="Unknown University",
                    options=["Other"],
                )
                self.assertEqual(plan, [])
                self.assertEqual(missing, ["k:School"] if required else [])


class FullNameAliasTests(unittest.TestCase):
    def test_label_spellings_resolve_to_full_name(self) -> None:
        for label in ("Name", "Full name", "Legal name", "Full legal name", "Your name", "Your full name", "My legal name"):
            for variant in (label, label.upper(), label.lower(), f"{label} *", f"{label}*", f"{label}:"):
                with self.subTest(label=variant):
                    self.assertEqual(profile_key_for(variant), "full_name")

    def test_other_name_labels_keep_their_own_key_or_none(self) -> None:
        cases = {
            "First name": "first_name",
            "Legal first name": "first_name",
            "Last name": "last_name",
            "Surname": "last_name",
            "Preferred name": "preferred_name",
            "Preferred first name": "preferred_name",
            "Nickname": "preferred_name",
        }
        for label, key in cases.items():
            with self.subTest(label=label):
                self.assertEqual(profile_key_for(label), key)

    def test_labels_about_something_else_are_not_full_name(self) -> None:
        for label in (
            "Company name",
            "School name",
            "University name",
            "Name of your current employer",
            "Reference name",
            "Emergency contact name",
            "Manager name",
            "Username",
            "User name",
            "Name of your manager",
            "Full name of reference",
            "Pet name",
        ):
            with self.subTest(label=label):
                self.assertNotEqual(profile_key_for(label), "full_name")


class FullNameDerivationTests(unittest.TestCase):
    def test_required_name_is_filled_from_first_and_last(self) -> None:
        for label in ("Name", "Full name", "Legal name", "Full legal name", "Your name"):
            for variant in (label, f"{label} *", label.upper(), label.lower()):
                with self.subTest(label=variant):
                    plan, missing = plan_for(variant)
                    self.assertEqual(plan, [PlanItem(f"k:{variant}", "Ada Lovelace", DERIVED_SOURCE)])
                    self.assertEqual(missing, [])

    def test_non_required_name_is_also_filled(self) -> None:
        plan, missing = plan_for("Name", required=False)
        self.assertEqual(plan, [PlanItem("k:Name", "Ada Lovelace", DERIVED_SOURCE)])
        self.assertEqual(missing, [])

    def test_parts_are_trimmed(self) -> None:
        plan, _ = plan_for("Name", profile={"first_name": " Ada ", "last_name": "\tLovelace\n"})
        self.assertEqual([p.value for p in plan], ["Ada Lovelace"])

    def test_either_part_missing_makes_a_required_field_an_owner_question(self) -> None:
        cases = {
            "no first name": {"last_name": "Lovelace"},
            "no last name": {"first_name": "Ada"},
            "blank first name": {"first_name": "   ", "last_name": "Lovelace"},
            "blank last name": {"first_name": "Ada", "last_name": ""},
            "neither": {},
        }
        for name, profile in cases.items():
            with self.subTest(case=name):
                plan, missing = plan_for("Name", profile=profile)
                self.assertEqual(plan, [])
                self.assertEqual(missing, ["k:Name"])

    def test_either_part_missing_leaves_an_optional_field_alone(self) -> None:
        for profile in ({"first_name": "Ada"}, {"last_name": "Lovelace"}, {"first_name": " ", "last_name": "Lovelace"}, {}):
            with self.subTest(profile=profile):
                self.assertEqual(plan_for("Name", profile=profile, required=False), ([], []))

    def test_first_and_last_name_fields_still_resolve_from_the_profile(self) -> None:
        known = Known(profile=PROFILE)
        fields = [field("First name"), field("Last name")]
        plan, missing = build_plan(fields, known, JOB_ID, COMPANY)
        self.assertEqual(
            plan,
            [PlanItem("k:First name", "Ada", "profile:first_name"), PlanItem("k:Last name", "Lovelace", "profile:last_name")],
        )
        self.assertEqual(missing, [])

    def test_names_of_other_people_are_never_auto_filled(self) -> None:
        for label in ("Reference name", "Emergency contact name", "Manager name"):
            with self.subTest(label=label):
                plan, missing = plan_for(label)
                self.assertEqual(plan, [])
                self.assertEqual(missing, [f"k:{label}"])

    def test_company_style_names_are_not_filled_with_the_owner_name(self) -> None:
        for label in ("Company name", "Username"):
            with self.subTest(label=label):
                plan, missing = plan_for(label)
                self.assertNotIn("Ada Lovelace", [p.value for p in plan])
                self.assertEqual(missing, [f"k:{label}"])


class FullNamePrecedenceTests(unittest.TestCase):
    def test_saved_global_answer_beats_the_derived_value(self) -> None:
        answers = {norm("Name"): {"global": "Augusta Ada King"}}
        plan, missing = plan_for("Name", answers=answers)
        self.assertEqual(plan, [PlanItem("k:Name", "Augusta Ada King", "answer:global")])
        self.assertEqual(missing, [])

    def test_job_and_company_scopes_beat_global(self) -> None:
        by_scope = {"global": "Global Name", f"company:{COMPANY}": "Company Name", f"job:{JOB_ID}": "Job Name"}
        plan, _ = plan_for("Name *", answers={norm("Name"): by_scope})
        self.assertEqual(plan, [PlanItem("k:Name *", "Job Name", f"answer:job:{JOB_ID}")])
        del by_scope[f"job:{JOB_ID}"]
        plan, _ = plan_for("Name *", answers={norm("Name"): by_scope})
        self.assertEqual(plan, [PlanItem("k:Name *", "Company Name", f"answer:company:{COMPANY}")])

    def test_a_blank_saved_answer_falls_back_to_the_derived_value(self) -> None:
        plan, _ = plan_for("Name", answers={norm("Name"): {"global": "  "}})
        self.assertEqual(plan, [PlanItem("k:Name", "Ada Lovelace", DERIVED_SOURCE)])

    def test_saved_answer_applies_even_when_the_profile_name_is_incomplete(self) -> None:
        plan, missing = plan_for("Name", profile={}, answers={norm("Name"): {"global": "Ada L."}})
        self.assertEqual(plan, [PlanItem("k:Name", "Ada L.", "answer:global")])
        self.assertEqual(missing, [])

    def test_explicit_profile_full_name_beats_the_derived_value(self) -> None:
        plan, missing = plan_for("Full name", profile={**PROFILE, "full_name": "Augusta Ada King"})
        self.assertEqual(plan, [PlanItem("k:Full name", "Augusta Ada King", "profile:full_name")])
        self.assertEqual(missing, [])

    def test_saved_answer_beats_explicit_profile_full_name(self) -> None:
        plan, _ = plan_for(
            "Name",
            profile={**PROFILE, "full_name": "Profile Name"},
            answers={norm("Name"): {"global": "Saved Name"}},
        )
        self.assertEqual(plan, [PlanItem("k:Name", "Saved Name", "answer:global")])

    def test_blank_profile_full_name_falls_back_to_the_derived_value(self) -> None:
        plan, _ = plan_for("Name", profile={**PROFILE, "full_name": "   "})
        self.assertEqual(plan, [PlanItem("k:Name", "Ada Lovelace", DERIVED_SOURCE)])


class FullNameFieldKindTests(unittest.TestCase):
    def test_a_checkbox_named_name_is_not_filled(self) -> None:
        plan, missing = plan_for("Name", ftype="checkbox", required=False)
        self.assertEqual((plan, missing), ([], []))

    def test_a_select_whose_options_include_the_name_picks_it(self) -> None:
        plan, missing = plan_for("Name", ftype="select", options=["Grace Hopper", "Ada Lovelace"])
        self.assertEqual(plan, [PlanItem("k:Name", "Ada Lovelace", DERIVED_SOURCE)])
        self.assertEqual(missing, [])

    def test_a_select_without_a_matching_option_is_left_to_the_owner(self) -> None:
        plan, missing = plan_for("Name", ftype="select", options=["Grace Hopper", "Alan Turing"])
        self.assertEqual(plan, [])
        self.assertEqual(missing, ["k:Name"])
        self.assertEqual(plan_for("Name", ftype="select", options=["Grace Hopper"], required=False), ([], []))

    def test_multiple_fields_encode_the_value_as_json(self) -> None:
        plan, _ = plan_for("Name", multiple=True)
        self.assertEqual(plan, [PlanItem("k:Name", '["Ada Lovelace"]', DERIVED_SOURCE)])


class SkippedQuestionTests(unittest.TestCase):
    def test_skipped_on_this_job_is_neither_planned_nor_missing(self) -> None:
        answers = {norm("Do you fluently speak any other languages"): {f"job:{JOB_ID}": SKIPPED}}
        plan, missing = plan_for("Do you fluently speak any other languages", answers=answers, ftype="select")
        self.assertEqual((plan, missing), ([], []))

    def test_a_skip_on_another_job_does_not_hide_the_question(self) -> None:
        answers = {norm("Do you fluently speak any other languages"): {"job:other": SKIPPED}}
        plan, missing = plan_for("Do you fluently speak any other languages", answers=answers, ftype="select")
        self.assertEqual(plan, [])
        self.assertEqual(missing, ["k:Do you fluently speak any other languages"])

    def test_a_skip_is_never_used_as_a_value_from_a_wider_scope(self) -> None:
        answers = {norm("Languages"): {"global": SKIPPED}}
        plan, missing = plan_for("Languages", answers=answers)
        self.assertEqual(plan, [])
        self.assertEqual(missing, ["k:Languages"])


if __name__ == "__main__":
    unittest.main()


class GraduationDatePartTests(unittest.TestCase):
    def plan(self, label: str, options: list[str] | None = None) -> tuple[list[PlanItem], list[str]]:
        known = Known(profile={"graduation": "2026-12"})
        field = {"key": "k", "label": label, "type": "text", "required": True, "options": options or []}
        return build_plan([field], known, "j", "c")

    def test_year_and_month_fields_get_their_part(self) -> None:
        self.assertEqual(self.plan("Anticipated graduation date (year)")[0][0].value, "2026")
        self.assertEqual(self.plan("Anticipated graduation date (month)", ["November", "December"])[0][0].value, "December")
        self.assertEqual(self.plan("Anticipated graduation date (month)", ["1", "12"])[0][0].value, "12")

    def test_a_day_the_profile_does_not_hold_is_asked_not_invented(self) -> None:
        plan, missing = self.plan("Please input your anticipated graduation date* (day of month)")
        self.assertEqual((plan, missing), ([], ["k"]))
