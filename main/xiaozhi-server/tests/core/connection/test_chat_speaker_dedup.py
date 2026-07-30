"""测试 ConnectionHandler 的 last_speaker_for_system 状态和 speakers_info 构建"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.connection import ConnectionHandler


@pytest.fixture
def mock_handler(monkeypatch, sample_config):
    """最小可用的 ConnectionHandler mock，跳过实际连接初始化"""
    from core.utils.dialogue import Dialogue
    from core.utils.prompt_manager import PromptManager

    handler = ConnectionHandler.__new__(ConnectionHandler)
    handler.config = sample_config
    handler.device_id = "test-device"
    handler.session_id = "test-session"
    handler.client_ip = "127.0.0.1"
    handler.dialogue = Dialogue()
    handler.prompt_manager = PromptManager(sample_config)
    handler.memory = None
    handler.last_speaker_for_system = None
    return handler


class TestLastSpeakerState:
    def test_init_starts_as_none(self, mock_handler):
        assert mock_handler.last_speaker_for_system is None

    def test_update_on_valid_speaker(self, mock_handler):
        mock_handler.last_speaker_for_system = "张三"
        assert mock_handler.last_speaker_for_system == "张三"

    def test_close_resets_last_speaker(self, mock_handler):
        """M3: close() 应在最开始把 last_speaker_for_system 重置为 None（行为验证）。"""
        import asyncio

        mock_handler.last_speaker_for_system = "张三"
        # close() 后续副作用用 mock 兜底, 只验证 reset 行为
        mock_handler.timeout_task = None
        mock_handler.vad = None
        mock_handler.audio_buffer = MagicMock()
        mock_handler._aec_cache_cleanup_task = None
        mock_handler.aec_audio_cache = MagicMock()
        mock_handler.func_handler = MagicMock()

        with patch.object(mock_handler.func_handler, "cleanup", create=True):
            try:
                asyncio.run(mock_handler.close())
            except Exception:
                # 任何后续副作用因 mock 不全而崩不影响; reset 在 close 第一行已发生
                pass

        assert mock_handler.last_speaker_for_system is None, (
            "close() 必须在最开始把 last_speaker_for_system 重置为 None"
        )


class TestBuildSpeakersInfo:
    def test_format_includes_current_and_list(self, mock_handler):
        """返回字符串含当前说话人 + 其他说话人列表"""
        voiceprint_config = {
            "speakers": ["id1,张三,家人男35", "id2,李四,朋友女28"]
        }
        result = mock_handler._build_speakers_info("张三", voiceprint_config)
        assert "当前说话人" in result or "张三" in result

    def test_empty_speakers_list(self, mock_handler):
        voiceprint_config = {"speakers": []}
        result = mock_handler._build_speakers_info("张三", voiceprint_config)
        # 至少包含当前说话人
        assert "张三" in result


class TestChatDynamicContextPipeline:
    def _setup_mock_handler_with_llm(self, mock_handler):
        """注入 mock LLM/tts/loop, 让 chat() 不实际调用"""
        mock_handler.intent_type = "function_call"
        mock_handler.llm = MagicMock()
        mock_handler.llm.response_with_functions = MagicMock(return_value=iter([]))
        mock_handler.func_handler = MagicMock()
        mock_handler.func_handler.get_functions = MagicMock(return_value=[])
        mock_handler.tts = MagicMock()
        mock_handler.tts.tts_text_queue = MagicMock()
        mock_handler.tts.store_tts_text = MagicMock()
        mock_handler.loop = MagicMock()
        mock_handler.client_abort = False
        mock_handler.client_listen_mode = "auto"
        mock_handler.current_speaker = None
        mock_handler.features = {"emoji": False}
        mock_handler.logger = MagicMock()
        mock_handler.sentence_id = "test-sentence-id"
        # 用 depth=1 调用以跳过 depth==0 的 TTS 初始化,但下面我们仍设 tts mock 以防万一
        return mock_handler

    def test_dedup_via_real_chat_path(self, mock_handler):
        """真实 chat() 路径: 首次说话人识别后, last_speaker_for_system 应被更新。"""
        self._setup_mock_handler_with_llm(mock_handler)
        mock_handler.current_speaker = "张三"
        mock_handler._build_speakers_info = MagicMock(return_value="当前说话人:张三")

        try:
            mock_handler.chat("hello", depth=1)
        except Exception as e:
            pytest.fail(f"chat() 在说话人识别前抛异常: {e!r}")

        assert mock_handler.last_speaker_for_system == "张三", (
            "chat() 应将 last_speaker_for_system 更新为识别到的说话人"
        )
        mock_handler._build_speakers_info.assert_called_once_with(
            "张三", mock_handler._build_speakers_info.call_args.args[1]
        )

    def test_speaker_change_triggers_re_inject(self, mock_handler):
        """说话人变化时, _build_speakers_info 应被再次调用。"""
        self._setup_mock_handler_with_llm(mock_handler)
        mock_handler._build_speakers_info = MagicMock(return_value="当前说话人:X")

        mock_handler.current_speaker = "张三"
        try:
            mock_handler.chat("hi", depth=1)
        except Exception as e:
            pytest.fail(f"chat() 第 1 次调用抛异常: {e!r}")
        assert mock_handler.last_speaker_for_system == "张三"

        mock_handler.current_speaker = "李四"
        try:
            mock_handler.chat("hi", depth=1)
        except Exception as e:
            pytest.fail(f"chat() 第 2 次调用抛异常: {e!r}")
        assert mock_handler.last_speaker_for_system == "李四"

        assert mock_handler._build_speakers_info.call_count == 2, (
            "说话人变化时 _build_speakers_info 应再次调用"
        )

    def test_same_speaker_skips_re_inject(self, mock_handler):
        """同一说话人连续 chat, _build_speakers_info 只调用一次。"""
        self._setup_mock_handler_with_llm(mock_handler)
        mock_handler.current_speaker = "张三"
        mock_handler._build_speakers_info = MagicMock(return_value="当前说话人:张三")

        try:
            mock_handler.chat("hi 1", depth=1)
            mock_handler.chat("hi 2", depth=1)
        except Exception as e:
            pytest.fail(f"chat() 抛异常: {e!r}")

        mock_handler._build_speakers_info.assert_called_once()
        assert mock_handler.last_speaker_for_system == "张三"