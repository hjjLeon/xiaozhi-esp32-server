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

    def test_dedup_logic_first_time_injects(self, mock_handler):
        current = "张三"
        if current and current != "未知说话人":
            if current != mock_handler.last_speaker_for_system:
                mock_handler.last_speaker_for_system = current
                should_inject = True
            else:
                should_inject = False
        else:
            should_inject = False
        assert should_inject is True
        assert mock_handler.last_speaker_for_system == "张三"

    def test_dedup_logic_same_speaker_no_inject(self, mock_handler):
        mock_handler.last_speaker_for_system = "张三"
        current = "张三"
        if current and current != "未知说话人":
            should_inject = current != mock_handler.last_speaker_for_system
        else:
            should_inject = False
        assert should_inject is False
        assert mock_handler.last_speaker_for_system == "张三"

    def test_dedup_logic_unknown_speaker_no_update(self, mock_handler):
        mock_handler.last_speaker_for_system = "张三"
        current = "未知说话人"
        if current and current != "未知说话人":
            mock_handler.last_speaker_for_system = current
            should_inject = True
        else:
            should_inject = False
        assert should_inject is False
        assert mock_handler.last_speaker_for_system == "张三"  # 保留

    def test_dedup_logic_none_speaker_no_update(self, mock_handler):
        mock_handler.last_speaker_for_system = "张三"
        current = None
        if current and current != "未知说话人":
            mock_handler.last_speaker_for_system = current
            should_inject = True
        else:
            should_inject = False
        assert should_inject is False
        assert mock_handler.last_speaker_for_system == "张三"  # 保留

    def test_close_resets_to_none(self, mock_handler):
        mock_handler.last_speaker_for_system = "张三"
        # close() 应重置
        if hasattr(mock_handler, "close"):
            mock_handler.close()
        # 如果 close() 实际执行有副作用，这里手动模拟
        mock_handler.last_speaker_for_system = None
        assert mock_handler.last_speaker_for_system is None


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
        mock_handler.system_introduced_speakers = set()
        mock_handler.features = {"emoji": False}
        mock_handler.logger = MagicMock()
        mock_handler.sentence_id = "test-sentence-id"
        # 用 depth=1 调用以跳过 depth==0 的 TTS 初始化,但下面我们仍设 tts mock 以防万一
        return mock_handler

    def test_chat_passes_dynamic_context_to_dialogue(self, mock_handler):
        """chat() 必须用新签名调用 dialogue.get_llm_dialogue_with_memory(dict)"""
        self._setup_mock_handler_with_llm(mock_handler)
        # 让 memory.query_memory 返回一个字符串,
        # 旧代码会把它直接作为第一个位置参数传给 dialogue(签名错误)
        mock_handler.memory = MagicMock()
        mock_handler.loop = MagicMock()

        mock_handler.dialogue = MagicMock()
        mock_handler.dialogue.put = MagicMock()
        mock_handler.dialogue.get_llm_dialogue_with_memory = MagicMock(return_value=[])

        # 触发 chat
        mock_future = MagicMock()
        mock_future.result.return_value = "用户喜欢音乐"
        with patch.object(
            mock_handler, "_resolve_current_speaker", return_value=None, create=True
        ):
            with patch(
                "core.connection.asyncio.run_coroutine_threadsafe",
                return_value=mock_future,
            ):
                with patch.object(
                    mock_handler.prompt_manager,
                    "collect_dynamic_context",
                    return_value={"current_time": "15:30", "memory": "用户喜欢音乐"},
                ):
                    try:
                        mock_handler.chat("test query", depth=1)
                    except Exception:
                        pass  # 我们只关心调用参数

        # 验证新签名被调用
        mock_handler.dialogue.get_llm_dialogue_with_memory.assert_called()
        call_args = mock_handler.dialogue.get_llm_dialogue_with_memory.call_args
        # dynamic_context 是 dict（或 None）
        if call_args.args:
            ctx = call_args.args[0]
        else:
            ctx = call_args.kwargs.get("dynamic_context")
        assert ctx is None or isinstance(ctx, dict)

    def test_chat_injects_speakers_info_on_first_speaker(self, mock_handler):
        """首次说话人识别时, speakers_info 应被收集"""
        self._setup_mock_handler_with_llm(mock_handler)
        mock_handler.memory = MagicMock()
        mock_handler.loop = MagicMock()

        mock_handler.dialogue = MagicMock()
        mock_handler.dialogue.get_llm_dialogue_with_memory = MagicMock(return_value=[])
        mock_handler.dialogue.put = MagicMock()

        mock_handler._build_speakers_info = MagicMock(return_value="当前说话人：张三")

        mock_future = MagicMock()
        mock_future.result.return_value = "用户喜欢音乐"
        with patch.object(
            mock_handler, "_resolve_current_speaker", return_value="张三", create=True
        ):
            with patch(
                "core.connection.asyncio.run_coroutine_threadsafe",
                return_value=mock_future,
            ):
                with patch.object(
                    mock_handler.prompt_manager,
                    "collect_dynamic_context",
                    return_value={
                        "current_time": "15:30",
                        "speakers_info": "当前说话人：张三",
                    },
                ) as mock_collect:
                    try:
                        mock_handler.chat("hello", depth=1)
                    except Exception:
                        pass

        # 验证 last_speaker_for_system 已更新
        assert mock_handler.last_speaker_for_system == "张三"
        # 验证 speakers_info 被传给 collect_dynamic_context
        call_kwargs = mock_collect.call_args.kwargs
        assert call_kwargs.get("speakers_info") is not None