"""
系统提示词管理器模块
负责管理和更新系统提示词，包括快速初始化和异步增强功能
"""

import os
import asyncio
import threading
from typing import Dict, Any, TYPE_CHECKING, Optional

# tiktoken 是可选依赖:不可用时回退到字符估算
try:
    import tiktoken
    _HAVE_TIKTOKEN = True
except ImportError:
    _HAVE_TIKTOKEN = False

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from config.logger import setup_logging
from jinja2 import Template

# 在模块顶层导入时间工具,使测试可通过 monkeypatch 替换
# (例如 pm_mod.get_current_lunar_date = boom)
from core.utils.current_time import (  # noqa: E402
    get_current_time,
    get_current_date,
    get_current_weekday,
    get_current_lunar_date,
)

TAG = __name__

# token 预算常量(spec §3.6:动态上下文 ≤ 600 tokens)
CONTEXT_TOKEN_BUDGET = 600
# 截断优先级(从低到高,先截 weather,最后截 memory)
CONTEXT_TRUNCATABLE_FIELDS = ["weather_info", "lunar_date", "local_address", "memory"]
# 永不截断的字段(供文档/未来扩展;截断循环天然不命中,仅遍历 CONTEXT_TRUNCATABLE_FIELDS)
CONTEXT_NEVER_TRUNCATE = {"speakers_info", "current_time", "today_date", "today_weekday"}
# 截断循环最大迭代次数(防御性, 防死循环)
_MAX_BUDGET_ITERATIONS = 20
# tiktoken 编码器(惰性初始化, L4: 多连接并发下用锁保护)
_TOKEN_ENCODING = None
_TOKEN_ENCODING_LOCK = threading.Lock()


def _get_token_encoding():
    """惰性获取 tiktoken 编码,避免冷启动开销。tiktoken 不可用时返回 None。"""
    global _TOKEN_ENCODING
    if _TOKEN_ENCODING is None and _HAVE_TIKTOKEN:
        with _TOKEN_ENCODING_LOCK:
            if _TOKEN_ENCODING is None and _HAVE_TIKTOKEN:  # double-check
                _TOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
    return _TOKEN_ENCODING

WEEKDAY_MAP = {
    "Monday": "星期一",
    "Tuesday": "星期二",
    "Wednesday": "星期三",
    "Thursday": "星期四",
    "Friday": "星期五",
    "Saturday": "星期六",
    "Sunday": "星期日",
}

EMOJI_List = [
    "😶",
    "🙂",
    "😆",
    "😂",
    "😔",
    "😠",
    "😭",
    "😍",
    "😳",
    "😲",
    "😱",
    "🤔",
    "😉",
    "😎",
    "😌",
    "🤤",
    "😘",
    "😏",
    "😴",
    "😜",
    "🙄",
]


class PromptManager:
    """系统提示词管理器，负责管理和更新系统提示词"""

    def __init__(self, config: Dict[str, Any], logger=None, conn_ref=None):
        self.config = config
        self.logger = logger or setup_logging()
        self.base_prompt_template = None
        self.last_update_time = 0
        # 持有 ConnectionHandler 引用, 用于 collect_dynamic_context 动态读取 client_ip
        self._conn_ref = conn_ref

        # 导入全局缓存管理器
        from core.utils.cache.manager import cache_manager, CacheType

        self.cache_manager = cache_manager
        self.CacheType = CacheType

        # 初始化上下文源
        from core.utils.context_provider import ContextDataProvider

        self.context_provider = ContextDataProvider(config, self.logger)
        self.context_data = {}

        self._load_base_template()

    def _load_base_template(self):
        """加载基础提示词模板,返回模板字符串(未找到时返回 None)。

        Returns:
            模板字符串;未找到时返回 None。调用方目前仅消费副作用
            (self.base_prompt_template),返回值供未来扩展可选使用。

        同时复用 KV cache 静态前缀优化的残留检测:如果模板仍含已废弃的
        动态占位符(如 {{current_time}}),打印 WARN 提示(不阻断加载)。
        """
        try:
            template_path = self.config.get("prompt_template", None)
            if not template_path:
                template_path = "agent-base-prompt.txt"
            cache_key = f"prompt_template:{template_path}"

            # 先从缓存获取
            cached_template = self.cache_manager.get(self.CacheType.CONFIG, cache_key)
            if cached_template is not None:
                self.base_prompt_template = cached_template
                self.logger.bind(tag=TAG).debug("从缓存加载基础提示词模板")
                self._validate_template_residue(cached_template)
                return cached_template

            # 缓存未命中，从文件读取
            if os.path.exists(template_path):
                with open(template_path, "r", encoding="utf-8") as f:
                    template_content = f.read()

                # 残留检测:扫描已废弃的占位符(仅 WARN,不阻断)
                self._validate_template_residue(template_content)

                # 存入缓存（CONFIG类型默认不自动过期，需要手动失效）
                self.cache_manager.set(
                    self.CacheType.CONFIG, cache_key, template_content
                )
                self.base_prompt_template = template_content
                self.logger.bind(tag=TAG).debug("成功加载基础提示词模板并缓存")
                return template_content
            else:
                self.logger.bind(tag=TAG).warning(f"未找到{template_path}文件")
                return None
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"加载提示词模板失败: {e}")
            return None

    def _validate_template_residue(self, template: str) -> None:
        """检测模板是否仍含已废弃的动态占位符（如 {{current_time}}）。

        仅 WARN 级别日志,不阻断加载（向后兼容旧模板），用于引导用户
        升级到 KV cache 静态前缀优化的新模板。
        """
        if not template:
            return
        # 已知已废弃的占位符
        deprecated = (
            "{{current_time}}",
            "{{today_date}}",
            "{{today_weekday}}",
            "{{lunar_date}}",
            "{{local_address}}",
            "{{weather_info}}",
            "{{memory}}",
            "{{ dynamic_context }}",
            # H4 预防性: 这些变量如出现在模板会破坏 KV cache 前缀稳定
            "{{device_id}}",
            "{{client_ip}}",
            "device_id",
            "client_ip",
        )
        found = [p for p in deprecated if p in template]
        if found:
            self.logger.bind(tag=TAG).warning(
                f"模板残留检测:仍含已废弃占位符 {found},"
                "KV cache 静态前缀优化可能未生效,"
                "建议更新模板移除这些占位符。"
            )

    def get_quick_prompt(self, user_prompt: str, device_id: str = None) -> str:
        """快速获取系统提示词（使用用户配置）"""
        device_cache_key = f"device_prompt:{device_id}"
        cached_device_prompt = self.cache_manager.get(
            self.CacheType.DEVICE_PROMPT, device_cache_key
        )
        if cached_device_prompt is not None:
            self.logger.bind(tag=TAG).debug(f"使用设备 {device_id} 的缓存提示词")
            return cached_device_prompt
        else:
            self.logger.bind(tag=TAG).debug(
                f"设备 {device_id} 无缓存提示词，使用传入的提示词"
            )

        # 使用传入的提示词并缓存（如果有设备ID）
        if device_id:
            device_cache_key = f"device_prompt:{device_id}"
            self.cache_manager.set(self.CacheType.DEVICE_PROMPT, device_cache_key, user_prompt)
            self.logger.bind(tag=TAG).debug(f"设备 {device_id} 的提示词已缓存")

        self.logger.bind(tag=TAG).info(f"使用快速提示词: {user_prompt[:50]}...")
        return user_prompt

    def _get_location_info(self, client_ip: str) -> str:
        """获取位置信息"""
        try:
            # 先从缓存获取
            cached_location = self.cache_manager.get(self.CacheType.LOCATION, client_ip)
            if cached_location is not None:
                return cached_location

            # 缓存未命中，调用API获取
            from core.utils.util import get_ip_info

            ip_info = get_ip_info(client_ip, self.logger)
            city = ip_info.get("city", "未知位置")
            location = f"{city}"

            # 存入缓存
            self.cache_manager.set(self.CacheType.LOCATION, client_ip, location)
            return location
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"获取位置信息失败: {e}")
            return "未知位置"

    def _get_weather_info(self, conn: "ConnectionHandler", location: str) -> str:
        """获取天气信息"""
        try:
            # 先从缓存获取
            cached_weather = self.cache_manager.get(self.CacheType.WEATHER, location)
            if cached_weather is not None:
                return cached_weather

            # 缓存未命中，调用 async get_weather 函数
            # Windows ProactorEventLoop 不支持 run_coroutine_threadsafe().result()
            # 因此用 call_soon_threadsafe 提交任务 + threading.Event 等待结果
            # 注意：Event.wait() 只阻塞当前线程池线程，不阻塞主事件循环
            from plugins_func.functions.get_weather import get_weather
            from plugins_func.register import ActionResponse

            result_holder = []
            exception_holder = []

            async def _call():
                try:
                    result_holder.append(
                        await get_weather(conn, location=location, lang="zh_CN")
                    )
                except Exception as e:
                    exception_holder.append(e)
                finally:
                    event.set()

            event = threading.Event()
            conn.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_call()))
            if not event.wait(timeout=10):
                raise TimeoutError("获取天气信息超时")
            if exception_holder:
                raise exception_holder[0]
            result = result_holder[0]
            if isinstance(result, ActionResponse):
                weather_report = result.result
                self.cache_manager.set(self.CacheType.WEATHER, location, weather_report)
                return weather_report
            return "天气信息获取失败"

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"获取天气信息失败: {e}")
            return "天气信息获取失败"

    def update_context_info(self, conn, client_ip: str):  # noqa: D401
        """向后兼容占位（已废弃）。KV cache 优化后该方法无副作用,调用点已全部移除。"""
        # 完整方法体删除 — 原实现仅含 debug 日志, 与 collect_dynamic_context 重复
        return

    def collect_dynamic_context(
        self,
        *,
        device_id: str,
        memory_str: Optional[str] = None,
        speakers_info: Optional[str] = None,
    ) -> Dict[str, str]:
        """一次性收集所有动态信息,返回 Dict[str, str] 用于组装 <context> 块。

        字段约定(KV cache 优化 §3.6):
            current_time  - HH:MM 格式
            today_date    - YYYY-MM-DD
            today_weekday - 星期一/二/...
            lunar_date    - 中文农历
            local_address - 城市名(若有 client_ip)
            weather_info  - 天气报告(若有 client_ip + conn)
            memory        - 长记忆摘要(由调用方提供)
            speakers_info - 当前说话人列表(由调用方提供)

        优雅降级:
            - 任一子步骤抛异常被 try/except 捕获,对应字段省略,不影响其他字段
            - memory_str / speakers_info 为 None 或空字符串时,字段被省略
            - token 总数超 600 时,按 CONTEXT_TRUNCATABLE_FIELDS 优先级截断
        """
        ctx: Dict[str, str] = {}

        # 1. 时间类字段(current_time/today_date/today_weekday/lunar_date)
        try:
            ctx["current_time"] = get_current_time()
            ctx["today_date"] = get_current_date()
            ctx["today_weekday"] = get_current_weekday()
            ctx["lunar_date"] = get_current_lunar_date()
        except Exception as e:
            self.logger.bind(tag=TAG).warning(
                f"collect_dynamic_context 时间字段收集失败: {e}"
            )

        # 2. location / weather(按 client_ip / conn 缓存)
        # 若调用方未传入 client_ip/conn,这两个字段自然省略
        # C2 修复后来源优先级: self._conn_ref.client_ip (生产) > self.client_ip (老 API 兼容)
        try:
            conn = getattr(self, "_conn_ref", None)
            client_ip = None
            if conn is not None:
                client_ip = getattr(conn, "client_ip", None)
            if client_ip is None:
                client_ip = getattr(self, "client_ip", None)
            if client_ip:
                ctx["local_address"] = self._get_location_info(client_ip)
                if conn is not None:
                    ctx["weather_info"] = self._get_weather_info(
                        conn, ctx.get("local_address", "")
                    )
        except Exception as e:
            self.logger.bind(tag=TAG).warning(
                f"collect_dynamic_context 位置/天气收集失败: {e}"
            )

        # 3. memory(由 Dialogue 层从 memory_str 传入)
        if memory_str is not None and memory_str.strip():
            ctx["memory"] = memory_str.strip()

        # 4. speakers_info(由 Dialogue 层从当前说话人列表传入)
        if speakers_info is not None and speakers_info.strip():
            ctx["speakers_info"] = speakers_info.strip()

        # 5. token 预算裁剪(600 tokens 上限)
        ctx = self._enforce_token_budget(ctx)

        return ctx

    def _enforce_token_budget(self, ctx: Dict[str, str]) -> Dict[str, str]:
        """按 CONTEXT_TOKEN_BUDGET (600) 裁剪可截断字段。

        截断顺序:weather_info → lunar_date → local_address → memory
        永不截断字段:speakers_info / current_time / today_date / today_weekday

        每次截断将该字段长度压缩到 75%(最少保留 10 字符 + "..."),
        直到总 tokens ≤ 600 或该字段已无法再截。
        """
        try:
            enc = _get_token_encoding()

            def total_tokens(d: Dict[str, str]) -> int:
                if enc is None:
                    # tiktoken 不可用:按字符估算 (混合中文取保守值 3 字符/token)
                    return sum(len(str(v)) for v in d.values()) // 3
                return sum(len(enc.encode(str(v))) for v in d.values())

            if total_tokens(ctx) <= CONTEXT_TOKEN_BUDGET:
                return ctx

            for key in CONTEXT_TRUNCATABLE_FIELDS:
                if key not in ctx:
                    continue
                value = ctx[key]
                for _ in range(_MAX_BUDGET_ITERATIONS):
                    if total_tokens(ctx) <= CONTEXT_TOKEN_BUDGET or len(value) <= 10:
                        break
                    new_len = max(10, int(len(value) * 0.75))
                    candidate = value[:new_len] + "..."
                    if len(candidate) >= len(value):  # 单调性失败: 截断后反而更长
                        self.logger.bind(tag=TAG).warning(
                            f"_enforce_token_budget 单调性失败, 字段 {key} 强制截断"
                        )
                        ctx[key] = value[:max(10, len(value) - 1)]
                        break
                    value = candidate
                    ctx[key] = value
                if total_tokens(ctx) <= CONTEXT_TOKEN_BUDGET:
                    break

            if total_tokens(ctx) > CONTEXT_TOKEN_BUDGET:
                self.logger.bind(tag=TAG).warning(
                    f"collect_dynamic_context token 超预算: "
                    f"{total_tokens(ctx)} > {CONTEXT_TOKEN_BUDGET}"
                )
            return ctx
        except Exception:
            # tiktoken 异常时回退到原 ctx,不阻断主流程
            return ctx

    def build_enhanced_prompt(
        self, user_prompt: str, device_id: str, *args, **kwargs
    ) -> str:
        """构建增强的系统提示词（仅替换静态变量,保持 KV cache 前缀稳定）。

        动态上下文(时间/位置/天气/记忆)由 Dialogue 层在每轮对话时注入,
        不再混入系统提示词中,以最大化 KV cache 静态前缀复用率。

        H4 预防性: device_id 仅用于 device_prompt 缓存 key,
        不再注入模板 render —— 任何 connection 级变量注入都会破坏 KV cache 稳定。
        额外 kwargs 仅允许白名单（emoji_enabled）, 其它写 warn 并丢弃。
        """
        if not self.base_prompt_template:
            return user_prompt

        try:
            # 获取TTS选择的语言，默认值为中文
            language = (
                self.config.get("TTS", {})
                .get(self.config.get("selected_module", {}).get("TTS", ""), {})
                .get("language")
                or "中文"
            )
            self.logger.bind(tag=TAG).debug(f"获取到选择的语言: {language}")

            # kwargs 白名单: 防有人误传动态内容导致 KV cache 失效
            allowed_kwargs = {"emoji_enabled"}
            extra_kwargs = {k: v for k, v in kwargs.items() if k not in allowed_kwargs}
            if extra_kwargs:
                self.logger.bind(tag=TAG).warning(
                    f"build_enhanced_prompt 收到非白名单 kwargs {list(extra_kwargs.keys())},"
                    "已忽略（防止破坏 KV cache 前缀稳定）"
                )
            safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed_kwargs}

            # 仅替换静态变量,保持 KV cache 前缀稳定
            # 显式不传 device_id / client_ip（防止 connection 级变量渗入前缀）
            template = Template(self.base_prompt_template)
            enhanced_prompt = template.render(
                base_prompt=user_prompt,
                emojiList=EMOJI_List,
                language=language,
                *args,
                **safe_kwargs,
            )
            if device_id:
                device_cache_key = f"device_prompt:{device_id}"
                self.cache_manager.set(
                    self.CacheType.DEVICE_PROMPT, device_cache_key, enhanced_prompt
                )
            self.logger.bind(tag=TAG).info(
                f"构建增强提示词成功，长度: {len(enhanced_prompt)}"
            )
            return enhanced_prompt

        except Exception as e:
            self.logger.bind(tag=TAG).error(f"构建增强提示词失败: {e}")
            return user_prompt
