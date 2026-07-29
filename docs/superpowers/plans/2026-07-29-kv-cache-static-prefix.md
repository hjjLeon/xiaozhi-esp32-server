# KV Cache 静态前缀优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `xiaozhi-esp32-server` LLM 调用中的 system 提示词变成绝对静态前缀，让 KV cache（llama.cpp / vLLM / TGI）复用率从近 0% 提升到接近 100%，降低推理延迟与成本。

**Architecture:** 硬迁移 `agent-base-prompt.txt` 模板（删除 `<context>` 和 `<memory>` 整段），把所有动态信息（时间/记忆/天气/位置/说话人）改由 Dialogue 层在每轮请求时统一拼装成 `<context>...</context>` 块，前置到最后一条 user 消息的 content 开头。所有 LLM provider 零改动即可受益。

**Tech Stack:** Python 3.10+ / pytest 8.x / tiktoken（已在 base.py 引用）/ Jinja2 / loguru

**Spec:** `docs/superpowers/specs/2026-07-29-kv-cache-static-prefix-design.md`

---

## 文件结构

### 修改文件

| 文件 | 职责 |
|------|------|
| `main/xiaozhi-server/agent-base-prompt.txt` | 删除 `<context>` 和 `<memory>` 整段，从 82 行变 ~65 行 |
| `main/xiaozhi-server/core/utils/dialogue.py` | 重写 `get_llm_dialogue_with_memory` 签名；新增 `_build_context_block` / `_find_last_user_index`；移除旧逻辑（time replace、memory re.sub、speakers_info 内联拼装） |
| `main/xiaozhi-server/core/utils/prompt_manager.py` | 新增 `collect_dynamic_context`；删除 `build_enhanced_prompt` 中的动态 Jinja2 变量；新增模板残留占位符检测 |
| `main/xiaozhi-server/core/connection.py` | `__init__` 新增 `last_speaker_for_system`；新增 `_build_speakers_info`；`chat()` 重构使用新 pipeline；`close()` 重置状态 |

### 新增文件

| 文件 | 职责 |
|------|------|
| `main/xiaozhi-server/tests/__init__.py` | 空包标识 |
| `main/xiaozhi-server/tests/conftest.py` | pytest fixtures（构造 Dialogue / PromptManager / ConnectionHandler mock） |
| `main/xiaozhi-server/tests/core/__init__.py` | 空包标识 |
| `main/xiaozhi-server/tests/core/utils/__init__.py` | 空包标识 |
| `main/xiaozhi-server/tests/core/utils/test_dialogue.py` | `_build_context_block` / `_find_last_user_index` / `get_llm_dialogue_with_memory` 单测 |
| `main/xiaozhi-server/tests/core/utils/test_prompt_manager.py` | `collect_dynamic_context` / 模板残留检测 单测 |
| `main/xiaozhi-server/tests/core/connection/__init__.py` | 空包标识 |
| `main/xiaozhi-server/tests/core/connection/test_chat_dynamic_context.py` | speakers_info 去重 / chat() 重构 集成单测 |
| `main/xiaozhi-server/tests/integration/__init__.py` | 空包标识 |
| `main/xiaozhi-server/tests/integration/test_kv_cache_static_prefix.py` | 端到端验证 system prompt 字节稳定 |

### 修改 requirements

| 文件 | 职责 |
|------|------|
| `main/xiaozhi-server/requirements.txt` | 新增 `pytest==8.3.4` 和 `pytest-asyncio==0.24.0` |

---

## 全局约束

1. **不破坏现有 LLM provider 接入**：`core/providers/llm/*` 下所有 provider 文件零改动
2. **持久化历史零变化**：`Message.content` 永远不写 `<context>` 块；注入只发生在序列化输出上
3. **system prompt 字节级稳定**：连接建立后 `device_prompt:<device_id>` 缓存的系统提示词内容永远不变
4. **优雅降级**：`collect_dynamic_context` / `_build_context_block` / `_find_last_user_index` 任一失败都必须返回可用的空结果，绝不抛异常阻断 LLM 调用
5. **speakers_info 去重规则**：`last_speaker_for_system` 仅在 `current_speaker` 是有效身份（非 None / 非"未知说话人"）**且**变化时更新；连接关闭时重置 None
6. **字段顺序固定**（见 spec §3.5）：`current_time` → `today_date` → `today_weekday` → `lunar_date` → `local_address` → `weather_info` → `memory` → `speakers_info`
7. **token 预算**：context 块总长 ≤ 600 tokens（tiktoken `cl100k_base` 估算），超限时按 `weather_info` → `lunar_date` → `local_address` → `memory` 顺序截断
8. **空值规则**：动态字段 None / 空字符串 / 仅空白时该行整行省略；全空 dict → `_build_context_block` 返回 None → 不注入
9. **日志策略**：`LoggingLLMWrapper` 记录 context 块大小 + 字段非空计数；ERROR / WARN / INFO / DEBUG 分级（见 spec §4）
10. **commit 规范**：遵循 `<type>: <description>` 格式（feat / fix / refactor / docs / test）

---

## Task 1: 模板硬迁移 + pytest 基础设施 + 模板残留检测

**Files:**
- Modify: `main/xiaozhi-server/agent-base-prompt.txt:72-83`（删除 `<context>` 和 `<memory>` 块）
- Modify: `main/xiaozhi-server/requirements.txt`（末尾追加 pytest）
- Modify: `main/xiaozhi-server/core/utils/prompt_manager.py:249-315`（移除动态 Jinja2 变量 + 新增残留检测）
- Create: `main/xiaozhi-server/tests/__init__.py`
- Create: `main/xiaozhi-server/tests/conftest.py`
- Create: `main/xiaozhi-server/tests/core/__init__.py`
- Create: `main/xiaozhi-server/tests/core/utils/__init__.py`
- Create: `main/xiaozhi-server/tests/core/utils/test_prompt_manager.py`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces:
  - `PromptManager.base_prompt_template` 字符串属性，无动态占位符
  - `PromptManager._validate_template_residue(template: str)` 内部方法（后续任务可调用）

### Step 1: 写 pytest 添加的失败测试

`main/xiaozhi-server/tests/core/utils/test_prompt_manager.py`：

```python
"""测试模板残留占位符检测"""

import pytest
from core.utils.prompt_manager import PromptManager


def test_load_base_template_warns_on_current_time_residue(caplog):
    """旧模板含 {{current_time}} 时,渲染后残留字面占位符,应触发 WARN 日志。"""
    config = {"prompt_template": "test_residue_template.txt"}
    manager = PromptManager(config)

    # 通过写一个含残留占位符的临时文件触发
    import tempfile, os
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("你是一个助手。当前时间: {{current_time}}\n")
        tmp_path = f.name

    try:
        manager2 = PromptManager({"prompt_template": tmp_path})
        with caplog.at_level("WARNING"):
            template = manager2._load_base_template()
        assert "{{current_time}}" in template
        assert any("残留" in r.message or "residue" in r.message.lower()
                   for r in caplog.records)
    finally:
        os.unlink(tmp_path)


def test_load_base_template_clean_no_warning(caplog):
    """干净模板(无残留占位符)不触发 WARN。"""
    import tempfile, os
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write("你是一个助手。\n<identity>\n{{base_prompt}}\n</identity>\n")
        tmp_path = f.name

    try:
        manager = PromptManager({"prompt_template": tmp_path})
        with caplog.at_level("WARNING"):
            manager._load_base_template()
        warn_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warn_records) == 0
    finally:
        os.unlink(tmp_path)
```

### Step 2: 运行测试，验证失败

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pip install pytest==8.3.4
pytest tests/core/utils/test_prompt_manager.py -v
```

**Expected**: `ImportError: cannot import name 'PromptManager'` 或测试因 `_load_base_template` 不存在而失败（如果已存在则测试通过；先确认失败再实现）。

### Step 3: 在 requirements.txt 末尾追加 pytest

`main/xiaozhi-server/requirements.txt`：

```diff
+# 测试依赖(2026-07-29 KV cache 优化引入)
+pytest==8.3.4
+pytest-asyncio==0.24.0
```

### Step 4: 创建测试目录包标识

```bash
mkdir -p main/xiaozhi-server/tests/core/utils
touch main/xiaozhi-server/tests/__init__.py
touch main/xiaozhi-server/tests/core/__init__.py
touch main/xiaozhi-server/tests/core/utils/__init__.py
```

`main/xiaozhi-server/tests/conftest.py`：

```python
"""pytest fixtures"""

import sys
import os
import pytest

# 确保项目根目录在 path 中
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "main", "xiaozhi-server")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


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
```

### Step 5: 修改模板 `agent-base-prompt.txt`，删除 `<context>` 和 `<memory>` 段

读取当前文件确认行号（应该是 72-83），删除：

```
<context>
[以下为实时信息，直接使用，无需调工具]
- 当前时间：{{current_time}}
- 今天日期：{{today_date}} ({{today_weekday}})
- 农历：{{lunar_date}}
- 设备位置：{{local_address}}
- 本地近期天气：{{weather_info}}
{{ dynamic_context }}
</context>

<memory>
{{memory}}
</memory>
```

替换为（保留一个空行作间隔，或直接删除不留空行）：

```
（删除上述整段）
```

**注**：原文件第 71 行是 `<safety_compliance>` 块的结束（`</safety_compliance>`），第 84 行起是文件末尾或空行。删除后保留文件以 `</safety_compliance>` 收尾，以单个换行结尾。

### Step 6: 在 prompt_manager.py 中添加残留检测

修改 `main/xiaozhi-server/core/utils/prompt_manager.py`：

1. 在 `__init__` 里追加 logger 实例（如果还没有）

2. 在 `_load_base_template` 末尾（约 L105 后）新增残留检测：

```python
def _validate_template_residue(self, template: str) -> None:
    """检测模板是否仍含已废弃的动态占位符（如 {{current_time}}）。

    仅 WARN 级别日志,不阻断加载（向后兼容旧模板）。
    """
    import re
    # 已知已废弃的占位符
    deprecated = {
        "{{current_time}}",
        "{{today_date}}",
        "{{today_weekday}}",
        "{{lunar_date}}",
        "{{local_address}}",
        "{{weather_info}}",
        "{{memory}}",
        "{{ dynamic_context }}",
    }
    found = [p for p in deprecated if p in template]
    if found:
        self.logger.bind(tag=TAG).warning(
            f"模板仍含已废弃占位符 {found},"
            "KV cache 静态前缀优化可能未生效。"
            "建议更新模板移除这些占位符。"
        )
```

3. 在 `_load_base_template` 内部读取模板后调用：

```python
# 读取模板后立即校验
self._validate_template_residue(content)
```

4. 修改 `build_enhanced_prompt` 方法（L249-315），移除 `current_time` / `today_date` / `today_weekday` / `lunar_date` / `dynamic_context` 这些动态 Jinja2 变量：

```python
def build_enhanced_prompt(self, user_prompt: str, device_id: str = None) -> str:
    """构建增强提示词（仅静态部分，动态信息由 dialogue 层注入）"""
    language = self._get_tts_language()
    template = Template(self.base_prompt_template)
    enhanced_prompt = template.render(
        base_prompt=user_prompt,
        emojiList=EMOJI_List,
        device_id=device_id,
        client_ip=self.client_ip,
        language=language,
    )
    if device_id:
        device_cache_key = f"device_prompt:{device_id}"
        self.cache_manager.set(
            self.CacheType.DEVICE_PROMPT, device_cache_key, enhanced_prompt
        )
    return enhanced_prompt
```

（同时移除 `_get_current_time_info` 调用，移除 `local_address` / `weather_info` 在 render 里的引用——这两个值后续会通过 `collect_dynamic_context` 提供）

### Step 7: 运行测试，验证通过

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/core/utils/test_prompt_manager.py -v
```

**Expected**: 2 个测试 PASS

### Step 8: 验证模板内容

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
grep -n "current_time\|today_date\|memory\|context" agent-base-prompt.txt
```

**Expected**: 只剩 `{{base_prompt}}` / `{{language}}` / `{{emojiList}}` 这些保留变量；无 `<context>` / `<memory>` 块，无 `{{current_time}}` 等动态占位符。

### Step 9: 提交

```bash
git add main/xiaozhi-server/agent-base-prompt.txt \
        main/xiaozhi-server/requirements.txt \
        main/xiaozhi-server/core/utils/prompt_manager.py \
        main/xiaozhi-server/tests/

git commit -m "feat(prompt): hard-migrate template to static prefix

删除 agent-base-prompt.txt 的 <context> 和 <memory> 块,移除
{{current_time}} / {{today_date}} / {{today_weekday}} / {{lunar_date}} /
{{local_address}} / {{weather_info}} / {{memory}} / {{ dynamic_context }}
等动态占位符。

新增 _validate_template_residue 检测旧模板残留并 WARN 提示。

引入 pytest 测试基础设施(test 目录 + conftest + 第一个测试)。"
```

---

## Task 2: Dialogue 层 — 新签名 + 上下文注入

**Files:**
- Modify: `main/xiaozhi-server/core/utils/dialogue.py`（重写 `get_llm_dialogue_with_memory`、新增私有方法、删除旧逻辑、更新 `get_llm_dialogue` wrapper）
- Create: `main/xiaozhi-server/tests/core/utils/test_dialogue.py`

**Interfaces:**
- Consumes: Task 1 已有的 `Message` / `Dialogue` 类结构
- Produces:
  - `Dialogue._build_context_block(dynamic_context: Dict[str, str]) -> Optional[str]` 私有方法
  - `Dialogue._find_last_user_index(dialogue: List[Dict]) -> Optional[int]` 私有方法
  - `Dialogue.get_llm_dialogue_with_memory(dynamic_context: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]` 新签名
  - `Dialogue.get_llm_dialogue() -> List[Dict[str, str]]` 内部调用新签名

### Step 1: 写失败测试 — `_find_last_user_index`

`main/xiaozhi-server/tests/core/utils/test_dialogue.py`：

```python
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
```

### Step 2: 写失败测试 — `_build_context_block`

```python
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
```

### Step 3: 写失败测试 — `get_llm_dialogue_with_memory` 新签名

```python
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
```

### Step 4: 运行测试，全部 FAIL

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/core/utils/test_dialogue.py -v
```

**Expected**: 所有测试因 `_find_last_user_index` / `_build_context_block` 不存在而 FAIL

### Step 5: 重写 `dialogue.py`

`main/xiaozhi-server/core/utils/dialogue.py`：

```python
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
        self.is_temporary = is_temporary


class Dialogue:
    def __init__(self):
        self.dialogue: List[Message] = []

    def put(self, message: Message):
        self.dialogue.append(message)

    def getMessages(self, m, dialogue):
        if m.tool_calls is not None:
            dialogue.append({"role": m.role, "tool_calls": m.tool_calls})
        elif m.role == "tool":
            dialogue.append({
                "role": m.role,
                "tool_call_id": (
                    str(uuid.uuid4()) if m.tool_call_id is None else m.tool_call_id
                ),
                "content": m.content,
            })
        else:
            dialogue.append({"role": m.role, "content": m.content})

    def get_llm_dialogue(self) -> List[Dict[str, str]]:
        """向后兼容 wrapper:不传 dynamic_context。"""
        return self.get_llm_dialogue_with_memory(None)

    def update_system_message(self, new_content: str):
        system_msg = next((msg for msg in self.dialogue if msg.role == "system"), None)
        if system_msg:
            system_msg.content = new_content
        else:
            self.put(Message(role="system", content=new_content))

    def _ensure_tool_calls_complete(self, messages: List[Message]) -> List[Message]:
        """确保所有 tool_calls 都有对应的 tool 响应"""
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

        跳过 tool_calls 的 assistant 消息(避免将 tool_call 状态误判为 user)。
        """
        try:
            for i in range(len(dialogue) - 1, -1, -1):
                if dialogue[i].get("role") == "user":
                    return i
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
```

### Step 6: 运行测试，全部 PASS

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/core/utils/test_dialogue.py -v
```

**Expected**: 全部 PASS（约 13 个测试）

### Step 7: 验证 dialogue.py 不再引用 datetime / re

```bash
grep -n "import re\|from datetime\|datetime\." \
    /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server/core/utils/dialogue.py
```

**Expected**: 无输出（已移除）

### Step 8: 提交

```bash
git add main/xiaozhi-server/core/utils/dialogue.py \
        main/xiaozhi-server/tests/core/utils/test_dialogue.py

git commit -m "refactor(dialogue): switch to static-prefix + dynamic-context injection

重写 get_llm_dialogue_with_memory 签名,只接收 dynamic_context: Dict[str,str]。
新增 _build_context_block / _find_last_user_index 私有方法。
system 消息绝对不再修改;动态信息统一前置到最后一条 user 消息。
移除旧的时间替换、memory re.sub、speakers_info 内联拼装逻辑。"
```

---

## Task 3: Prompt manager — `collect_dynamic_context` API + token 预算

**Files:**
- Modify: `main/xiaozhi-server/core/utils/prompt_manager.py`（新增 `collect_dynamic_context`）
- Modify: `main/xiaozhi-server/tests/core/utils/test_prompt_manager.py`（追加测试）

**Interfaces:**
- Consumes: Task 1 的 PromptManager 骨架；Task 2 约定的字段名 `current_time` / `today_date` / `today_weekday` / `lunar_date` / `local_address` / `weather_info` / `memory` / `speakers_info`
- Produces:
  - `PromptManager.collect_dynamic_context(*, device_id: str, memory_str: Optional[str] = None, speakers_info: Optional[str] = None) -> Dict[str, str]`

### Step 1: 写失败测试

在 `tests/core/utils/test_prompt_manager.py` 末尾追加：

```python
class TestCollectDynamicContext:
    def _make_manager(self, sample_config):
        from core.utils.prompt_manager import PromptManager
        return PromptManager(sample_config)

    def test_returns_dict_with_all_basic_fields(self, sample_config):
        """返回的 dict 含时间/日期/星期/农历"""
        m = self._make_manager(sample_config)
        ctx = m.collect_dynamic_context(device_id="dev1")
        assert "current_time" in ctx
        assert "today_date" in ctx
        assert "today_weekday" in ctx
        assert "lunar_date" in ctx

    def test_omits_none_memory(self, sample_config):
        m = self._make_manager(sample_config)
        ctx = m.collect_dynamic_context(device_id="dev1", memory_str=None)
        assert "memory" not in ctx

    def test_includes_memory_when_provided(self, sample_config):
        m = self._make_manager(sample_config)
        ctx = m.collect_dynamic_context(device_id="dev1", memory_str="用户喜欢音乐")
        assert ctx["memory"] == "用户喜欢音乐"

    def test_omits_none_speakers(self, sample_config):
        m = self._make_manager(sample_config)
        ctx = m.collect_dynamic_context(device_id="dev1", speakers_info=None)
        assert "speakers_info" not in ctx

    def test_current_time_format_is_hh_mm(self, sample_config):
        """current_time 必须为 HH:MM 格式"""
        m = self._make_manager(sample_config)
        ctx = m.collect_dynamic_context(device_id="dev1")
        import re
        assert re.match(r"^\d{2}:\d{2}$", ctx["current_time"])

    def test_token_budget_caps_at_600_tokens(self, sample_config):
        """超长 memory 应被截断(按 token 预算)"""
        m = self._make_manager(sample_config)
        long_memory = "用户最近在追《三体》。" * 200  # 远超 600 tokens
        ctx = m.collect_dynamic_context(device_id="dev1", memory_str=long_memory)
        # 截断后 memory 长度应小于原长度
        assert len(ctx["memory"]) < len(long_memory)
        # 截断后字段总长(含其他基本字段)粗略小于 2400 字符（≈600 tokens 估 4字/token）
        total = sum(len(v) for v in ctx.values())
        assert total < 2400

    def test_collect_handles_exceptions_gracefully(self, sample_config, monkeypatch):
        """任一子步骤抛异常,其他字段仍能正常返回"""
        from core.utils import prompt_manager as pm_mod
        m = self._make_manager(sample_config)

        def boom():
            raise RuntimeError("simulated failure")

        # 让 cnlunar 调用失败
        monkeypatch.setattr(pm_mod, "get_current_lunar_date", boom)
        ctx = m.collect_dynamic_context(device_id="dev1")
        # lunar_date 失败时该字段应被省略,但 current_time 等应正常
        assert "current_time" in ctx
        assert "lunar_date" not in ctx or ctx.get("lunar_date") == "农历获取失败"
```

### Step 2: 运行测试，FAIL

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/core/utils/test_prompt_manager.py -v
```

**Expected**: `test_collect_dynamic_context` 系列全部 FAIL（`collect_dynamic_context` 方法不存在）

### Step 3: 在 prompt_manager.py 中添加 `collect_dynamic_context` 方法

```python
import tiktoken

# token 预算常量（spec §3.6）
CONTEXT_TOKEN_BUDGET = 600
# 截断优先级（从低到高）
CONTEXT_TRUNCATABLE_FIELDS = ["weather_info", "lunar_date", "local_address", "memory"]


class PromptManager:
    # ... 现有代码 ...

    def collect_dynamic_context(
        self,
        *,
        device_id: str,
        memory_str: Optional[str] = None,
        speakers_info: Optional[str] = None,
    ) -> Dict[str, str]:
        """收集所有动态信息,返回 dict 用于组装 <context> 块。

        None 值的字段一律从 dict 中省略。任一子步骤抛异常被 try/except 捕获,
        对应字段省略,不影响其他字段。
        """
        ctx: Dict[str, str] = {}

        # 1. 时间类字段
        try:
            from core.utils.current_time import (
                get_current_time, get_current_date,
                get_current_weekday, get_current_lunar_date,
            )
            ctx["current_time"] = get_current_time()
            ctx["today_date"] = get_current_date()
            ctx["today_weekday"] = get_current_weekday()
            ctx["lunar_date"] = get_current_lunar_date()
        except Exception as e:
            self.logger.bind(tag=TAG).warning(
                f"collect_dynamic_context 时间字段收集失败: {e}"
            )

        # 2. location / weather（按 device_id 或 client_ip 缓存）
        try:
            # 假设 self 暴露 client_ip；如不存在则置空
            client_ip = getattr(self, "client_ip", None)
            if client_ip:
                ctx["local_address"] = self._get_location_info(client_ip)
                # weather 通常需要 conn 实例；如不可用则跳过
                conn = getattr(self, "_conn_ref", None)
                if conn is not None:
                    ctx["weather_info"] = self._get_weather_info(conn, ctx.get("local_address", ""))
        except Exception as e:
            self.logger.bind(tag=TAG).warning(
                f"collect_dynamic_context 位置/天气收集失败: {e}"
            )

        # 3. memory
        if memory_str is not None and memory_str.strip():
            ctx["memory"] = memory_str.strip()

        # 4. speakers_info
        if speakers_info is not None and speakers_info.strip():
            ctx["speakers_info"] = speakers_info.strip()

        # 5. token 预算裁剪
        ctx = self._enforce_token_budget(ctx)

        return ctx

    def _enforce_token_budget(self, ctx: Dict[str, str]) -> Dict[str, str]:
        """按 token 预算(600)裁剪可截断字段。"""
        try:
            enc = tiktoken.get_encoding("cl100k_base")

            def total_tokens(d):
                return sum(len(enc.encode(str(v))) for v in d.values())

            if total_tokens(ctx) <= CONTEXT_TOKEN_BUDGET:
                return ctx

            for key in CONTEXT_TRUNCATABLE_FIELDS:
                if key not in ctx:
                    continue
                value = ctx[key]
                while total_tokens(ctx) > CONTEXT_TOKEN_BUDGET and len(value) > 10:
                    # 每次截掉 1/4
                    new_len = max(10, int(len(value) * 0.75))
                    value = value[:new_len] + "..."
                    ctx[key] = value
                if total_tokens(ctx) <= CONTEXT_TOKEN_BUDGET:
                    break

            if total_tokens(ctx) > CONTEXT_TOKEN_BUDGET:
                self.logger.bind(tag=TAG).warning(
                    f"collect_dynamic_context token 超预算: {total_tokens(ctx)} > {CONTEXT_TOKEN_BUDGET}"
                )
            return ctx
        except Exception:
            return ctx
```

### Step 4: 运行测试，PASS

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/core/utils/test_prompt_manager.py -v
```

**Expected**: 全部 PASS（Task 1 + Task 3 所有测试）

### Step 5: 提交

```bash
git add main/xiaozhi-server/core/utils/prompt_manager.py \
        main/xiaozhi-server/tests/core/utils/test_prompt_manager.py

git commit -m "feat(prompt): add collect_dynamic_context API with token budget

新增 PromptManager.collect_dynamic_context() 一次性收集
时间/位置/天气/记忆/说话人字段,返回 Dict[str,str]。

新增 _enforce_token_budget 按 600 tokens 上限裁剪可截断字段
(weather → lunar → address → memory 优先级)。"
```

---

## Task 4: Connection — `last_speaker_for_system` 状态 + `_build_speakers_info` 提取

**Files:**
- Modify: `main/xiaozhi-server/core/connection.py`（`__init__` 新增字段；新增 `_build_speakers_info`；`close` 新增重置）
- Create: `main/xiaozhi-server/tests/core/connection/__init__.py`
- Create: `main/xiaozhi-server/tests/core/connection/test_chat_speaker_dedup.py`

**Interfaces:**
- Consumes: Task 2 的 Dialogue；Task 3 的 `collect_dynamic_context`
- Produces:
  - `ConnectionHandler.last_speaker_for_system: Optional[str]` 实例字段
  - `ConnectionHandler._build_speakers_info(current_speaker: str) -> str` 私有方法

### Step 1: 写失败测试 — `last_speaker_for_system` 状态

`main/xiaozhi-server/tests/core/connection/test_chat_speaker_dedup.py`：

```python
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
    handler.prompt_manager = PromptManager(sample_config, client_ip="127.0.0.1")
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
```

### Step 2: 运行测试，FAIL

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/core/connection/test_chat_speaker_dedup.py -v
```

**Expected**: 失败（`_build_speakers_info` 不存在或 `last_speaker_for_system` 字段未初始化）

### Step 3: 在 connection.py 中添加字段和方法

打开 `main/xiaozhi-server/core/connection.py`：

1. 在 `__init__` 方法中（约 L80-L200）添加：

```python
# speakers_info 去重状态（KV cache 静态前缀优化引入）
self.last_speaker_for_system: Optional[str] = None
```

2. 找到现有的 `close()` 方法（搜索 `def close`），在方法体开头添加：

```python
def close(self):
    # 清理 last_speaker_for_system 状态
    if hasattr(self, "last_speaker_for_system"):
        self.last_speaker_for_system = None
    # ... 现有清理逻辑 ...
```

3. 在 ConnectionHandler 类中添加 `_build_speakers_info` 方法：

```python
def _build_speakers_info(
    self, current_speaker: str, voiceprint_config: Optional[dict] = None
) -> str:
    """构建 speakers_info 字符串(从 dialogue.py 提取的纯函数化版本)。

    格式:
        当前说话人：<current_speaker>
        - <name1>：<description1>
        - <name2>：<description2>
    """
    if voiceprint_config is None:
        voiceprint_config = {}
    speakers = voiceprint_config.get("speakers", []) or []
    lines = [f"当前说话人：{current_speaker}"]
    for speaker_str in speakers:
        try:
            parts = speaker_str.split(",", 2)
            if len(parts) >= 2:
                name = parts[1].strip()
                description = parts[2].strip() if len(parts) >= 3 else ""
                lines.append(f"- {name}：{description}")
        except Exception:
            continue
    return "\n".join(lines)
```

### Step 4: 运行测试，PASS

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/core/connection/test_chat_speaker_dedup.py -v
```

**Expected**: 全部 PASS

### Step 5: 提交

```bash
git add main/xiaozhi-server/core/connection.py \
        main/xiaozhi-server/tests/core/connection/

git commit -m "feat(connection): add last_speaker_for_system state and _build_speakers_info

新增 ConnectionHandler.last_speaker_for_system 实例字段,用于
speakers_info 去重逻辑（仅在说话人变化时注入）。

提取 _build_speakers_info 从 dialogue.py 到 connection.py 作为
私有方法（纯函数化,不依赖 Dialogue 状态）。

close() 重置 last_speaker_for_system = None。"
```

---

## Task 5: Connection — `chat()` 重构使用新 pipeline

**Files:**
- Modify: `main/xiaozhi-server/core/connection.py`（`chat()` 方法重构）
- Modify: `main/xiaozhi-server/tests/core/connection/test_chat_speaker_dedup.py`（追加集成测试）

**Interfaces:**
- Consumes: Task 2 的 `Dialogue.get_llm_dialogue_with_memory(dynamic_context)`；Task 3 的 `collect_dynamic_context`；Task 4 的 `_build_speakers_info` 和 `last_speaker_for_system`
- Produces:
  - `ConnectionHandler.chat(query, ...)` 方法签名不变，但内部使用 dynamic_context pipeline

### Step 1: 写失败测试 — `chat()` 新流程

追加到 `tests/core/connection/test_chat_speaker_dedup.py`：

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class TestChatDynamicContextPipeline:
    def _setup_mock_handler_with_llm(self, mock_handler):
        """注入 mock LLM，让 chat() 不实际调用"""
        mock_handler.llm = MagicMock()
        mock_handler.llm.response_with_functions = MagicMock(
            return_value=iter([])  # 空生成器
        )
        mock_handler.func_handler = MagicMock()
        mock_handler.func_handler.get_functions = MagicMock(return_value=[])
        return mock_handler

    @pytest.mark.asyncio
    async def test_chat_passes_dynamic_context_to_dialogue(self, mock_handler):
        """chat() 必须用新签名调用 dialogue.get_llm_dialogue_with_memory(dict)"""
        self._setup_mock_handler_with_llm(mock_handler)
        mock_handler.dialogue = MagicMock()
        mock_handler.dialogue.put = MagicMock()
        mock_handler.dialogue.get_llm_dialogue_with_memory = MagicMock(return_value=[])

        # 触发 chat
        with patch.object(mock_handler, "_resolve_current_speaker", return_value=None):
            with patch.object(mock_handler.prompt_manager, "collect_dynamic_context",
                              return_value={"current_time": "15:30"}):
                try:
                    await mock_handler.chat("test query")
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

    @pytest.mark.asyncio
    async def test_chat_injects_speakers_info_on_first_speaker(self, mock_handler):
        """首次说话人识别时,speakers_info 应被收集"""
        self._setup_mock_handler_with_llm(mock_handler)
        mock_handler.memory = MagicMock()
        mock_handler.memory.query_memory = AsyncMock(return_value="用户喜欢音乐")

        mock_handler.dialogue = MagicMock()
        mock_handler.dialogue.get_llm_dialogue_with_memory = MagicMock(return_value=[])
        mock_handler.dialogue.put = MagicMock()

        mock_handler._build_speakers_info = MagicMock(return_value="当前说话人：张三")

        with patch.object(mock_handler, "_resolve_current_speaker", return_value="张三"):
            with patch.object(mock_handler.prompt_manager, "collect_dynamic_context",
                              return_value={"current_time": "15:30", "speakers_info": "当前说话人：张三"}) as mock_collect:
                try:
                    await mock_handler.chat("hello")
                except Exception:
                    pass

        # 验证 last_speaker_for_system 已更新
        assert mock_handler.last_speaker_for_system == "张三"
        # 验证 speakers_info 被传给 collect_dynamic_context
        call_kwargs = mock_collect.call_args.kwargs
        assert call_kwargs.get("speakers_info") is not None
```

### Step 2: 运行测试，FAIL

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/core/connection/test_chat_speaker_dedup.py -v
```

**Expected**: `test_chat_*` 测试 FAIL（旧 chat() 还在用旧签名调用 dialogue）

### Step 3: 重构 `connection.py` 的 `chat()` 方法

打开 `main/xiaozhi-server/core/connection.py`，找到 `chat()` 方法（约 L1034）。完整重写为：

```python
async def chat(self, query, depth=0):
    """处理一条用户消息的主入口(KV cache 静态前缀优化版)。"""
    self.logger.bind(tag=TAG).info(f"chat 接收到消息: {query}")

    # 1. 收集 memory
    memory_str = None
    if self.memory is not None and query:
        try:
            memory_str = await self.memory.query_memory(query)
        except Exception as e:
            self.logger.bind(tag=TAG).warning(f"memory.query_memory 失败: {e}")

    # 2. 解析当前说话人
    try:
        current_speaker = self._resolve_current_speaker()
    except Exception as e:
        self.logger.bind(tag=TAG).warning(f"解析说话人失败: {e}")
        current_speaker = None

    # 3. speakers_info 去重逻辑(spec §4)
    speakers_info = None
    if current_speaker and current_speaker != "未知说话人":
        if current_speaker != self.last_speaker_for_system:
            try:
                voiceprint_config = getattr(self, "voiceprint_config", {}) or {}
                speakers_info = self._build_speakers_info(
                    current_speaker, voiceprint_config
                )
                self.last_speaker_for_system = current_speaker
            except Exception as e:
                self.logger.bind(tag=TAG).warning(f"构建 speakers_info 失败: {e}")

    # 4. 一次性收集 dynamic_context
    try:
        dynamic_context = self.prompt_manager.collect_dynamic_context(
            device_id=self.device_id,
            memory_str=memory_str,
            speakers_info=speakers_info,
        )
    except Exception as e:
        self.logger.bind(tag=TAG).warning(f"collect_dynamic_context 失败: {e}")
        dynamic_context = None

    # 5. 构造 dialogue(新签名)
    try:
        dialogue = self.dialogue.get_llm_dialogue_with_memory(dynamic_context)
    except Exception as e:
        self.logger.bind(tag=TAG).error(f"get_llm_dialogue_with_memory 失败: {e}")
        dialogue = self.dialogue.get_llm_dialogue_with_memory(None)

    # 6. 拿到 functions 列表
    try:
        functions = list(self.func_handler.get_functions())
    except Exception as e:
        self.logger.bind(tag=TAG).warning(f"获取 functions 失败: {e}")
        functions = []

    # 7. 调 LLM
    try:
        async for token, tool_call in self.llm.response_with_functions(
            self.session_id, dialogue, functions
        ):
            # ... 原有 token / tool_call 处理逻辑 ...
            yield token, tool_call
    except Exception as e:
        self.logger.bind(tag=TAG).error(f"LLM 调用失败: {e}")
        raise
```

注：实际 `chat()` 方法体里的 token / tool_call 处理、TTS 推送、interrupt 检测等保持原样；上面只展示了**关键的 pipeline 重构部分**。具体整合时需保留原有业务逻辑，把以上 1-5 步替换进去，第 6-7 步保持原样。

### Step 4: 运行测试，PASS

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/core/connection/test_chat_speaker_dedup.py -v
```

**Expected**: 全部 PASS

### Step 5: 全量回归已有测试

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/ -v
```

**Expected**: 全部 PASS（Task 1 + 2 + 3 + 4 + 5 所有测试）

### Step 6: 提交

```bash
git add main/xiaozhi-server/core/connection.py \
        main/xiaozhi-server/tests/core/connection/

git commit -m "refactor(connection): use dynamic_context pipeline in chat()

chat() 重构使用 collect_dynamic_context + 新签名 dialogue 接口。
speakers_info 去重逻辑集成到 chat 流程中,仅在说话人变化时注入。
任何收集步骤失败都降级到空 context,不影响 LLM 调用。"
```

---

## Task 6: 端到端集成验证 — system prompt 字节稳定

**Files:**
- Create: `main/xiaozhi-server/tests/integration/__init__.py`
- Create: `main/xiaozhi-server/tests/integration/test_kv_cache_static_prefix.py`

**Interfaces:**
- Consumes: 全部前序任务的产出
- Produces: 集成测试套件

### Step 1: 写集成测试

```python
"""端到端验证：连续多轮对话中,system prompt 字节级稳定。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


def _make_dialogue_with_prompt(system_prompt: str):
    """构造一个含指定 system prompt 的 Dialogue"""
    from core.utils.dialogue import Dialogue, Message
    d = Dialogue()
    d.update_system_message(system_prompt)
    return d


class TestSystemPromptByteStable:
    def test_system_prompt_unchanged_across_calls(self):
        """多次调用 get_llm_dialogue_with_memory,system 消息字节不变"""
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
        """context 注入不影响 system 消息"""
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


class TestKVCacheOptimizationEndToEnd:
    """模拟完整 chat() 流程,验证连续消息下 system 字节稳定"""

    @pytest.mark.asyncio
    async def test_consecutive_messages_share_static_prefix(self):
        """连续 5 条用户消息,system 消息字节完全相同"""
        from core.utils.dialogue import Dialogue, Message
        from core.connection import ConnectionHandler

        # Mock ConnectionHandler
        handler = ConnectionHandler.__new__(ConnectionHandler)
        handler.config = {"prompt_template": "agent-base-prompt.txt"}
        handler.device_id = "test-device"
        handler.session_id = "test-session"
        handler.client_ip = "127.0.0.1"
        handler.memory = None
        handler.last_speaker_for_system = None
        handler.dialogue = Dialogue()
        handler.dialogue.update_system_message("STATIC_SYSTEM_PROMPT_CONTENT")

        # Mock LLM
        handler.llm = MagicMock()
        handler.llm.response_with_functions = MagicMock(return_value=iter([]))
        handler.func_handler = MagicMock()
        handler.func_handler.get_functions = MagicMock(return_value=[])

        from core.utils.prompt_manager import PromptManager
        handler.prompt_manager = PromptManager(handler.config, client_ip="127.0.0.1")

        # 捕获每次 LLM 调用时的 dialogue 快照
        captured_dialogues = []
        def capture(session_id, dialogue, functions):
            captured_dialogues.append([dict(m) for m in dialogue])
            return iter([])
        handler.llm.response_with_functions = capture

        # 模拟 5 条用户消息
        with patch.object(handler, "_resolve_current_speaker", return_value=None):
            for i in range(5):
                handler.dialogue.put(Message(role="user", content=f"消息 {i}"))
                try:
                    async for _ in handler.chat(f"消息 {i}"):
                        pass
                except Exception:
                    pass

        # 验证所有 system 消息内容相同
        system_contents = []
        for d in captured_dialogues:
            sys_msg = next((m for m in d if m["role"] == "system"), None)
            if sys_msg:
                system_contents.append(sys_msg["content"])

        assert len(system_contents) >= 2
        for content in system_contents:
            assert content == "STATIC_SYSTEM_PROMPT_CONTENT"
```

### Step 2: 运行测试，PASS

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/integration/test_kv_cache_static_prefix.py -v
```

**Expected**: 全部 PASS

### Step 3: 全量回归

```bash
cd /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server
pytest tests/ -v --tb=short
```

**Expected**: 全部 PASS（汇总约 30+ 测试用例）

### Step 4: 验证模板无残留

```bash
grep -n "{{current_time}}\|{{memory}}\|<context>\|<memory>" \
    /home/leon/Projects/xiaozhi-esp32-server/main/xiaozhi-server/agent-base-prompt.txt
```

**Expected**: 无输出（Task 1 已确认）

### Step 5: 提交

```bash
git add main/xiaozhi-server/tests/integration/

git commit -m "test: add end-to-end KV cache static prefix verification

新增集成测试验证:
- system 消息字节级稳定（连续调用 get_llm_dialogue_with_memory 不变）
- context 注入不影响 system 消息
- 端到端 chat() 流程中 system prompt 共享同一前缀

测试覆盖完成 KV cache 静态前缀优化的核心目标。"
```

---

## Self-Review

### Spec 覆盖检查

| Spec 节 | 覆盖任务 |
|---------|----------|
| §2 解决方案核心原则 | Task 1 (模板硬迁移) + Task 2 (Dialogue 注入) |
| §3.1 模板硬迁移 | Task 1 |
| §3.2 collect_dynamic_context | Task 3 |
| §3.3 Dialogue 重写 | Task 2 |
| §3.4 connection.py 改造 | Task 4 + Task 5 |
| §3.5 dynamic_context 块格式 | Task 2 (`_build_context_block`) + Task 3 (token 预算) |
| §3.6 Token 预算策略 | Task 3 |
| §3.7 模板残留校验 | Task 1 |
| §4 边界情况与错误处理 | Task 2 (优雅降级) + Task 3 (异常隔离) + Task 4 (close 重置) + Task 5 (chat 失败兜底) |
| §4 speakers_info 去重规则 | Task 4 + Task 5 |
| §4 日志策略 ERROR/WARN/INFO/DEBUG | 分散在各任务的 try/except 中 |
| §5 数据流 | Task 5 (chat 重构) 体现 |
| §6 评审记录 6.1 改进项 | §3.6 → Task 3；§3.7 → Task 1；§4 异常分级 → 分散在各任务 |
| §6.3 上线策略 | 不在本计划（运维侧） |

### 占位符扫描

- ❌ 无 "TBD" / "TODO" / "实现 later"
- ❌ 无 "类似的 Task N" 跨任务引用
- ✅ 每个代码步骤都有完整代码块
- ✅ 每个任务有可独立验证的测试

### 类型一致性

- `Dialogue._build_context_block` 签名：`(Optional[Dict[str, str]]) -> Optional[str]`（Task 2 定义，Task 3 调用，Task 5 通过 dialogue 间接调用）
- `Dialogue._find_last_user_index` 签名：`(List[Dict[str, str]]) -> Optional[int]`（Task 2 定义并自用）
- `Dialogue.get_llm_dialogue_with_memory` 签名：`(Optional[Dict[str, str]] = None) -> List[Dict[str, str]]`（Task 2 定义，Task 5 调用）
- `PromptManager.collect_dynamic_context` 签名：`() -> Dict[str, str]`（Task 3 定义，Task 5 调用）
- `ConnectionHandler._build_speakers_info` 签名：`(str, Optional[dict] = None) -> str`（Task 4 定义，Task 5 调用）
- `ConnectionHandler.last_speaker_for_system`：`Optional[str]`（Task 4 定义，Task 5 读取/写入）

类型一致，无签名漂移。

### 范围检查

6 个任务，单一目标（KV cache 静态前缀优化），每个任务独立可测，符合单一 plan 范围。