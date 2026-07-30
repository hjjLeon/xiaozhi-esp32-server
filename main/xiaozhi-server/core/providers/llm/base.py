import json
from abc import ABC, abstractmethod
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

# 尝试导入 tiktoken 做精确 token 计数
try:
    import tiktoken
    _HAVE_TIKTOKEN = True
except ImportError:
    _HAVE_TIKTOKEN = False


def estimate_input_tokens(messages: list, tools: list = None) -> int:
    """估算输入上下文的 token 数量（供日志使用）。

    优先使用 tiktoken（精确），不可用时回退到字符估算。
    对含中文的混合内容约 2-3 字符/token，取保守值 3。
    """
    text = json.dumps(messages, ensure_ascii=False)
    if tools:
        text += json.dumps(tools, ensure_ascii=False)
    if _HAVE_TIKTOKEN:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    return len(text) // 3


class LoggingLLMWrapper:
    """LLM Provider 透明日志包装器。

    在统一的入口点（create_instance）包装所有 Provider，自动记录每次
    发给 LLM 的上下文长度统计信息，无需在各 Provider 中重复添加。
    """

    def __init__(self, provider):
        self._provider = provider

    def response(self, session_id, dialogue, **kwargs):
        self._log_input(session_id, dialogue, None)
        yield from self._provider.response(session_id, dialogue, **kwargs)

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        self._log_input(session_id, dialogue, functions)
        yield from self._provider.response_with_functions(
            session_id, dialogue, functions, **kwargs
        )

    def response_no_stream(self, system_prompt, user_prompt, **kwargs):
        self._log_no_stream_input(system_prompt, user_prompt)
        return self._provider.response_no_stream(
            system_prompt, user_prompt, **kwargs
        )

    def _log_no_stream_input(self, system_prompt, user_prompt):
        """M0: 与 response/response_with_functions 对称, 记录 no_stream 路径的 token 估算。"""
        model_name = getattr(self._provider, "model_name", "unknown")
        messages = [
            {"role": "system", "content": system_prompt or ""},
            {"role": "user", "content": user_prompt or ""},
        ]
        est = estimate_input_tokens(messages)
        logger.bind(tag=TAG).info(
            f"请求 LLM（no_stream），模型：{model_name}，"
            f"估算 tokens：{est}"
        )

    def _log_input(self, session_id, dialogue, functions):
        model_name = getattr(self._provider, "model_name", "unknown")
        mode = "带工具" if functions else "无工具"
        tools_count = len(functions) if functions else 0
        msg_count = len(dialogue)
        est = estimate_input_tokens(dialogue, functions)
        logger.bind(tag=TAG).info(
            f"请求 LLM（{mode}），模型：{model_name}，"
            f"消息数：{msg_count}，"
            f"工具数：{tools_count}，"
            f"估算 tokens：{est}"
        )

    def __getattr__(self, name):
        """透传其他属性和方法到原始 Provider"""
        return getattr(self._provider, name)


class LLMProviderBase(ABC):
    @abstractmethod
    def response(self, session_id, dialogue):
        """LLM response generator"""
        pass

    def response_no_stream(self, system_prompt, user_prompt, **kwargs):
        # 构造对话格式
        dialogue = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        result = ""
        for part in self.response("", dialogue, **kwargs):
            result += part
        return result

    def response_with_functions(self, session_id, dialogue, functions=None):
        """
        Default implementation for function calling (streaming)
        This should be overridden by providers that support function calls

        Returns: generator that yields either text tokens or a special function call token
        """
        # For providers that don't support functions, just return regular response
        for token in self.response(session_id, dialogue):
            yield token, None

