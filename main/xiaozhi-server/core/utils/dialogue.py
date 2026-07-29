from typing import List, Dict, Optional
import uuid


# 字段顺序固定常量（spec §3.5）
CONTEXT_FIELD_ORDER = [
    "current_time",
    "today_date",
    "today_weekday",
    "lunar_date",
    "local_address",
    "weather_info",
    "memory",
    "speakers_info",
]

CONTEXT_FIELD_LABELS = {
    "current_time": "当前时间",
    "today_date": "今天日期",
    "today_weekday": "星期",
    "lunar_date": "农历",
    "local_address": "设备位置",
    "weather_info": "本地天气",
    "memory": "记忆",
    "speakers_info": "说话人信息",
}


class Message:
    def __init__(
        self,
        role: str,
        content: str = None,
        uniq_id: str = None,
        tool_calls=None,
        tool_call_id=None,
        is_temporary=False,
    ):
        self.uniq_id = uniq_id if uniq_id is not None else str(uuid.uuid4())
        self.role = role
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_id = tool_call_id
        self.is_temporary = is_temporary  # 标记临时消息（如工具调用提醒）


class Dialogue:
    def __init__(self):
        self.dialogue: List[Message] = []

    def put(self, message: Message):
        self.dialogue.append(message)

    def getMessages(self, m, dialogue):
        if m.tool_calls is not None:
            dialogue.append({"role": m.role, "tool_calls": m.tool_calls})
        elif m.role == "tool":
            dialogue.append(
                {
                    "role": m.role,
                    "tool_call_id": (
                        str(uuid.uuid4()) if m.tool_call_id is None else m.tool_call_id
                    ),
                    "content": m.content,
                }
            )
        else:
            dialogue.append({"role": m.role, "content": m.content})

    def get_llm_dialogue(self) -> List[Dict[str, str]]:
        """向后兼容 wrapper:不传 dynamic_context。"""
        return self.get_llm_dialogue_with_memory(None)

    def update_system_message(self, new_content: str):
        """更新或添加系统消息"""
        # 查找第一个系统消息
        system_msg = next((msg for msg in self.dialogue if msg.role == "system"), None)
        if system_msg:
            system_msg.content = new_content
        else:
            self.put(Message(role="system", content=new_content))

    def _ensure_tool_calls_complete(self, messages: List[Message]) -> List[Message]:
        """
        确保所有 tool_calls 都有对应的 tool 响应
        修复被打断导致的悬空 tool_calls，防止大模型 API 报 400 错误
        """
        pending_tool_calls = set()
        result = []

        for msg in messages:
            result.append(msg)

            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if tc_id:
                        pending_tool_calls.add(tc_id)

            elif msg.role == "tool" and msg.tool_call_id:
                pending_tool_calls.discard(msg.tool_call_id)

        for missing_id in pending_tool_calls:
            dummy_tool_msg = Message(
                role="tool",
                content='{"status": "interrupted", "message": "动作已取消/被打断"}',
                tool_call_id=missing_id
            )
            result.append(dummy_tool_msg)

        return result

    def _build_context_block(
        self, dynamic_context: Optional[Dict[str, str]]
    ) -> Optional[str]:
        """按固定顺序组装 <context>...</context> 块;全空返回 None。

        Args:
            dynamic_context: 各字段值,None / 空字符串 / 仅空白跳过该字段。

        Returns:
            形如 "<context>\\n- K: V\\n...</context>" 的字符串,或 None。
        """
        if not dynamic_context:
            return None
        try:
            lines = []
            for key in CONTEXT_FIELD_ORDER:
                value = dynamic_context.get(key)
                if value is None:
                    continue
                if not isinstance(value, str):
                    continue
                stripped = value.strip()
                if not stripped:
                    continue
                label = CONTEXT_FIELD_LABELS.get(key, key)
                lines.append(f"- {label}: {stripped}")
            if not lines:
                return None
            return "<context>\n" + "\n".join(lines) + "\n</context>"
        except Exception:
            return None

    def _find_last_user_index(
        self, dialogue: List[Dict[str, str]]
    ) -> Optional[int]:
        """找到最后一条 role=='user' 消息的索引;找不到返回 None。

        仅当 dialogue 以 user 消息结尾时返回其索引;以 assistant/tool 结尾时返回 None,
        避免把已经处理过/正在处理的历史 user 消息误当作"当前 user 输入"。
        """
        try:
            if not dialogue:
                return None
            if dialogue[-1].get("role") == "user":
                return len(dialogue) - 1
            return None
        except Exception:
            return None

    def get_llm_dialogue_with_memory(
        self,
        dynamic_context: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, str]]:
        """构建 LLM 请求用的 dialogue 列表。

        Args:
            dynamic_context: 动态上下文 dict。None / 空 dict → 不注入。
        """
        dialogue = []

        # 1. system 消息原样取(零修改)
        system_message = next(
            (msg for msg in self.dialogue if msg.role == "system"), None
        )
        if system_message:
            dialogue.append({"role": "system", "content": system_message.content})

        # 2. few-shot 序列
        non_system = [m for m in self.dialogue if m.role != "system"]
        fewshot = [m for m in non_system if m.is_temporary]
        complete_fewshot = self._ensure_tool_calls_complete(fewshot)
        for m in complete_fewshot:
            self.getMessages(m, dialogue)

        # 3. 实际历史
        actual = [m for m in non_system if not m.is_temporary]
        complete_actual = self._ensure_tool_calls_complete(actual)
        for m in complete_actual:
            self.getMessages(m, dialogue)

        # 4. 前置注入 dynamic_context
        ctx_block = self._build_context_block(dynamic_context)
        if ctx_block:
            last_user_idx = self._find_last_user_index(dialogue)
            if last_user_idx is not None:
                orig = dialogue[last_user_idx].get("content") or ""
                dialogue[last_user_idx]["content"] = f"{ctx_block}\n\n{orig}"

        return dialogue