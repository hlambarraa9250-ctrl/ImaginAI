import unittest

from app.niches import find_niche_info


ARCHIVAL_PROVIDERS = {"wikimedia", "archive_org", "loc", "nara"}


def _sources(niche_id: str) -> list[str]:
    info = find_niche_info(niche_id)
    assert info is not None
    return [item.strip() for item in str(info.get("sources", "")).split(",") if item.strip()]


class Phase1NicheSourceTests(unittest.TestCase):
    def test_space_starts_with_nasa(self):
        self.assertEqual(_sources("space")[0], "nasa")

    def test_finance_and_psychology_avoid_archival_providers(self):
        finance_sources = _sources("company_building")
        psychology_sources = _sources("applied_psychology")
        self.assertTrue(ARCHIVAL_PROVIDERS.isdisjoint(finance_sources))
        self.assertTrue(ARCHIVAL_PROVIDERS.isdisjoint(psychology_sources))


if __name__ == "__main__":
    unittest.main()
