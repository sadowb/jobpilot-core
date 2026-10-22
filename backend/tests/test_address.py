import unittest

from jobpilot.domain.address import split_us_address


class SplitAddressTests(unittest.TestCase):
    def test_street_unit_city_state_zip(self) -> None:
        self.assertEqual(
            split_us_address("123 Example Ave, Unit 4B, Springfield IL 62704"),
            {"address_line1": "123 Example Ave", "postal_code": "62704", "address_line2": "Unit 4B"},
        )

    def test_without_a_unit_and_with_zip_plus_four(self) -> None:
        self.assertEqual(
            split_us_address("12 Main St, Springfield, IL 62704-1234"), {"address_line1": "12 Main St", "postal_code": "62704"}
        )

    def test_a_partial_address_is_not_guessed(self) -> None:
        for text in ("Springfield IL", "Example Ave, Springfield IL 62704", "123 Example Ave", ""):
            self.assertEqual(split_us_address(text), {})
