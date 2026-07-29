# KV Cache 静态前缀优化设计

**日期**: 2026-07-29
**状态**: 设计稿，待实施
**作者**: brainstorm + claude-code

---

## 1. 背景

`xiaozhi-esp32-server` 当前在每次 LLM 请求时，把多种"频繁变化"的动态内容拼接到 system 提示词的末尾（`<context>` 块）和 `<memory>` 块里。这类内容会导致以下运行时问题：

| 动态内容 | 频率 | 对 KV cache 的影响 |
|---|---|---|
| `{{current_time}}` | **每分钟** | 致命：跨分钟必击穿前缀缓存 |
| `<memory>...</memory>` | **每条用户消息**（top-K 相似度检索不稳定） | 严重：检索结果顺序/内容漂移 |
| `<speakers_info>` | 首轮一次性 | 中：首轮一次失效 |
| `today_date/weekday/lunar` | 每次 `build_enhanced_prompt` | 中 |
| `local_address` | 按 client_ip 永久缓存 | 低 |
| `weather_info` | 按 location 缓存 8h | 低 |

**根本原因**: KV cache（`llama.cpp` / vLLM / TGI）使用**严格前缀匹配** + **RoPE 位置编码**。system 提示末尾的动态块变化，会让后续所有历史 token 的绝对位置偏移，即使内容相同也无法复用缓存。

**目标**: 把 system 提示变成绝对静态前缀，KV cache 复用率从近 0% 提升到接近 100%。

---

## 2. 解决方案

### 2.1 核心原则
**静态前置、动态后置**：所有频繁变化的内容从 system 提示移除，统一拼装成一个 `<context>...</context>` 块，前置到最后一条 user 消息的 content 开头。

### 2.2 注入机制（方案 A）
- 单一 `<context>` 块，前置到当前 user 消息 content 开头
- 单条 user 消息，不引入连续 user 消息，兼容所有 chat_template
- `<context>` 标签借用 Anthropic `<system-reminder>` 的训练范式，模型已熟悉

### 2.3 注入位置
**Dialogue 层**（`core/utils/dialogue.py:get_llm_dialogue_with_memory`）：
- 所有 LLM provider 零改动、自动受益
- 只修改序列化输出，不动 `Message` 对象，不影响持久化

---

## 3. 组件设计

### 3.1 模板 `main/xiaozhi-server/agent-base-prompt.txt`

**删除**（硬迁移）：
- L72–80 `<context>...</context>` 整段（含 `{{current_time}}` / `{{today_date}}` / `{{today_weekday}}` / `{{lunar_date}}` / `{{local_address}}` / `{{weather_info}}` / `{{ dynamic_context }}`）
- L82–83 `<memory>...</memory>` 整段（含 `{{memory}}`）

**保留**：`<identity>` / `<core_rules>` / `<output_language>` / `<input_format>` / `<style>` / `<tts_format>` / `<tools>` / `<weather_fallback>` / `<safety_compliance>` / `<speaker_recognition>` 全部不变；Jinja 变量 `{{base_prompt}}` / `{{language}}` / `{{emojiList}}` / `{% if emoji_enabled %}` 保持。

**模板变化**：82 行（含 14 个动态占位符）→ 约 65 行（无任何动态占位符），纯静态。

### 3.2 `core/utils/prompt_manager.py`

**删除**：
- `build_enhanced_prompt()` 中 Jinja2 变量 `current_time` / `today_date` / `today_weekday` / `lunar_date` / `dynamic_context` 的 render
- `{{current_time}}` 占位符的注入逻辑

**保留**：
- `_get_location_info()` / `_get_weather_info()` 仍按 client_ip / location 缓存（提供数据，不渲染进 prompt）

**新增 API**：
```python
def collect_dynamic_context(
    self,
    *,
    device_id: str,
    memory_str: Optional[str] = None,
    speakers_info: Optional[str] = None,
) -> Dict[str, str]:
    """收集所有动态信息，返回 dict 用于组装 <context> 块。

    字段约定:
        - None 值的字段一律从 dict 中省略（键不存在），不输出空键
        - 收集过程中任一子步骤抛异常被 try/except 捕获，对应字段省略
        - 其他字段（current_time / today_date / today_weekday /
          lunar_date / local_address / weather_info）由方法内部生成，
          失败时该字段省略

    返回 dict 字段（出现则非空）:
        current_time   当前时间（HH:MM）
        today_date     今天日期（YYYY-MM-DD）
        today_weekday  星期几（中文）
        lunar_date     农历日期
        local_address  IP 地理位置（按 client_ip 缓存）
        weather_info   本地天气（按 location 缓存 8h）
        memory         记忆内容（字符串）
        speakers_info  声纹信息（字符串）
    """
```

### 3.3 `core/utils/dialogue.py`

**重写** `get_llm_dialogue_with_memory()`：

```python
def get_llm_dialogue_with_memory(
    self,
    dynamic_context: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    dialogue = []

    # 1. system 消息原样取，不再做任何替换
    system_message = next((m for m in self.dialogue if m.role == "system"), None)
    if system_message:
        dialogue.append({"role": "system", "content": system_message.content})

    # 2. few-shot 序列（保持现状）
    non_system = [m for m in self.dialogue if m.role != "system"]
    fewshot = [m for m in non_system if m.is_temporary]
    complete_fewshot = self._ensure_tool_calls_complete(fewshot)
    for m in complete_fewshot:
        self.getMessages(m, dialogue)

    # 3. 实际历史（保持现状）
    actual = [m for m in non_system if not m.is_temporary]
    complete_actual = self._ensure_tool_calls_complete(actual)
    for m in complete_actual:
        self.getMessages(m, dialogue)

    # 4. 【新】如果 dynamic_context 非空，前置注入
    if dynamic_context:
        ctx_block = self._build_context_block(dynamic_context)
        if ctx_block:
            last_user_idx = self._find_last_user_index(dialogue)
            if last_user_idx is not None:
                orig = dialogue[last_user_idx]["content"] or ""
                dialogue[last_user_idx]["content"] = f"{ctx_block}\n\n{orig}"

    return dialogue
```

**新私有方法**：
- `_build_context_block(dynamic_context) -> Optional[str]`
  - 拼装 `<context>\n- K: V\n...</context>`
  - 跳过空值（None / 空字符串 / 仅空白）
  - 字段按固定顺序：`current_time` → `today_date` → `today_weekday` → `lunar_date` → `local_address` → `weather_info` → `memory` → `speakers_info`
  - 全空返回 None
- `_find_last_user_index(dialogue) -> Optional[int]`
  - 从后往前找第一条 `role == "user"` 的索引
  - 找不到返回 None

**删除**：
- `from datetime import datetime` 不再需要
- `import re` 不再需要（memory re.sub 移除）
- `speakers_info` 内联拼装移除

**遗留 wrapper 更新**：`get_llm_dialogue()`（dialogue.py:50-53）当前调用 `self.get_llm_dialogue_with_memory(None, None)`（旧签名 `memory_str, voiceprint_config`）。签名变更后须改为 `self.get_llm_dialogue_with_memory(None)`；若该 wrapper 无其他调用方，可一并删除。

**调用点核查清单**（实施时务必 grep 全量验证）：
- `core/connection.py` —— 主调用方，本次随 §3.4 改造
- `core/utils/dialogue.py:53` —— `get_llm_dialogue()` 内部调用，按上条处理
- 其他可能的脚本 / 测试 / 外部代码

### 3.4 `core/connection.py`

**`__init__` 新增**：`self.last_speaker_for_system: Optional[str] = None`

**`chat()` 修改**：
```python
async def chat(self, query, ...):
    # 1. 原有：收集 memory
    memory_str = None
    if self.memory and query:
        memory_str = await self.memory.query_memory(query)

    # 2. 解析当前说话人
    current_speaker = self._resolve_current_speaker(...)

    # 3. 【新】决定是否需要 speakers_info（去重规则见 §4）
    speakers_info = None
    if current_speaker and current_speaker != "未知说话人":
        if current_speaker != self.last_speaker_for_system:
            speakers_info = self._build_speakers_info(current_speaker)
            self.last_speaker_for_system = current_speaker

    # 4. 【新】一次性收集 dynamic_context
    dynamic_context = self.prompt_manager.collect_dynamic_context(
        device_id=self.device_id,
        memory_str=memory_str,
        speakers_info=speakers_info,
    )

    # 5. 调 dialogue（接口签名变了）
    dialogue = self.dialogue.get_llm_dialogue_with_memory(dynamic_context)

    # 6. LLM 调用（原有）
    async for token, tool_call in self.llm.response_with_functions(
        session_id, dialogue, functions
    ):
        ...
```

**新增私有方法** `_build_speakers_info(current_speaker) -> str`：复用现有 speakers_info 拼装逻辑，提取为纯函数（`dialogue.py:128-144` 现有逻辑）。

**`close()` / 断开回调新增**：`self.last_speaker_for_system = None`

### 3.5 dynamic_context 块格式（运行时生成）

下面是一个**完整填充**的示例，运行时由代码按字段顺序逐行生成；任一字段为空则该行整体省略。

```xml
<context>
- 当前时间: 15:30
- 今天日期: 2026-07-29 (周二)
- 农历: ...
- 设备位置: 广东省深圳市南山区
- 本地天气: 晴 32°C 东南风 2级
- 记忆: 用户最近在追《三体》动画版,昨晚看到第5集
- 当前说话人: 张三 (家人,男,35岁)
- 其他说话人: 张三(家人,35岁); 李四(朋友,28岁)
</context>
```

字段顺序固定（实现见 §3.3 `_build_context_block`）：
1. `current_time`
2. `today_date`
3. `today_weekday`
4. `lunar_date`
5. `local_address`
6. `weather_info`
7. `memory`
8. `speakers_info`

**speakers_info 行格式约定**（运行时由 `_build_speakers_info()` 生成）：
- 第一行 `- 当前说话人: <name>`
- 后续每行 `- <name>:<description>`（按 voiceprint_config 中的 speakers 列表顺序）

**空值规则**：
- 任一字段为空字符串 / None / 仅空白 → 该行整行省略
- 整个 dict 全空 → 返回 None → dialogue 不做任何注入

---

## 4. 边界情况与错误处理

| 场景 | 行为 |
|------|------|
| 最后一条消息不是 user（如 assistant 正在 tool_call） | `_find_last_user_index` 返回 None → 跳过注入，dynamic_context 留到下一轮 |
| dynamic_context 全空 | 返回 None → user 消息原样发送 |
| memory_str 为 None | context 块省略 `- 记忆:` 行 |
| current_speaker 为 None 或 "未知说话人" | speakers_info 不进 dict |
| voiceprint_config 缺失或为空 | speakers_info 字段 = None |
| `datetime.now()` 抛异常 | try/except；current_time = "未知" |
| 用户自定义模板仍含 `{{current_time}}` | Jinja 渲染保留为字面字符串；运行时不做替换 |
| `collect_dynamic_context` 任一步失败 | try/except；返回部分 dict 或空 dict |
| `_build_context_block` 失败 | 返回 None → 跳过注入 |
| `_find_last_user_index` 失败 | 跳过注入 → 返回原 dialogue |

### speakers_info 去重规则（关键）

| 场景 | `last_speaker_for_system` 更新 |
|------|--------------------------------|
| `current_speaker = "张三"`（首次） | → 更新为 "张三"；注入 speakers_info |
| `current_speaker = "张三"`（第二次） | 不更新；**不**注入 |
| `current_speaker = "李四"`（变化） | → 更新为 "李四"；注入 speakers_info |
| `current_speaker = "未知说话人"` | **不更新**；保留"李四"；**不**注入 |
| `current_speaker = None` | **不更新**；**不**注入 |
| 对话结束（`close()` / 断开） | → 重置 None |

**日志策略**：
- `LoggingLLMWrapper`（`base.py:34`）记录 context 块大小（字符数）+ 各字段是否非空
- 失败注入：warn 级别，含 query hash（不含 PII）
- 不记录 context 块完整内容

**优雅降级**：所有失败路径必须保证 LLM 调用仍能成功，仅丢失动态上下文功能。

---

## 5. 数据流

```
WebSocket 握手
  ↓
ConnectionHandler.__init__
  ├─ self.last_speaker_for_system = None          [新]
  ├─ self.memory = _memory
  └─ self.prompt_manager = PromptManager(config, ...)

  ↓ (后台线程)
_init_prompt_enhancement
  └─ prompt_manager.build_enhanced_prompt()
        ├─ Jinja2 render 模板（无动态占位符）       [改动]
        ├─ 写 device_prompt:<device_id> 缓存（静态）
        └─ change_system_prompt(enhanced_prompt)

用户消息到达 → chat(query)
  ↓
self.memory.query_memory(query) → memory_str        [每条消息变]
  ↓
current_speaker = self._resolve_current_speaker()   [每条消息变]
  ↓
if current_speaker and current_speaker != "未知说话人":
    if current_speaker != self.last_speaker_for_system:
        speakers_info = self._build_speakers_info(current_speaker)
        self.last_speaker_for_system = current_speaker
  ↓
dynamic_context = prompt_manager.collect_dynamic_context(
    device_id, memory_str, speakers_info
)
  ↓
dialogue = self.dialogue.get_llm_dialogue_with_memory(dynamic_context)
  ├─ system_message.content 原样取(零修改)
  ├─ few-shot 序列(不变)
  ├─ actual 历史(不变)
  └─ 【新】前置注入：找到最后一条 user，content = "<context>...</context>\n\n" + 原 content
  ↓
async for token, tool_call in self.llm.response_with_functions(
    session_id, dialogue, functions
)
  ↓
连接断开 / close() → self.last_speaker_for_system = None
```

### 持久化保证

| 路径 | 是否含 `<context>` | 原因 |
|------|---------------------|------|
| `self.dialogue` 内存历史 | ❌ | 只修改序列化输出,不动 `Message.content` |
| 持久化 JSON 文件 | ❌ | 持久化的是 Message 对象,不受影响 |
| 注入只发生在 `get_llm_dialogue_with_memory` 返回的新 list 上 | — | 不写回 |