"""pytest fixtures"""

import sys
import os
import logging
import pytest

# 确保项目根目录在 path 中
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "main", "xiaozhi-server")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# 预先填充 config cache,避免 setup_logging() 在没有真实网络的环境下
# 触发 load_config() -> /config/server-base POST 重试,导致测试 hang。
# 注意:setup_logging() 的逻辑是 cache 命中则直接返回,只有未命中才走 asyncio.run。
from core.utils.cache.manager import cache_manager, CacheType  # noqa: E402

cache_manager.set(
    CacheType.CONFIG,
    "main_config",
    {
        "log": {
            "log_dir": "tmp",
            "log_format": "",
            "log_format_file": "",
            "selected_module": "00000000000000",
        },
    },
)


# 桥接 loguru 到 Python 标准 logging,使 pytest caplog 可以捕获
# PromptManager 内部使用 loguru,默认不会传播到 stdlib logging。
# 关键问题:PromptManager.__init__ 调用 setup_logging(),而 setup_logging()
# 内部会执行 logger.remove() 清掉所有 sink。因此我们的 sink 必须在
# setup_logging() 之后/每次 PromptManager 初始化之后重新挂上。
# 解决方案:monkey-patch setup_logging,让它在配置完成后再执行 logger.add(sink)。
from loguru import logger as _loguru_logger  # noqa: E402
import config.logger as _config_logger_module  # noqa: E402


def _loguru_to_stdlib_sink(message) -> None:
    """loguru sink:把消息原样转发到 stdlib logging root logger(由 caplog 捕获)。"""
    record = message.record
    logging.getLogger().log(record["level"].no, record["message"])


_original_setup_logging = _config_logger_module.setup_logging


def _patched_setup_logging(config=None):
    """调用真正的 setup_logging 后,再把 stdlib 桥接 sink 挂回去。"""
    result = _original_setup_logging(config)
    _loguru_logger.add(
        _loguru_to_stdlib_sink,
        format="{message}",
        level="DEBUG",
        colorize=False,
        # filter 防止 sink 重复:仅未通过此 sink 的记录会触发
    )
    return result


_config_logger_module.setup_logging = _patched_setup_logging
# PromptManager 持有的是 from config.logger import setup_logging 的引用,
# 但 setup_logging 是模块级函数,PromptManager 内部调用时走模块查表,
# 所以我们替换模块上的 setup_logging 即可生效。


@pytest.fixture
def sample_config():
    """最小可用的 PromptManager 配置"""
    return {
        "prompt_template": "agent-base-prompt.txt",
        "VAD": {"selected_module": "silero", "SileroVAD": {}},
        "ASR": {"selected_module": "funasr", "FunASR": {}},
        "LLM": {"selected_module": "openai", "OpenAI": {}},
        "TTS": {"selected_module": "edge", "EdgeTTS": {"language": "zh-CN"}},
        "Memory": {"selected_module": "nomem"},
        "prompt_params": {},
    }
