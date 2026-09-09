"""档案顶栏：下拉 + 新建 + 管理菜单（offscreen）。"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QWidgetAction

from ui.widgets.profile_bar import ProfileBar, _COMBO_MAX_W, _COMBO_MIN_W


def test_profile_bar_chrome_is_compact(qapp):
    bar = ProfileBar()
    assert bar.combo.minimumWidth() == _COMBO_MIN_W
    assert bar.combo.maximumWidth() == _COMBO_MAX_W
    assert bar.btn_new.text() == "新建"
    assert bar.btn_manage.text() == "管理"
    assert bar.btn_manage.menu() is bar.menu
    assert not hasattr(bar, "btn_delete")
    assert not any(
        isinstance(w, QPushButton) and w.text() in ("另存为", "重命名", "删除", "导入", "导出")
        for w in bar.findChildren(QPushButton)
    )


def test_profile_bar_menu_groups_and_signals(qapp):
    bar = ProfileBar()
    texts = [a.text() for a in bar.menu.actions() if a.text()]
    assert texts == [
        "另存为…",
        "重命名…",
        "导入…",
        "导出…",
        "打开保存目录",
        "打开配置目录",
        "删除档案…",
    ]
    seps = [a for a in bar.menu.actions() if a.isSeparator()]
    assert len(seps) == 3
    assert isinstance(bar.act_delete, QWidgetAction)

    bar.set_profiles(["A", "B"], "A")
    seen: list[str] = []
    bar.save_as_requested.connect(lambda: seen.append("save_as"))
    bar.rename_requested.connect(lambda: seen.append("rename"))
    bar.import_requested.connect(lambda: seen.append("import"))
    bar.export_requested.connect(lambda: seen.append("export"))
    bar.open_save_dir_requested.connect(lambda: seen.append("save_dir"))
    bar.open_config_dir_requested.connect(lambda: seen.append("config_dir"))
    bar.delete_requested.connect(lambda: seen.append("delete"))
    bar.act_save_as.trigger()
    bar.act_rename.trigger()
    bar.act_import.trigger()
    bar.act_export.trigger()
    bar.act_open_save.trigger()
    bar.act_open_config.trigger()
    bar.act_delete.trigger()
    assert seen == [
        "save_as",
        "rename",
        "import",
        "export",
        "save_dir",
        "config_dir",
        "delete",
    ]


def test_profile_bar_delete_disabled_when_only_one(qapp):
    bar = ProfileBar()
    bar.set_profiles(["默认"], "默认")
    assert not bar.act_delete.isEnabled()
    assert not bar.act_delete.defaultWidget().isEnabled()
    bar.set_profiles(["钱江录像机", "备份"], "钱江录像机")
    assert bar.act_delete.isEnabled()
    assert bar.act_delete.defaultWidget().isEnabled()
    assert bar.active() == "钱江录像机"
