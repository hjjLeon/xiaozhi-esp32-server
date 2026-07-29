"""测试 Dialogue 层动态上下文注入"""

import pytest
from core.utils.dialogue import Dialogue, Message


def make_dialogue_with(*messages):
    """构造一个 Dialogue,内部含给定的 (role, content) 元组列表"""
    d = Dialogue()
    for role, content in messages:
        d.put(Message(role=role, content=content))
    return d


class TestFindLastUserIndex:
    def test_finds_last_user_in_assistant_user_sequence(self):
        """对话以 assistant + user 结尾,应返回 user 的索引"""
        d = make_dialogue_with(
            ("system", "sys"),
            ("user", "u1"),
            ("assistant", "a1"),
            ("user", "u2"),
        )
        serialized = [{"role": m.role, "content": m.content}
                      for m in d.dialogue if m.role == "system"] + \
                     [{"role": m.role, "content": m.content}
                      for m in d.dialogue if m.role != "system"]
        idx = d._find_last_user_index(serialized)
        assert idx == 3
        assert serialized[idx]["content"] == "u2"

    def test_returns_none_when_no_user(self):
        """对话只有 system + assistant,无 user 时返回 None"""
        d = make_dialogue_with(
            ("system", "sys"),
            ("assistant", "a1"),
        )
        serialized = [
            {"role": m.role, "content": m.content} for m in d.dialogue
        ]
        assert d._find_last_user_index(serialized) is None

    def test_returns_none_when_last_is_tool_or_assistant(self):
        """最后一条是 assistant(含 tool_calls)时返回 None"""
        d = make_dialogue_with(
            ("system", "sys"),
            ("user", "u1"),
            ("assistant", "a1"),
        )
        # 模拟 assistant tool_calls 状态：最后一条是 assistant 但 role==assistant
        serialized = [
            {"role": m.role, "content": m.content} for m in d.dialogue
        ]
        serialized[-1] = {"role": "assistant", "tool_calls": [{"id": "t1"}]}
        assert d._find_last_user_index(serialized) is None


class TestBuildContextBlock:
    def test_empty_dict_returns_none(self):
        d = Dialogue()
        assert d._build_context_block({}) is None

    def test_none_dict_returns_none(self):
        d = Dialogue()
        assert d._build_context_block(None) is None

    def test_skips_none_values(self):
        d = Dialogue()
        block = d._build_context_block({
            "current_time": "15:30",
            "today_date": None,
            "weather_info": "",
            "memory": "用户喜欢音乐",
        })
        assert "<context>" in block
        assert "- 当前时间: 15:30" in block
        assert "- 记忆: 用户喜欢音乐" in block
        assert "today_date" not in block
        assert "weather_info" not in block
        assert "weather_info: " not in block

    def test_skips_whitespace_only_values(self):
        d = Dialogue()
        block = d._build_context_block({"current_time": "   "})
        assert block is None

    def test_field_order_is_fixed(self):
        """字段按固定顺序: time → date → weekday → lunar → address → weather → memory → speakers"""
        d = Dialogue()
        block = d._build_context_block({
            "speakers_info": "speakers",
            "memory": "mem",
            "current_time": "t",
            "weather_info": "w",
        })
        # speakers 在 memory 后, memory 在 weather 后, weather 在 time 后
        t_pos = block.index("当前时间")
        w_pos = block.index("本地天气") if "本地天气" in block else block.index("weather")
        m_pos = block.index("记忆")
        s_pos = block.index("当前说话人") if "当前说话人" in block else block.index("speakers")
        assert t_pos < w_pos < m_pos < s_pos

    def test_returns_wrapped_block(self):
        d = Dialogue()
        block = d._build_context_block({"current_time": "15:30"})
        assert block.startswith("<context>")
        assert block.rstrip().endswith("</context>")


class TestGetLLMDialogueWithMemoryNewSignature:
    def test_no_dynamic_context_keeps_user_content_intact(self):
        """dynamic_context=None 时 user 消息原样保留(向后兼容基线)"""
        d = make_dialogue_with(
            ("system", "sys content"),
            ("user", "用户原话"),
        )
        out = d.get_llm_dialogue_with_memory(None)
        user_msgs = [m for m in out if m["role"] == "user"]
        assert user_msgs[0]["content"] == "用户原话"

    def test_injects_context_into_last_user_message(self):
        d = make_dialogue_with(
            ("system", "sys"),
            ("user", "u1"),
            ("assistant", "a1"),
            ("user", "今天吃什么"),
        )
        out = d.get_llm_dialogue_with_memory({"current_time": "15:30"})
        last_user = [m for m in out if m["role"] == "user"][-1]
        assert last_user["content"].startswith("<context>")
        assert "今天吃什么" in last_user["content"]
        # context 在用户原话前
        assert last_user["content"].index("<context>") < last_user["content"].index("今天吃什么")

    def test_empty_dynamic_context_no_injection(self):
        d = make_dialogue_with(("system", "sys"), ("user", "u1"))
        out = d.get_llm_dialogue_with_memory({})
        user_msg = [m for m in out if m["role"] == "user"][0]
        assert user_msg["content"] == "u1"

    def test_skip_injection_when_last_is_assistant(self):
        """最后一条非 user 时跳过注入,user 消息保持原样"""
        d = make_dialogue_with(
            ("system", "sys"),
            ("user", "u1"),
            ("assistant", "a1"),
        )
        out = d.get_llm_dialogue_with_memory({"current_time": "15:30"})
        # 没有任何 user 消息被注入 context
        for m in out:
            if m["role"] == "user":
                assert "<context>" not in m["content"]

    def test_system_message_content_not_modified(self):
        """system 消息绝对不被修改"""
        d = make_dialogue_with(("system", "sys with {{current_time}} literal"), ("user", "u1"))
        out = d.get_llm_dialogue_with_memory({"current_time": "15:30"})
        sys_msg = [m for m in out if m["role"] == "system"][0]
        assert sys_msg["content"] == "sys with {{current_time}} literal"