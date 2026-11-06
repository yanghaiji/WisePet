"""跨平台 TTS 测试：命令构造（文本不拼进 shell）、平台装配、失败降级、截断。"""
from WisePet.infra.tts import (
    MAX_SPEAK_CHARS,
    MacSynthesizer,
    NullSynthesizer,
    WindowsSynthesizer,
    create_synthesizer,
    strip_emoji,
    truncate,
)


class Recorder:
    def __init__(self, raise_exc=False):
        self.calls = []
        self._raise = raise_exc

    def __call__(self, cmd, **kwargs):
        if self._raise:
            raise RuntimeError("engine down")
        self.calls.append((cmd, kwargs))


class TestFactory:
    def should_pick_null_when_disabled(self):
        assert isinstance(create_synthesizer("win32", enabled=False), NullSynthesizer)

    def should_pick_platform_impl(self):
        assert isinstance(create_synthesizer("win32"), WindowsSynthesizer)
        assert isinstance(create_synthesizer("darwin"), MacSynthesizer)
        assert isinstance(create_synthesizer("linux"), NullSynthesizer)


class TestNull:
    def should_do_nothing(self):
        assert NullSynthesizer().speak("你好") is None


class TestWindows:
    def should_run_powershell_with_text_via_stdin(self):
        rec = Recorder()
        WindowsSynthesizer(runner=rec).speak("你好世界", volume=80, rate=-2)

        cmd, kwargs = rec.calls[0]
        assert cmd[0] == "powershell"
        script = cmd[-1]
        # System.Speech 程序集非 PowerShell 默认加载，必须先 Add-Type，否则 New-Object 报 TypeNotFound
        assert "Add-Type -AssemblyName System.Speech" in script
        assert "System.Speech" in script
        assert "Volume=80" in script and "Rate=-2" in script
        # 文本走 stdin，绝不拼进命令（防注入）
        assert kwargs["input"] == "你好世界".encode("utf-8")
        assert "你好世界" not in script

    def should_skip_when_text_blank(self):
        rec = Recorder()
        WindowsSynthesizer(runner=rec).speak("   ")
        assert rec.calls == []

    def should_swallow_engine_failure(self):
        WindowsSynthesizer(runner=Recorder(raise_exc=True)).speak("喵")  # 不抛异常即通过


class TestMac:
    def should_run_say_with_argv_text(self):
        rec = Recorder()
        MacSynthesizer(runner=rec).speak("喵", volume=50, rate=3)

        cmd, _kwargs = rec.calls[0]
        assert cmd[0] == "say"
        assert cmd[1] == "-r"
        assert cmd[2] == str(175 + 3 * 8)     # rate 折算 wpm
        assert "--" in cmd
        assert cmd[-1] == "[[volm: 0.50]]喵"   # 音量内联指令 + 文本

    def should_skip_when_text_blank(self):
        rec = Recorder()
        MacSynthesizer(runner=rec).speak("")
        assert rec.calls == []


class TestTruncate:
    def should_cap_length(self):
        assert truncate("啊" * (MAX_SPEAK_CHARS + 50)) == "啊" * MAX_SPEAK_CHARS
        assert truncate(None) == ""


class TestStripEmoji:
    def should_remove_pictographs_and_symbols(self):
        assert strip_emoji("你好😀🎉世界") == "你好世界"

    def should_collapse_space_left_by_emoji(self):
        assert strip_emoji("喝水啦 💧 哦") == "喝水啦 哦"

    def should_keep_plain_text_and_cjk(self):
        assert strip_emoji("今天心情不错 v1.8 冲鸭") == "今天心情不错 v1.8 冲鸭"

    def should_return_empty_for_emoji_only(self):
        assert strip_emoji("😀🎉✨") == ""
        assert strip_emoji(None) == ""
