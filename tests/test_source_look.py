"""소스 아이콘·색 (설정 > 소스): 올바른 모양만 저장, 비우면 저장하지 않음 (종류별 기본)"""
from ym_player import config_store


def test_source_icon_color_cleaned():
    s = config_store._clean_source({"type": "podcast", "name": "팟", "icon": "podcast", "color": "#FF8800"}, None)
    assert s["icon"] == "podcast" and s["color"] == "#ff8800"
    bad = config_store._clean_source({"type": "podcast", "icon": "<script>", "color": "red"}, None)
    assert "icon" not in bad and "color" not in bad
    plain = config_store._clean_source({"type": "podcast"}, None)
    assert "icon" not in plain and "color" not in plain
    assert config_store.public_config({"sources": [s]})["sources"][0]["color"] == "#ff8800"   # 브라우저로 나감
