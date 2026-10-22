"""Provider parsers over real, trimmed public ATS responses (tests/fixtures/posting_forms)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from jobpilot.domain.posting_form import (
    PostingForm,
    parse_ashby,
    parse_ashby_form,
    parse_bamboo,
    parse_greenhouse,
    parse_smartrecruiters,
    parse_workable,
)

FIXTURES = Path(__file__).parent / "fixtures" / "posting_forms"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class GreenhouseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.form = parse_greenhouse(load("greenhouse_job.json"))
        self.by_label = {q.label: q for q in self.form.questions}

    def test_meta(self) -> None:
        meta = self.form.meta
        self.assertEqual(meta.role, "2027 Software Engineering Intern")
        self.assertEqual(meta.company, "Voyager Technologies, Inc.")
        self.assertEqual(meta.location, "Long Beach")
        self.assertEqual(meta.pay, "$23.50–$30/hr")
        self.assertEqual(meta.department, "Intern")

    def test_select_options_come_from_the_provider(self) -> None:
        q = self.by_label["Are you legally authorized to work in the United States for any employer?"]
        self.assertEqual((q.type, q.required, q.options), ("select", True, ("Yes", "No")))

    def test_standard_fields_and_types(self) -> None:
        self.assertEqual(self.by_label["First Name"].group, "standard")
        self.assertEqual(self.by_label["First Name"].type, "text")
        self.assertTrue(any(q.type == "file" for q in self.form.questions))

    def test_eeoc_questions_are_a_separate_group(self) -> None:
        eeoc = [q for q in self.form.questions if q.group == "eeoc"]
        self.assertTrue(eeoc)
        self.assertTrue(all(not q.required for q in eeoc))
        self.assertNotIn("eeoc", {q.group for q in self.form.questions if q.group != "eeoc"})

    def test_keys_unique_and_inventory_shape(self) -> None:
        keys = [q.key for q in self.form.questions]
        self.assertEqual(len(keys), len(set(keys)))
        item = self.by_label["First Name"].inventory_item()
        self.assertEqual(set(item), {"key", "label", "type", "required"})

    def test_paste_box_keeps_the_pages_plain_label(self) -> None:
        form = parse_greenhouse(
            {
                "questions": [
                    {
                        "label": "Cover Letter",
                        "required": False,
                        "fields": [
                            {"name": "cover_letter", "type": "input_file", "values": []},
                            {"name": "cover_letter_text", "type": "textarea", "values": []},
                        ],
                    }
                ]
            }
        )
        self.assertEqual({(q.type, q.label) for q in form.questions}, {("file", "Cover Letter"), ("textarea", "Cover Letter")})

    def test_garbage_does_not_raise(self) -> None:
        form = parse_greenhouse({"questions": [None, 3, {"label": 7, "fields": "x"}], "compliance": "no"})
        self.assertEqual(form.questions, ())


class AshbyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.questions = parse_ashby_form(load("ashby_form.json"))
        self.by_label = {q.label: q for q in self.questions}

    def test_boolean_becomes_yes_no_select(self) -> None:
        q = self.by_label["Are you currently authorized to work in the United States?"]
        self.assertEqual((q.type, q.options, q.required), ("select", ("Yes", "No"), True))

    def test_value_select_keeps_provider_options(self) -> None:
        q = self.by_label["How did you hear about us?"]
        self.assertEqual(q.type, "select")
        self.assertIn("LinkedIn", q.options)

    def test_system_fields_are_standard(self) -> None:
        self.assertEqual(self.by_label["Name"].group, "standard")
        self.assertEqual(self.by_label["Resume"].type, "file")
        self.assertEqual(self.by_label["Ideal start date in office"].type, "date")

    def test_meta_and_form_from_one_response(self) -> None:
        form = parse_ashby(load("ashby_form.json"), company="Fab2")
        self.assertEqual(form.provider, "ashby")
        self.assertEqual(form.meta.role, "Fab Software Engineering Intern - Winter")
        self.assertEqual(form.meta.company, "Fab2")
        self.assertEqual(form.meta.location, "Austin")
        self.assertEqual(form.meta.work_mode, "On-site")
        self.assertEqual(form.meta.employment_type, "Internship")
        self.assertEqual(form.meta.pay, "$114K - $131K")
        self.assertTrue(form.questions)

    def test_errors_payload_yields_no_questions(self) -> None:
        self.assertEqual(parse_ashby_form({"errors": [{"message": "x"}], "data": None}), ())


class BambooTests(unittest.TestCase):
    def setUp(self) -> None:
        self.form: PostingForm = parse_bamboo(load("bamboo_detail.json"))
        self.by_key = {q.key: q for q in self.form.questions}

    def test_meta(self) -> None:
        self.assertEqual(self.form.meta.role, "Software Engineering Intern (Winter Session)")
        self.assertEqual(self.form.meta.employment_type, "Intern")
        self.assertEqual(self.form.meta.pay, "$20-$25/hr depending on experience")
        self.assertEqual(self.form.meta.location, "Middleboro, Massachusetts, United States")

    def test_standard_fields(self) -> None:
        self.assertEqual(self.by_key["bamboo:email"].type, "email")
        self.assertTrue(self.by_key["bamboo:email"].required)
        self.assertEqual(self.by_key["bamboo:resumeFileId"].type, "file")
        self.assertIn("United States", self.by_key["bamboo:countryId"].options)

    def test_custom_yes_no_questions(self) -> None:
        q = self.by_key["bamboo:q233"]
        self.assertEqual((q.type, q.options, q.required), ("select", ("Yes", "No"), False))


class WorkableTests(unittest.TestCase):
    def setUp(self) -> None:
        data = load("workable_job.json")
        self.form = parse_workable(data["job"], data["form"])
        self.by_key = {q.key: q for q in self.form.questions}

    def test_meta(self) -> None:
        self.assertEqual(self.form.provider, "workable")
        self.assertEqual(self.form.meta.role, "Open-Source Machine Learning Engineer - EMEA Remote")
        self.assertIn("Paris", self.form.meta.location or "")
        self.assertEqual(self.form.meta.work_mode, "Remote")

    def test_field_types_and_required(self) -> None:
        self.assertEqual(self.by_key["workable:email"].type, "email")
        self.assertTrue(self.by_key["workable:firstname"].required)
        self.assertEqual(self.by_key["workable:resume"].type, "file")
        self.assertEqual(self.by_key["workable:firstname"].group, "standard")

    def test_boolean_and_dropdown_become_selects_with_provider_options(self) -> None:
        booleans = [q for q in self.form.questions if q.options == ("Yes", "No")]
        self.assertTrue(booleans)
        dropdowns = [q for q in self.form.questions if q.type == "select" and q.options != ("Yes", "No")]
        self.assertTrue(dropdowns and all(q.options for q in dropdowns))

    def test_repeating_groups_are_left_to_the_browser(self) -> None:
        self.assertNotIn("workable:education", self.by_key)

    def test_garbage_does_not_raise(self) -> None:
        self.assertEqual(parse_workable({}, [None, 3, {"fields": "x"}]).questions, ())


class SmartRecruitersTests(unittest.TestCase):
    def test_metadata_only(self) -> None:
        form = parse_smartrecruiters(load("smartrecruiters_posting.json"))
        self.assertEqual(form.provider, "smartrecruiters")
        self.assertTrue(form.meta.role)
        self.assertTrue(form.meta.company)
        self.assertEqual(form.questions, ())


if __name__ == "__main__":
    unittest.main()
