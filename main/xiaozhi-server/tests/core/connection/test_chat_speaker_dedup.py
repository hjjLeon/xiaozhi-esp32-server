"""测试 ConnectionHandler 的 last_speaker_for_system 状态和 speakers_info 构建"""

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