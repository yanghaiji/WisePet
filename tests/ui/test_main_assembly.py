"""组装根冒烟：装配顺序、恢复/落盘、事件到控件的闭环（技术方案 §4 / §9 Step 6）。"""
from __future__ import annotations


import logging
from dataclasses import replace

import pytest
from PySide6.QtGui import QColor, QCursor, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon

import main
from WisePet.app.chat_service import ChatService
from WisePet.domain.chat_record import day_key
from WisePet.domain.errors import AIGatewayError
from WisePet.domain.events import (
    ChatErrorEvent,
    KIND_WATER,
    HelpRequestEvent,
    ReminderDueEvent,
    SpeakRequestedEvent,
)
from WisePet.domain.pet import Pet
from WisePet.domain.position import PositionVO
from WisePet.domain.state_machine import BehaviorState
from WisePet.domain.vitals import VitalsVO
from WisePet.infra.config_repo import JsonConfigRepository
from WisePet.infra.pet_state_repo import PetStateRepository
from WisePet.ui.screen_bounds import ScreenBounds
from fakes import FakeClock


def _build(qapp, tmp_path, assets_dir, clock=None, tray_available=None):
    return main.PetApplication(qapp,
                               config_repo=JsonConfigRepository(tmp_path),
                               state_repo=PetStateRepository(tmp_path),
                               assets_dir=assets_dir,
                               clock=clock or FakeClock(),
                               tray_available=tray_available)


@pytest.fixture
def pet_app(qtbot, qapp, tmp_path, assets_dir):
    app = _build(qapp, tmp_path, assets_dir)
    for widget in (app.window, app._bubble, app._chat_input,
                   app._chat_window, app._menu):
        qtbot.addWidget(widget)
    yield app
    app.shutdown()


def _workarea():
    return ScreenBounds().bounds_for(main.PET_SIZE, main.PET_SIZE)


def should_place_new_pet_at_workarea_bottom_right_when_no_snapshot(pet_app):
    _x1, _y1, x2, y2 = _workarea()

    assert pet_app.pet.position.x == x2
    assert pet_app.pet.position.y == y2
    assert pet_app.pet.name == pet_app._config.persona.name


def should_clamp_restored_position_when_snapshot_out_of_workarea(qapp, tmp_path, assets_dir):
    PetStateRepository(tmp_path).save(
        Pet(pet_id=main.PET_ID, name="墨墨", position=PositionVO(999_999, 999_999)))
    x1, y1, x2, y2 = _workarea()

    app = _build(qapp, tmp_path, assets_dir)
    try:
        assert (x1 <= app.pet.position.x <= x2) and (y1 <= app.pet.position.y <= y2)
        assert app.pet.name == "墨墨"
    finally:
        app.shutdown()


def should_persist_state_only_after_save_interval_when_ticking(pet_app, tmp_path):
    clock = pet_app._clock
    interval = pet_app._config.vitals.save_interval_s

    clock.advance(interval - 10)
    pet_app.tick()
    assert not (tmp_path / "pet_state.json").exists()

    clock.advance(20)
    pet_app.tick()
    assert PetStateRepository(tmp_path).load().pet_id == main.PET_ID


def should_enter_thinking_and_enqueue_when_message_sent(pet_app):
    pet_app.send_message("你好呀")

    assert pet_app.pet.state is BehaviorState.THINKING
    assert pet_app._chat.has_pending()          # 未经 worker，主线程不发请求
    assert pet_app._streaming is True


def should_short_circuit_quick_reminder_without_ai_roundtrip(pet_app):
    """「N分钟后提醒我X」走本地快速通道：立即排期，不进 AI 队列（真机回归）。"""
    pet_app.send_message("3分钟后提醒我开会")

    assert len(pet_app._one_shot.pending) == 1
    due_at, text = pet_app._one_shot.pending[0]
    assert text == "开会"
    assert due_at > pet_app._clock.now()
    assert pet_app._chat.has_pending() is False     # 没有走 AI 往返
    assert pet_app._streaming is False


def should_drop_blank_message_without_state_change_when_sent(pet_app):
    pet_app.send_message("   ")

    assert pet_app.pet.state is BehaviorState.IDLE
    assert pet_app._streaming is False


def should_show_bubble_and_speak_when_speak_requested(pet_app):
    pet_app._bus.publish(SpeakRequestedEvent(text="我在这儿呢", source="system"))

    assert pet_app._bubble.current_text() == "我在这儿呢"
    assert pet_app.pet.state is BehaviorState.SPEAKING


def should_return_to_idle_when_bubble_closed(pet_app):
    pet_app._bus.publish(SpeakRequestedEvent(text="说完了", source="system"))

    pet_app._bubble.hide_bubble()

    assert pet_app.pet.state is BehaviorState.IDLE


def should_offer_ack_and_rearm_when_reminder_due(pet_app):
    pet_app._bus.publish(ReminderDueEvent(kind=KIND_WATER, text="该喝水啦"))

    assert pet_app._reminder_kind == KIND_WATER
    assert pet_app._bubble.current_text() == "该喝水啦"
    assert pet_app._bubble.cards[-1].title_label.text() == "喝水提醒"
    assert pet_app._bubble.cards[-1].ack_btn.isVisibleTo(pet_app._bubble)

    pet_app._bubble.cards[-1].ack_btn.click()

    assert pet_app._reminder_kind is None
    assert not pet_app._bubble.isVisible()


def should_move_pet_to_cursor_when_reminder_due(pet_app):
    cursor = QCursor.pos()
    x1, y1, x2, y2 = _workarea()
    expected_x = max(x1, min(x2, cursor.x() - main.PET_SIZE // 2))

    pet_app._bus.publish(ReminderDueEvent(kind=KIND_WATER, text="喝水"))

    assert pet_app.pet.position.x == expected_x
    assert y1 <= pet_app.pet.position.y <= y2


def should_reply_locally_without_ai_key_when_message_processed(pet_app):
    """不配 AI Key 也要能对话：本地小脑接管，而不是只会说「连不上」。"""
    pet_app.send_message("你好呀")

    assert pet_app._chat.process_next() is True

    text = pet_app._bubble.current_text()
    assert text and "你" in text               # v1.4 C6：首句先接住用户，而不是自报家门
    assert "连上" not in text                     # 区别于 AUTH 失败的「我还没连上大脑哦」
    assert pet_app._streaming is False
    assert pet_app.pet.state is BehaviorState.SPEAKING
    assert pet_app._session.last_role == "assistant"


def should_use_readable_fallback_when_chat_error(pet_app):
    pet_app.send_message("在吗")

    pet_app._bus.publish(ChatErrorEvent(kind=AIGatewayError.NETWORK, detail="dns down"))

    assert pet_app._bubble.current_text() == ChatService.fallback_text(AIGatewayError.NETWORK)
    assert pet_app._streaming is False
    assert pet_app.pet.state is BehaviorState.SPEAKING


def should_fill_persona_placeholders_when_system_prompt_built(pet_app):
    prompt = pet_app._system_prompt()

    assert "{name}" not in prompt and "{vitals_summary}" not in prompt
    assert pet_app.pet.name in prompt
    assert pet_app.pet.vitals_summary() in prompt


def should_keep_prompt_readable_when_template_malformed(pet_app):
    persona = replace(pet_app._config.persona, system_prompt="坏模板 {unknown}")
    pet_app._config = replace(pet_app._config, persona=persona)

    prompt = pet_app._system_prompt()

    assert prompt.startswith("坏模板")
    assert pet_app.pet.vitals_summary() in prompt


def should_persist_and_rewire_services_when_config_applied(pet_app, tmp_path):
    cfg = replace(pet_app._config,
                  pet=replace(pet_app._config.pet, max_turns=3),
                  reminders=replace(pet_app._config.reminders,
                                    water=replace(pet_app._config.reminders.water,
                                                  enabled=False)))
    fired = []
    pet_app._router.on(ReminderDueEvent, fired.append)

    pet_app.apply_config(cfg)

    assert JsonConfigRepository(tmp_path).load().pet.max_turns == 3
    assert pet_app._session.max_turns == 3

    pet_app._clock.advance(50 * 60)      # 跨过喝水原 45min 间隔，未到久坐 60min
    pet_app.tick()
    assert fired == []                   # 已关闭的提醒不再播报


class TestChatEntryPoints:
    """FR v1.10 C18/C19：两入口互斥、历史窗可继续聊、浮窗跟着宠物走。"""

    def should_open_input_bar_not_history_when_window_clicked(self, pet_app):
        pet_app.window.clicked.emit()

        assert pet_app._chat_input.isVisible()
        assert not pet_app._chat_window.isVisible()
        assert pet_app.controller.conversation_active is True

    def should_not_auto_show_history_on_startup(self, pet_app):
        pet_app.start()
        assert not pet_app._chat_window.isVisible()

    def should_open_history_only_from_context_menu(self, pet_app):
        pet_app._menu.act_history.trigger()

        assert pet_app._chat_window.isVisible()
        assert "历史对话" in pet_app._menu.act_history.text()
        assert not pet_app._chat_input.isVisible()
        assert pet_app.controller.conversation_active is True

    def should_toggle_history_from_context_menu_twice(self, pet_app):
        pet_app._menu.act_history.trigger()
        pet_app._menu.act_history.trigger()

        assert not pet_app._chat_window.isVisible()
        assert pet_app.controller.conversation_active is False

    def should_close_input_bar_when_history_opened(self, pet_app):
        pet_app.window.clicked.emit()
        assert pet_app._chat_input.isVisible()

        pet_app._menu.act_history.trigger()

        assert not pet_app._chat_input.isVisible()
        assert pet_app._chat_window.isVisible()
        assert pet_app.controller.conversation_active is True

    def should_keep_conversation_active_while_either_ui_open(self, pet_app):
        pet_app.window.clicked.emit()             # 输入条开
        pet_app._chat_window.show_near(
            pet_app._window.window_rect())        # 绕过互斥直接开，验证合并判定
        assert pet_app.controller.conversation_active is True

        pet_app._chat_window.hide()               # 关掉历史窗，输入条还开着
        assert pet_app.controller.conversation_active is True

        pet_app._chat_input.hide()
        assert pet_app.controller.conversation_active is False

    def should_route_chat_input_submitted_to_chat_service(self, pet_app):
        pet_app.window.clicked.emit()
        pet_app._chat_input.line_edit.setText("你好呀")
        pet_app._chat_input.line_edit.send_requested.emit()

        assert pet_app.pet.state is BehaviorState.THINKING
        assert pet_app._chat.has_pending()

    def should_route_history_submitted_to_chat_service(self, pet_app):
        pet_app._menu.act_history.trigger()
        pet_app._chat_window.line_edit.setText("接着聊")
        pet_app._chat_window.line_edit.send_requested.emit()

        assert pet_app.pet.state is BehaviorState.THINKING
        assert pet_app._chat.has_pending()

    def should_focus_history_instead_of_opening_bar_when_clicked(self, pet_app):
        pet_app._menu.act_history.trigger()

        pet_app.window.clicked.emit()

        assert pet_app._chat_window.isVisible()
        assert not pet_app._chat_input.isVisible()

    def should_hide_both_chat_uis_when_pet_hidden(self, pet_app):
        pet_app.start()
        pet_app.window.clicked.emit()
        pet_app._menu.act_history.trigger()
        pet_app.toggle_visible()

        assert not pet_app._chat_input.isVisible()
        assert not pet_app._chat_window.isVisible()
        assert pet_app.controller.conversation_active is False

    def should_follow_pet_when_input_bar_moves(self, pet_app):
        pet_app.start()
        pet_app.window.clicked.emit()
        before = pet_app._chat_input.pos()

        pet_app.pet.position = PositionVO(200, 200)
        pet_app._sync_window()

        assert pet_app._chat_input.pos() != before
        rect = pet_app._window.window_rect()
        assert (pet_app._chat_input.x() + pet_app._chat_input.width() <= rect.left()
                or pet_app._chat_input.x() >= rect.right())

    def should_follow_pet_when_history_window_open(self, pet_app):
        pet_app.start()
        pet_app._menu.act_history.trigger()
        before = pet_app._chat_window.pos()

        pet_app.pet.position = PositionVO(200, 200)
        pet_app._sync_window()

        assert pet_app._chat_window.pos() != before
        rect = pet_app._window.window_rect()
        assert (pet_app._chat_window.x() + pet_app._chat_window.width() <= rect.left()
                or pet_app._chat_window.x() >= rect.right())


class TestChatSessionLifecycle:
    """FR v1.8 C13/C14：会话状态可见、按天入库、重开恢复、对话中不动。"""

    def should_persist_turn_and_show_thinking_when_message_sent(self, pet_app):
        pet_app.send_message("今天怎么样")

        records = pet_app._chat_log_repo.load(day_key())
        assert len(records) == 1
        assert records[0].content == "今天怎么样"
        assert records[0].role == "user"
        assert "你好" not in pet_app._chat_window.status_text  # 状态栏不是历史区
        assert "思考中" in pet_app._chat_window.status_text
        assert "本地小脑" in pet_app._chat_window.status_text
        # 历史窗默认不显示，所以状态必须在聊天时看得见的输入条上同样可见
        assert "思考中" in pet_app._chat_input.status_text
        assert "本地小脑" in pet_app._chat_input.status_text
        assert "今天怎么样" in pet_app._chat_window._history.toPlainText()

    def should_persist_assistant_turn_and_done_state_when_reply_finished(self, pet_app):
        pet_app.send_message("你好呀")
        pet_app._chat.process_next()

        records = pet_app._chat_log_repo.load(day_key())
        assert [r.role for r in records] == ["user", "assistant"]
        assert records[1].source == "local"
        assert "回复完成" in pet_app._chat_window.status_text

    def should_show_error_state_when_gateway_fails(self, pet_app):
        pet_app.send_message("在吗")
        pet_app._bus.publish(ChatErrorEvent(kind=AIGatewayError.TIMEOUT, detail="slow"))

        assert "出错了" in pet_app._chat_window.status_text
        assert AIGatewayError.TIMEOUT in pet_app._chat_window.status_text

    def should_restore_today_history_when_restarted(self, qapp, tmp_path, assets_dir, qtbot):
        first = _build(qapp, tmp_path, assets_dir)
        qtbot.addWidget(first._chat_window)
        first.send_message("记住这句话")
        first.shutdown()

        second = _build(qapp, tmp_path, assets_dir)
        qtbot.addWidget(second._chat_window)
        try:
            second.start()
            assert "记住这句话" in second._chat_window._history.toPlainText()
        finally:
            second.shutdown()

    def should_restore_chat_session_so_pet_remembers_when_restarted(
            self, qapp, tmp_path, assets_dir, qtbot):
        """FR v1.10 C16：记录不只要能看，还要回灌给模型上下文。"""
        first = _build(qapp, tmp_path, assets_dir)
        qtbot.addWidget(first.window)
        first.send_message("我叫小明，记住啦")
        first._chat.process_next()
        first.shutdown()

        second = _build(qapp, tmp_path, assets_dir)
        qtbot.addWidget(second.window)
        try:
            second.start()
            turns = [(t.role, t.content) for t in second._session.turns]
            assert ("user", "我叫小明，记住啦") in turns
            assert any(role == "assistant" for role, _ in turns)
            messages = second._session.build_messages(second._system_prompt())
            assert messages[0]["role"] == "system"
            assert any(m["content"] == "我叫小明，记住啦" for m in messages[1:])
        finally:
            second.shutdown()

    def should_not_duplicate_records_to_disk_when_session_restored(
            self, qapp, tmp_path, assets_dir, qtbot):
        """恢复只灌内存，不能把旧记录又写一遍盘。"""
        first = _build(qapp, tmp_path, assets_dir)
        qtbot.addWidget(first.window)
        first.send_message("一句话")
        first.shutdown()
        assert first._chat_log_repo.count() == 1

        second = _build(qapp, tmp_path, assets_dir)
        qtbot.addWidget(second.window)
        try:
            second.start()
            assert second._chat_log_repo.count() == 1
        finally:
            second.shutdown()

    def should_show_saved_count_on_input_bar_when_restarted(
            self, qapp, tmp_path, assets_dir, qtbot):
        first = _build(qapp, tmp_path, assets_dir)
        qtbot.addWidget(first.window)
        first.send_message("一句话")
        first.shutdown()

        second = _build(qapp, tmp_path, assets_dir)
        qtbot.addWidget(second._chat_input)
        try:
            second.start()
            assert second._chat_input.history_count == 1
            assert "已记 1 条" in second._chat_input.status_text
        finally:
            second.shutdown()

    def should_keep_pet_still_while_chat_input_open(self, pet_app):
        pet_app.start()
        pet_app.window.clicked.emit()                 # 打开输入条 → 会话冻结
        initial = pet_app.pet.position
        for _ in range(20):
            pet_app._clock.advance(0.1)
            pet_app.tick_motion()

        assert pet_app.pet.position == initial
        assert pet_app.controller.is_moving() is False


def should_stop_worker_and_save_state_when_shutdown(qapp, tmp_path, assets_dir, qtbot):
    app = _build(qapp, tmp_path, assets_dir)
    qtbot.addWidget(app.window)

    app.start()
    assert app._worker.isRunning()
    assert app.window.isVisible()

    app.shutdown()

    assert not app._worker.isRunning()
    assert PetStateRepository(tmp_path).load().pet_id == main.PET_ID


def should_log_chat_brain_local_when_started_without_key(pet_app, caplog):
    """启动就说清“回复由谁作答”：本地小脑也逐段上屏，看气泡分不出来源。"""
    with caplog.at_level(logging.INFO):
        pet_app.start()

    assert "chat brain=local" in caplog.text


def should_log_chat_brain_ai_when_key_saved(pet_app, caplog):
    cfg = pet_app._config
    with caplog.at_level(logging.INFO):
        pet_app.apply_config(replace(cfg, ai=replace(cfg.ai, api_key="sk-test",
                                                    model="glm-4-flash")))

    assert "chat brain=ai" in caplog.text
    assert "model=glm-4-flash" in caplog.text


class TestFloorAndMotionTimer:
    """贴地重力由组装根驱动：domain 不持时钟，计时器归 main（PRD v2.0 C10）。

    走路 60px/s、下落 900px/s 在 1s 粒度下都是“每秒瞬移一次”，所以位移单独用
    50ms 的运动计时器推，且只在宠物真在动时才跑（不影响办公的硬约束）。
    """

    def should_drop_restored_pet_to_floor_when_snapshot_was_in_air(self, qapp, tmp_path,
                                                                   assets_dir):
        """旧快照里的半空 Y 启动时一次性落回地面，而不是等重力慢慢掉。"""
        repo = JsonConfigRepository(tmp_path)
        cfg = repo.load()
        repo.save(replace(cfg, pet=replace(cfg.pet, roam=False)))   # 贴地语义：关掉漫游
        PetStateRepository(tmp_path).save(
            Pet(pet_id=main.PET_ID, name="墨墨", position=PositionVO(300, 10)))
        _x1, _y1, _x2, y2 = _workarea()

        app = _build(qapp, tmp_path, assets_dir)
        try:
            assert app.pet.position.y == y2
            assert app.pet.position.x == 300        # 只改 Y，X 保留原位
        finally:
            app.shutdown()

    def should_keep_snapshot_y_when_stick_to_floor_disabled(self, qapp, tmp_path, assets_dir):
        """关掉贴地即完全恢复 v1.x 自由漂浮：启动不重钳 Y。"""
        repo = JsonConfigRepository(tmp_path)
        cfg = repo.load()
        repo.save(replace(cfg, pet=replace(cfg.pet, stick_to_floor=False)))
        PetStateRepository(tmp_path).save(
            Pet(pet_id=main.PET_ID, name="墨墨", position=PositionVO(300, 10)))

        app = _build(qapp, tmp_path, assets_dir)
        try:
            assert app.pet.position.y == 10
        finally:
            app.shutdown()

    def should_lift_low_mood_to_positive_on_restore(self, qapp, tmp_path, assets_dir):
        """开机元气启动：旧快照三维贴底时全部托底到 60，别一开机就拉着脸/喊饿喊困。"""
        PetStateRepository(tmp_path).save(
            Pet(pet_id=main.PET_ID, name="墨墨", position=PositionVO(300, 10),
                vitals=VitalsVO(hunger=40.0, mood=10.0, energy=40.0)))

        app = _build(qapp, tmp_path, assets_dir)
        try:
            assert app.pet.vitals.mood == 60.0
            assert app.pet.vitals.hunger == 60.0      # 三维都托底，不再“其它维度不动”
            assert app.pet.vitals.energy == 60.0
        finally:
            app.shutdown()

    def should_not_ask_for_help_on_fresh_start(self, qapp, tmp_path, assets_dir):
        """开机把精力/饱食托底到元气基线，第一拍不该又冒“好困呀”求助（用户反馈）。"""
        PetStateRepository(tmp_path).save(
            Pet(pet_id=main.PET_ID, name="墨墨", position=PositionVO(300, 10),
                vitals=VitalsVO(hunger=8.0, mood=8.0, energy=8.0)))
        app = _build(qapp, tmp_path, assets_dir)
        try:
            helps = []
            app._router.on(HelpRequestEvent, helps.append)

            app.start()
            app.tick()

            assert helps == []
            assert app.pet.vitals.energy >= 60.0
        finally:
            app.shutdown()

    def should_keep_motion_timer_off_when_pet_rests_on_floor(self, pet_app):
        pet_app.start()

        pet_app.tick()

        assert pet_app._motion_timer.isActive() is False

    def should_run_motion_timer_while_falling_and_stop_after_landed(self, pet_app):
        _x1, y1, _x2, y2 = _workarea()
        pet_app._config.pet.roam = False          # 本用例验证贴地重力，关掉漫游
        pet_app.start()
        pet_app.pet.position = PositionVO(pet_app.pet.position.x, y1 + 20)   # 手动丢到半空

        pet_app.tick()                                                        # 同步后起跳
        assert pet_app._motion_timer.isActive() is True

        for _ in range(40):
            pet_app._clock.advance(0.1)
            pet_app.tick_motion()

        assert pet_app.pet.position.y == y2
        assert pet_app._motion_timer.isActive() is False

    def should_stop_motion_timer_when_shutdown(self, qapp, tmp_path, assets_dir, qtbot):
        """两个计时器都得停，否则退出后仍有后台唤醒。"""
        app = _build(qapp, tmp_path, assets_dir)
        qtbot.addWidget(app.window)
        app._config.pet.roam = False              # 需要宠物处于「下落中」：关掉漫游
        app.start()
        app.pet.position = PositionVO(app.pet.position.x, _workarea()[1] + 20)
        app.tick()
        assert app._motion_timer.isActive() is True

        app.shutdown()

        assert app._tick_timer.isActive() is False
        assert app._motion_timer.isActive() is False


class TestHideStopsFramePolling:
    """隐藏即停帧轮询：没人在看时不必每秒 4~16 次取帧 + 镜像 + 重绘（PRD NFR 空闲 CPU <3%）。

    主 tick 不能一起停——衰减、提醒、落盘都挂在它上面，停了就等于桌宠「假死」。
    """

    def should_stop_frame_polling_when_pet_hidden(self, pet_app):
        pet_app.start()
        assert pet_app._animator.is_running is True

        pet_app.toggle_visible()                      # 显示中 → 藏起来

        assert pet_app.window.isVisible() is False
        assert pet_app._animator.is_running is False
        assert pet_app._tick_timer.isActive() is True

    def should_resume_frame_polling_when_pet_shown_again(self, pet_app):
        pet_app.start()
        pet_app.toggle_visible()

        pet_app.toggle_visible()                      # 藏起来 → 再显示

        assert pet_app.window.isVisible() is True
        assert pet_app._animator.is_running is True


def _fill(directory, color: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pix = QPixmap(32, 32)
    pix.fill(QColor(color))
    pix.save(str(directory / "01.png"), "PNG")


def should_pick_up_replaced_asset_only_after_reload(qapp, tmp_path):
    """换素材不重启也能生效：右键「重新加载素材」清帧缓存重读磁盘。

    帧图按文件路径缓存在内存里，AI 图覆盖同名 PNG 后路径没变，不重载就一直在播旧帧。
    """
    root = tmp_path / "assets"
    idle = root / "pet" / "cat" / "idle"
    _fill(idle, "#FF0000")
    app = _build(qapp, tmp_path, root)
    try:
        app._animator.set_action("idle")
        assert app._animator.uses_placeholder is False
        assert app._animator.current_frame().toImage().pixelColor(6, 6).red() == 255

        _fill(idle, "#00FF00")
        assert app._animator.current_frame().toImage().pixelColor(6, 6).green() == 0

        app.reload_assets()
        assert app._animator.current_frame().toImage().pixelColor(6, 6).green() == 255
        assert app._menu.act_reload.isEnabled() is True
    finally:
        app.shutdown()


class TestTrayRestore:
    """FR v1.11 C21：宠物藏起来之后，系统托盘是唯一还能点的入口。"""

    def should_keep_tray_menu_as_restore_entry_when_pet_hidden(self, pet_app):
        pet_app.start()
        pet_app.toggle_visible()

        assert pet_app._tray.isVisible()
        assert pet_app._tray.contextMenu() is pet_app._menu
        assert pet_app._menu.act_toggle.text() == "显示宠物"
        assert "已隐藏" in pet_app._tray.toolTip()

    def should_restore_pet_from_tray_menu_when_hidden(self, qapp, tmp_path,
                                                       assets_dir, qtbot):
        app = _build(qapp, tmp_path, assets_dir, tray_available=True)
        qtbot.addWidget(app.window)
        try:
            app.start()
            app.toggle_visible()
            assert app._menu.act_toggle.isEnabled()

            app._menu.act_toggle.trigger()

            assert app.window.isVisible()
            assert app._animator.is_running is True
            assert app._menu.act_toggle.text() == "隐藏宠物"
            assert "已隐藏" not in app._tray.toolTip()
        finally:
            app.shutdown()

    def should_restore_pet_when_tray_icon_double_clicked(self, pet_app):
        pet_app.start()
        pet_app.toggle_visible()

        pet_app._tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)

        assert pet_app.window.isVisible()
        assert pet_app._animator.is_running is True

    def should_disable_hide_when_tray_unavailable(self, qapp, tmp_path, assets_dir):
        app = _build(qapp, tmp_path, assets_dir, tray_available=False)
        try:
            assert not app._menu.act_toggle.isEnabled()
            assert "托盘" in app._menu.act_toggle.toolTip()
        finally:
            app.shutdown()

    def should_not_quit_when_last_window_hidden(self, pet_app):
        assert pet_app._app.quitOnLastWindowClosed() is False


class _SyncThread:
    """把 threading.Thread 换成同步执行，便于确定性地测后台线程逻辑。"""

    def __init__(self, target=None, name=None, daemon=None):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


class TestStartupGreeting:
    """每次打开宠物先推送一句昂扬问候：AI 在线出模型口号，未连/失败用宠物名兜底。"""

    def should_use_pet_name_fallback_when_ai_not_configured(self, pet_app):
        text = pet_app._greeting_text()

        assert pet_app._pet.name in text
        assert "开启美好的一天" in text

    def should_use_ai_slogan_when_configured(self, pet_app, monkeypatch):
        cfg = pet_app._config
        pet_app._config = replace(cfg, ai=replace(cfg.ai, api_key="sk-test"))
        monkeypatch.setattr(pet_app._ai_client, "complete",
                            lambda msgs, ai: "冲鸭，今天也要元气满满！")

        assert pet_app._greeting_text() == "冲鸭，今天也要元气满满！"

    def should_clamp_greeting_ai_timeout(self, pet_app, monkeypatch):
        """问候的 AI 等待必须钳到上限：网关慢/不通时不至于拖住开机体验。"""
        cfg = pet_app._config
        pet_app._config = replace(cfg, ai=replace(cfg.ai, api_key="sk-test", timeout_s=30.0))
        seen = {}

        def _capture(msgs, ai):
            seen["timeout_s"] = ai.timeout_s
            return "口号"

        monkeypatch.setattr(pet_app._ai_client, "complete", _capture)

        pet_app._generate_greeting()

        assert seen["timeout_s"] == main.GREETING_TIMEOUT_S

    def should_fallback_when_ai_raises(self, pet_app, monkeypatch):
        """模型报错也不能不问候：退回含宠物名的兜底语。"""
        cfg = pet_app._config
        pet_app._config = replace(cfg, ai=replace(cfg.ai, api_key="sk-test"))

        def _boom(*_a, **_k):
            raise AIGatewayError(AIGatewayError.NETWORK, "dns down")

        monkeypatch.setattr(pet_app._ai_client, "complete", _boom)
        text = pet_app._greeting_text()

        assert pet_app._pet.name in text
        assert "开启美好的一天" in text

    def should_publish_greeting_event_when_greet_on_start(self, pet_app, monkeypatch):
        captured = []
        pet_app._router.on(SpeakRequestedEvent, captured.append)
        monkeypatch.setattr(main.threading, "Thread", _SyncThread)

        pet_app._greet_on_start()

        assert captured
        assert captured[-1].title == "开启新的一天"
        assert pet_app._pet.name in captured[-1].text

    def should_trigger_greeting_on_start(self, pet_app, monkeypatch):
        """start() 会拉起问候（只验证调用，不依赖真线程/事件循环）。"""
        called = []
        monkeypatch.setattr(pet_app, "_greet_on_start", lambda: called.append(1))

        pet_app.start()

        assert called
