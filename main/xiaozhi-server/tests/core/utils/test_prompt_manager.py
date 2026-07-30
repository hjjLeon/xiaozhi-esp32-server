"""测试模板残留占位符检测 + collect_dynamic_context 动态上下文收集"""

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


class TestCollectDynamicContext:
    """KV cache 优化:PromptManager.collect_dynamic_context() 动态上下文收集。"""

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
        # 截断后字段总长(含其他基本字段)粗略小于 2400 字符(≈600 tokens 估 4字/token)
        total = sum(len(v) for v in ctx.values())
        assert total < 2400

    def test_collect_handles_exceptions_gracefully(self, sample_config, monkeypatch):
        """任一子步骤抛异常,其他字段仍能正常返回"""
        from core.utils import prompt_manager as pm_mod

        m = self._make_manager(sample_config)

        def boom():
            raise RuntimeError("simulated failure")

        # 让 lunar_date 调用失败
        monkeypatch.setattr(pm_mod, "get_current_lunar_date", boom)
        ctx = m.collect_dynamic_context(device_id="dev1")
        # lunar_date 失败时该字段应被省略,但 current_time 等应正常
        assert "current_time" in ctx
        assert "lunar_date" not in ctx or ctx.get("lunar_date") == "农历获取失败"

    def test_collect_without_tiktoken_uses_char_estimation(self, sample_config, monkeypatch):
        """C3 修复: tiktoken 不可用时 collect_dynamic_context 仍返回,按字符估算。"""
        from core.utils import prompt_manager as pm_mod

        # 强制 tiktoken 不可用
        monkeypatch.setattr(pm_mod, "_HAVE_TIKTOKEN", False)
        monkeypatch.setattr(pm_mod, "_TOKEN_ENCODING", None)

        m = self._make_manager(sample_config)
        ctx = m.collect_dynamic_context(device_id="dev1", memory_str="x" * 5000)
        # 即使超长 memory 也应返回 dict,不抛异常
        assert isinstance(ctx, dict)
        assert "current_time" in ctx
        # 超长字段应被截断(按字符估算)
        assert len(ctx["memory"]) < 5000
