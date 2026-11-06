"""陪伴统计面板测试：数值格回填 + 成长等级/形态展示（v2.2）。"""
from WisePet.ui.stats_dialog import StatsDialog


def should_show_level_and_stage_when_growth_provided(qtbot, fake_screen):
    dialog = StatsDialog(fake_screen)
    qtbot.addWidget(dialog)

    dialog.set_values(days=3, messages=10, focus_minutes=5, focus_count=1,
                      level=7, stage="成长期")

    assert "Lv7" in dialog._growth.text()
    assert "成长期" in dialog._growth.text()
    assert dialog._values["days"].text() == "3"


def should_show_placeholder_when_level_absent(qtbot):
    dialog = StatsDialog()
    qtbot.addWidget(dialog)

    dialog.set_values(days=0, messages=0, focus_minutes=0, focus_count=0)

    assert "–" in dialog._growth.text()
