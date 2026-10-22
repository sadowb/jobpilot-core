"""Server-rendered ATS forms (real, trimmed public pages) and the JSON-LD reader both depend on."""

from __future__ import annotations

import unittest
from pathlib import Path

from jobpilot.domain.html_form import parse_html_form
from jobpilot.extract import _jsonld

FIXTURES = Path(__file__).parent / "fixtures" / "posting_forms"


def questions(name: str) -> dict[str, object]:
    found = parse_html_form((FIXTURES / f"{name}_form.html").read_text(), name)
    return {q.label: q for q in found}


class CareerPlugTests(unittest.TestCase):
    def setUp(self) -> None:
        self.q: dict[str, object] = questions("careerplug")

    def test_required_comes_from_the_wrapping_class_and_attribute(self) -> None:
        self.assertTrue(self.q["First Name"].required)  # type: ignore[attr-defined]
        self.assertTrue(self.q["Are you eligible to work in the United States without sponsorship"].required)  # type: ignore[attr-defined]
        self.assertFalse(self.q["Recent Employer"].required)  # type: ignore[attr-defined]

    def test_select_options_use_visible_text_not_ids(self) -> None:
        q = next(v for k, v in self.q.items() if k.startswith("Are you comfortable with basic coding"))
        self.assertEqual(q.options, ("Yes", "No"))  # type: ignore[attr-defined]
        self.assertTrue(all(not o.isdigit() for o in q.options))  # type: ignore[attr-defined]

    def test_placeholder_option_is_dropped(self) -> None:
        self.assertNotIn("State", self.q["States"].options)  # type: ignore[attr-defined]

    def test_no_hidden_fields(self) -> None:
        self.assertFalse(any("authenticity" in k.lower() or "job_id" in k.lower() for k in self.q))


class JazzTests(unittest.TestCase):
    def setUp(self) -> None:
        self.q: dict[str, object] = questions("jazzhr")

    def test_asterisk_means_required_and_is_not_part_of_the_label(self) -> None:
        q = self.q["Are you legally authorized to work in the US?"]
        self.assertTrue(q.required)  # type: ignore[attr-defined]
        self.assertEqual(q.options, ("Yes", "No"))  # type: ignore[attr-defined]

    def test_eeo_questions_are_voluntary_group(self) -> None:
        for label in ("Gender", "Race/Ethnicity", "Veteran status"):
            self.assertEqual(self.q[label].group, "eeoc", label)  # type: ignore[attr-defined]
            self.assertFalse(self.q[label].required)  # type: ignore[attr-defined]

    def test_hidden_xml_and_tracking_inputs_are_ignored(self) -> None:
        self.assertFalse(any("resumator" in k.lower() or "xml" in k.lower() for k in self.q))


class LeverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.q: dict[str, object] = questions("lever")

    def test_custom_question_label_comes_from_the_caption_element(self) -> None:
        q = self.q["Are you eligible to work in the United States?"]
        self.assertEqual(q.options, ("Yes", "No"))  # type: ignore[attr-defined]

    def test_hint_text_is_not_part_of_the_label(self) -> None:
        self.assertIn("Current location", self.q)
        self.assertFalse(any("No location found" in k for k in self.q))

    def test_resume_upload_is_a_file(self) -> None:
        self.assertEqual(self.q["Resume/CV"].type, "file")  # type: ignore[attr-defined]


class RobustnessTests(unittest.TestCase):
    def test_malformed_and_empty_markup_never_raise(self) -> None:
        for markup in ("", "<form><input", "<<<>>>", "<label for=x>Q<input id=x>", "<div>" * 5000):
            parse_html_form(markup, "x")

    def test_control_without_any_label_is_dropped_not_guessed(self) -> None:
        self.assertEqual(parse_html_form('<form><input type="text" name="zz"></form>', "x"), ())

    def test_aria_label_and_radio_group_with_legend(self) -> None:
        markup = (
            '<form><input aria-label="Nickname" name="n">'
            "<fieldset><legend>Remote ok?</legend>"
            '<label><input type="radio" name="r" value="1"> Yes</label>'
            '<label><input type="radio" name="r" value="0"> No</label></fieldset></form>'
        )
        found = {q.label: q for q in parse_html_form(markup, "x")}
        self.assertIn("Nickname", found)
        self.assertEqual(found["Remote ok?"].options, ("Yes", "No"))


class JsonLdTests(unittest.TestCase):
    def test_json_with_quote_entities_inside_strings_is_read(self) -> None:
        block = (
            '<script type="application/ld+json">{"@type":"JobPosting","title":"Dev",'
            '"description":"He said &quot;hi&quot; and left","hiringOrganization":{"name":"Acme"}}</script>'
        )
        found = _jsonld(block)
        self.assertEqual(found and found["role"], "Dev")

    def test_pay_interval_is_kept(self) -> None:
        block = (
            '<script type="application/ld+json">{"@type":"JobPosting","title":"Dev","baseSalary":{"currency":"USD",'
            '"value":{"minValue":"35000","maxValue":"50000","unitText":"YEAR"}}}</script>'
        )
        found = _jsonld(block)
        self.assertEqual(found and found["pay"], "USD 35000-50000/yr")


if __name__ == "__main__":
    unittest.main()
