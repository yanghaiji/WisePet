"""端到端诊断：模型只回「好嘞」不调工具时，视频兜底必须真的生成并产出视频。

模拟真机场景：用户说「生成一个可爱的小猫跳舞的视频 10s左右的」，
模型偷懒只回「好嘞！」，兜底要补调 generate_video，最终 outcome 带视频路径。
"""
from __future__ import annotations

import pathlib

from tests.unit.fakes import FakeGateway
from tests.unit.test_agent_loop import _call, _chat, _runner, _toolbox, _ai_config
from WisePet.domain.ports import AIConfig as AC, PURPOSE_VIDEO
from WisePet.domain.events import ChatReplyChunkEvent, MediaTaskEvent


def test_model_says_ok_but_fallback_generates_video(tmp_path):
    class _Vid:
        def generate_video(self, prompt, cfg, progress=None):
            if progress:
                progress("生成中 40%")
                progress("生成中 100%")
            return b"MP4DATA", "video/mp4"

    events = []
    def sink(task_id, status, text, path):
        events.append((status, text, path))

    toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
    toolbox._video_gateway = _Vid()
    toolbox._video_dir = tmp_path / "generated"
    toolbox._video_cfg = lambda: AC(api_key="k", purpose=PURPOSE_VIDEO)
    toolbox._video_task = sink

    # 模型只回「好嘞」：没有工具调用
    gateway = FakeGateway(tools_results=[("好嘞！", [])])
    from WisePet.app.agent_loop import AgentRunner, wants_video
    runner = AgentRunner(gateway, lambda: _ai_config(), lambda: "sys", toolbox,
                         keyword_fallbacks=((wants_video, "generate_video"),))
    outcome = runner.run("生成一个可爱的小猫跳舞的视频 10s左右的", [])

    assert "generate_video" in outcome.tools_used, "兜底没触发 generate_video"
    assert len(outcome.videos) == 1, "outcome 没有视频路径"
    assert pathlib.Path(outcome.videos[0]).is_file(), "视频文件没落盘"
    assert ("submitted",) <= tuple(e[0] for e in events), "没发提交事件"
    assert any(e[0] == "done" and e[2] for e in events), "没发完成事件带路径"
    assert "10s" in outcome.final_text or "视频" in outcome.final_text


def test_fallback_failure_overwrites_model_ok(tmp_path):
    """视频没配/失败时，必须覆盖模型那句「好嘞」，否则用户以为成了干等。"""
    from WisePet.app.agent_loop import AgentRunner, wants_video
    toolbox, _folder, _cfg_tool = _toolbox(tmp_path)
    # 故意不注入 video_gateway/cfg → 工具返回「视频功能未启用」
    gateway = FakeGateway(tools_results=[("好嘞！", [])])
    runner = AgentRunner(gateway, lambda: _ai_config(), lambda: "sys", toolbox,
                         keyword_fallbacks=((wants_video, "generate_video"),))
    outcome = runner.run("生成一个视频", [])
    assert "好嘞" not in outcome.final_text, "模型的空话没被失败原因覆盖"
    assert "未启用" in outcome.final_text or "失败" in outcome.final_text
