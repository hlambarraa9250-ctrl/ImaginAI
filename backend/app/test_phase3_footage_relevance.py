import unittest

from app.services.footage import _candidate_meets_metadata_quality, score_candidate


class Phase3FootageRelevanceTests(unittest.TestCase):
    def test_score_candidate_rewards_overlap_and_penalizes_blocked_terms(self):
        strong = {
            "title": "Rocket launch over galaxy and nebula",
            "description": "Mission control tracks the launch",
            "tags": "space,rocket,nebula",
            "is_image": False,
            "width": 1920,
            "height": 1080,
        }
        weak = {
            "title": "Military parade with soldiers and tanks",
            "description": "army ceremony in city",
            "tags": "war,military",
            "is_image": False,
            "width": 1920,
            "height": 1080,
        }
        keywords = ["rocket launch", "galaxy nebula"]
        blocked = ["military", "war", "army", "soldier"]
        self.assertGreater(score_candidate(strong, keywords, blocked), score_candidate(weak, keywords, blocked))

    def test_score_candidate_rejects_overlay_graphics_terms(self):
        candidate = {
            "title": "Stock market chart infographic",
            "description": "poster with arrows",
            "tags": "business",
            "is_image": True,
            "width": 2000,
            "height": 1200,
        }
        self.assertLess(score_candidate(candidate, ["stock market"], []), 0)

    def test_metadata_quality_gate_blocks_low_resolution_assets(self):
        low_res_image = {"source": "wikimedia", "is_image": True, "width": 1200, "height": 900}
        low_res_video = {"source": "pexels", "is_image": False, "width": 960, "height": 540}
        archive_low_video = {"source": "archive_org", "is_image": False, "width": 1280, "height": 600}
        good_video = {"source": "pexels", "is_image": False, "width": 1920, "height": 1080}

        self.assertFalse(_candidate_meets_metadata_quality(low_res_image))
        self.assertFalse(_candidate_meets_metadata_quality(low_res_video))
        self.assertFalse(_candidate_meets_metadata_quality(archive_low_video))
        self.assertTrue(_candidate_meets_metadata_quality(good_video))


if __name__ == "__main__":
    unittest.main()
