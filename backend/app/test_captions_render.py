"""
Verification test script for custom ASS subtitle generation.
"""

from pathlib import Path
from app.services.render import _build_ass_filter, _extract_font_family_from_path, _generate_ass_subtitles

def test_ass_generation():
    test_lines = [
        {"start": 0.0, "end": 2.5, "text": "هذا مثال لشكل الترجمة على الفيديو"},
        {"start": 2.5, "end": 5.0, "text": "اختبار محرر الترجمة المخصص السريع"}
    ]
    
    config = {
        "color": "#FACC15",
        "effect": "shadow",
        "font": "Cairo",
        "size_percent": 120,
        "position": "top"
    }
    
    out_ass = Path("temp_test.ass")
    _generate_ass_subtitles(test_lines, str(out_ass), config)
    
    content = out_ass.read_text(encoding="utf-8")
    print("=== Generated ASS File Content ===")
    print(content)
    
    # Assertions
    assert "Cairo" in content, "Font name Cairo missing!"
    assert "&H0015CCFA" in content, "Color hex conversion #FACC15 -> &H0015CCFA failed!"
    assert "Alignment, MarginL, MarginR, MarginV" in content
    print("\n[SUCCESS] ASS Subtitle verification test passed successfully!")


def test_extract_bundled_cairo_family():
    cairo_path = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Cairo-Bold.ttf"
    family = _extract_font_family_from_path(cairo_path)
    assert family == "Cairo"


def test_ass_filter_uses_single_fontsdir():
    ass_filter = _build_ass_filter("/tmp/scene.ass", "/tmp/job_fonts")
    assert "fontsdir='/tmp/job_fonts'" in ass_filter
    assert ass_filter.count("fontsdir=") == 1
    assert ":" not in ass_filter.split("fontsdir='", 1)[1].split("'", 1)[0]


if __name__ == "__main__":
    test_ass_generation()
