"""端到端验证：连续多轮对话中,system prompt 字节级稳定。

KV cache 静态前缀优化的核心目标:
    连续多轮 LLM 调用中,system 消息字节完全相同 → 命中 KV cache 前缀。

覆盖 spec §2 核心原则、§3.3 Dialogue 注入、§3.4 connection.py 改造的端到端集成。
"""

import pytest
from unittest.mock import MagicMock, patch

from core.connection import ConnectionHandler
from core.utils.dialogue import Dialogue, Message


def _make_dialogue_with_prompt(system_prompt: str):
    """构造一个含指定 system prompt 的 Dialogue。"""
    d = Dialogue()
    d.update_system_message(system_prompt)
    return d


class TestSystemPromptByteStable:
    """Dialogue 层验证: 多次调用 get_llm_dialogue_with_memory, system 字节不变。"""

    def test_system_prompt_unchanged_across_calls(self):
        """多次调用 get_llm_dialogue_with_memory, system 消息字节不变。"""
        static_prompt = "你是助手<identity>{{base_prompt}}</identity>"
        d = _make_dialogue_with_prompt(static_prompt)

        out1 = d.get_llm_dialogue_with_memory({"current_time": "15:30"})
        out2 = d.get_llm_dialogue_with_memory({"current_time": "15:31"})
        out3 = d.get_llm_dialogue_with_memory({"current_time": "15:32"})

        sys1 = next(m for m in out1 if m["role"] == "system")
        sys2 = next(m for m in out2 if m["role"] == "system")
        sys3 = next(m for m in out3 if m["role"] == "system")

        assert sys1["content"] == sys2["content"] == sys3["content"]

    def test_context_injection_does_not_modify_system(self):
        """context 注入只发生在 user 消息, system 消息字节不变。"""
        d = _make_dialogue_with_prompt("static system prompt")
        d.put(Message(role="user", content="用户原话"))

        out = d.get_llm_dialogue_with_memory({
            "current_time": "15:30",
            "memory": "用户喜欢音乐",
            "speakers_info": "当前说话人:张三",
        })

        sys_msg = next(m for m in out if m["role"] == "system")
        assert sys_msg["content"] == "static system prompt"
        # 确认 system 消息里没有 <context> 标签
        assert "<context>" not in sys_msg["content"]


def _build_mock_handler(sample_config):
    """构造一个最小可用、跳过真实连接初始化的 ConnectionHandler mock。"""
    handler = ConnectionHandler.__new__(ConnectionHandler)
    handler.config = sample_config
    handler.device_id = "test-device"
    handler.session_id = "test-session"
    handler.client_ip = "127.0.0.1"
    handler.memory = None
    handler.last_speaker_for_system = None
    handler.current_speaker = None  # C1 修复后 chat() 直读此属性, 跳过 __init__ 的 mock 必须显式设
    handler.dialogue = Dialogue()
    handler.dialogue.update_system_message("STATIC_SYSTEM_PROMPT_CONTENT")
    handler.intent_type = "function_call"
    handler.features = {"emoji": False}
    handler.logger = MagicMock()
    handler.tts = MagicMock()  # tts_text_queue / store_tts_text 自动是 MagicMock
    handler.sentence_id = "init-sentence-id"

    from core.utils.prompt_manager import PromptManager
    handler.prompt_manager = PromptManager(sample_config)

    handler.func_handler = MagicMock()
    handler.func_handler.get_functions = MagicMock(return_value=[])
    return handler


class TestKVCacheOptimizationEndToEnd:
    """模拟完整 chat() 流程, 验证连续消息下 system 字节稳定。"""

    def test_consecutive_messages_share_static_prefix(self, sample_config):
        """连续 5 条用户消息, system 消息字节完全相同 → KV cache 可复用。"""
        handler = _build_mock_handler(sample_config)

        # 捕获每次 LLM 调用时的 dialogue 快照
        captured_dialogues = []

        def capture(session_id, dialogue, functions=None):
            # 深拷贝对话快照(后续 chat() 会继续 mutate self.dialogue)
            captured_dialogues.append([dict(m) for m in dialogue])
            return iter([])

        handler.llm = MagicMock()
        handler.llm.response_with_functions = MagicMock(side_effect=capture)
        handler.llm.response = MagicMock(side_effect=capture)

        # 模拟 5 条用户消息
        # 注意: chat() 是同步方法, 不使用 async for
        for i in range(5):
            handler.dialogue.put(Message(role="user", content=f"消息 {i}"))
            try:
                handler.chat(f"消息 {i}")
            except Exception as e:
                pytest.fail(f"chat() 在第 {i} 轮抛异常: {e!r}")

        # 验证所有 system 消息内容相同
        system_contents = []
        for d in captured_dialogues:
            sys_msg = next((m for m in d if m["role"] == "system"), None)
            if sys_msg:
                system_contents.append(sys_msg["content"])

        assert len(system_contents) >= 2, (
            f"应至少有 2 个 LLM 调用,实际 {len(system_contents)}"
        )
        for content in system_contents:
            assert content == "STATIC_SYSTEM_PROMPT_CONTENT", (
                f"system prompt 不稳定: {content!r}"
            )

    def test_system_message_is_first_and_identical_across_calls(self, sample_config):
        """每次 LLM 调用, dialogue[0] 都是同一个 system 字符串(完全相同字节)。"""
        handler = _build_mock_handler(sample_config)

        captured_system_contents = []

        def capture(session_id, dialogue, functions=None):
            # dialogue[0] 必须是 system
            assert dialogue[0]["role"] == "system", (
                f"dialogue[0] 应该是 system,实际 {dialogue[0]['role']!r}"
            )
            captured_system_contents.append(dialogue[0]["content"])
            return iter([])

        handler.llm = MagicMock()
        handler.llm.response_with_functions = MagicMock(side_effect=capture)
        handler.llm.response = MagicMock(side_effect=capture)

        for i in range(5):
            try:
                handler.chat(f"消息 {i}")
            except Exception as e:
                pytest.fail(f"chat() 在第 {i} 轮抛异常: {e!r}")

        assert len(captured_system_contents) >= 2
        # 所有 system 内容字节完全相同 → KV cache 静态前缀命中
        first = captured_system_contents[0]
        for content in captured_system_contents:
            assert content == first, (
                "system prompt 字节发生变化 → KV cache 失效"
            )
        assert first == "STATIC_SYSTEM_PROMPT_CONTENT"

    def test_dynamic_context_changes_user_but_not_system(self, sample_config):
        """不同 dynamic_context 注入到 user 消息, system 消息保持稳定。"""
        handler = _build_mock_handler(sample_config)

        captured_dialogues = []

        def capture(session_id, dialogue, functions=None):
            captured_dialogues.append([dict(m) for m in dialogue])
            return iter([])

        handler.llm = MagicMock()
        handler.llm.response_with_functions = MagicMock(side_effect=capture)
        handler.llm.response = MagicMock(side_effect=capture)

        # 两次调用, 不同 dynamic_context
        ctx1 = {"current_time": "10:00", "memory": "用户喜欢古典音乐"}
        ctx2 = {"current_time": "14:30", "memory": "用户最近在学Python"}

        handler.dialogue.put(Message(role="user", content="帮我推荐首歌"))
        with patch.object(handler.prompt_manager, "collect_dynamic_context",
                          return_value=ctx1):
            try:
                handler.chat("帮我推荐首歌")
            except Exception as e:
                pytest.fail(f"chat() 第 1 次调用抛异常: {e!r}")

        handler.dialogue.put(Message(role="user", content="再推荐一首"))
        with patch.object(handler.prompt_manager, "collect_dynamic_context",
                          return_value=ctx2):
            try:
                handler.chat("再推荐一首")
            except Exception as e:
                pytest.fail(f"chat() 第 2 次调用抛异常: {e!r}")

        # 至少有 2 个 LLM 调用
        assert len(captured_dialogues) >= 2

        # 每次 system 消息完全相同
        system_msgs = []
        for d in captured_dialogues:
            sys_msg = next(m for m in d if m["role"] == "system")
            system_msgs.append(sys_msg["content"])
        assert all(s == "STATIC_SYSTEM_PROMPT_CONTENT" for s in system_msgs)

        # 第二次的 user 消息应包含第二次的 context("用户最近在学Python"),
        # 而第一次的 user 消息保持原样(因为 _find_last_user_index 只命中最后一条 user)
        first_user_msgs = [m for m in captured_dialogues[0] if m["role"] == "user"]
        second_user_msgs = [m for m in captured_dialogues[1] if m["role"] == "user"]

        # 第一次: 第一条 user 不含第二次的 memory 字符串
        for u in first_user_msgs:
            assert "用户最近在学Python" not in u["content"]
        # 第二次: 最后一条 user 包含 context("用户最近在学Python" 或 "14:30")
        last_user_in_second = second_user_msgs[-1]
        assert "用户最近在学Python" in last_user_in_second["content"] or \
               "14:30" in last_user_in_second["content"]
