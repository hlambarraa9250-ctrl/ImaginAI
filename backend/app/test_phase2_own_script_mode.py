import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.models import ScriptScene
from app.services.gemini_script import generate_visual_keywords


class Phase2OwnScriptModeTests(unittest.TestCase):
    def test_generate_visual_keywords_maps_by_scene_index(self):
        scenes = [
            ScriptScene(narration="مشهد عن إطلاق صاروخ", visual_keywords=["cinematic"], estimated_seconds=6.0),
            ScriptScene(narration="مشهد عن البورصة", visual_keywords=["documentary"], estimated_seconds=6.0),
        ]
        response_payload = {
            "items": [
                {"index": 1, "keywords": ["stock market traders", "financial district skyline"]},
                {"index": 0, "keywords": ["rocket launch pad", "mission control room"]},
            ]
        }
        response = SimpleNamespace(text=json.dumps(response_payload, ensure_ascii=False))
        client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_: response))

        with patch("app.services.gemini_script.get_gemini_keys", return_value=["k1"]), patch(
            "app.services.gemini_script.genai.Client", return_value=client
        ):
            keywords = asyncio.run(generate_visual_keywords(scenes, niche_id="space", style="documentary"))

        self.assertEqual(keywords[0], ["rocket launch pad", "mission control room"])
        self.assertEqual(keywords[1], ["stock market traders", "financial district skyline"])


if __name__ == "__main__":
    unittest.main()
