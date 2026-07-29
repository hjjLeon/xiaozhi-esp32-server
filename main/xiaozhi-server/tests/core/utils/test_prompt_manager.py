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
