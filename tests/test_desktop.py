from pathlib import Path


def test_tray_icon_uses_the_same_ave_favicon_colors():
    source = (Path(__file__).resolve().parents[1] / "app" / "desktop.py").read_text(encoding="utf-8")

    assert "rounded_rectangle" in source
    assert '"AVE"' in source
    assert '"#1f2937"' in source
    assert '"#f97316"' in source
