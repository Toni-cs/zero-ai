"""diff_edit 功能单元测试

测试 zeroai/tools/file_manager.py 中的 diff 编辑相关功能：
- apply_edit_with_diff：带 diff 预览和审批的编辑操作
- generate_diff：生成 unified diff
- apply_patch_edit：批量补丁编辑
- preview_edit：只预览不应用

测试场景覆盖：
- 正常 diff 应用 / 空 diff / 无效 diff 格式 / 文件不存在
- approval callback 拒绝 / 接受
- 多行替换 / 部分匹配 / 无匹配 / 大文件 diff
"""
import os
import tempfile

import pytest

from zeroai.tools.file_manager import (
    apply_edit_with_diff,
    apply_patch_edit,
    generate_diff,
    preview_edit,
)


# ============================================================================
# 辅助函数
# ============================================================================

def _write_temp_file(content: str, suffix: str = ".py") -> str:
    """创建临时文件并写入内容，返回文件路径"""
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        os.close(fd)
        raise
    return path


def _read_file(path: str) -> str:
    """读取文件内容（utf-8）"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_file():
    """创建一个临时文件，测试后自动清理"""
    path = _write_temp_file("line1\nline2\nline3\n")
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def temp_dir():
    """创建一个临时目录，测试后自动清理"""
    with tempfile.TemporaryDirectory() as d:
        yield d


# ============================================================================
# 测试用例
# ============================================================================

class TestDiffEdit:
    """测试 diff_edit 功能（apply_edit_with_diff）"""

    # 1. 正常 diff 应用 - 确认 diff 能正确替换文件内容
    def test_apply_diff_normal(self, temp_file):
        """测试正常 diff 应用：old_string 存在时应成功替换并写入文件"""
        result = apply_edit_with_diff(temp_file, "line2", "LINE_TWO")
        assert "已应用编辑" in result, f"应成功应用编辑，实际返回: {result}"
        # 验证文件内容已更新
        new_content = _read_file(temp_file)
        assert "LINE_TWO" in new_content, "文件应包含新内容"
        assert "line2" not in new_content, "文件不应再包含旧内容"
        assert "line1" in new_content, "未修改的部分应保留"
        assert "line3" in new_content, "未修改的部分应保留"

    # 2. 空 diff - 空的 diff 不应修改文件
    def test_apply_empty_diff(self, temp_file):
        """测试空 diff：old_string == new_string 时文件内容应保持不变"""
        original = _read_file(temp_file)
        result = apply_edit_with_diff(temp_file, "line2", "line2")
        # 即使 old==new，函数仍会"应用"但内容不变
        assert "错误" not in result or "未在文件中找到" not in result, \
            f"空 diff 不应报错，实际返回: {result}"
        # 文件内容应保持不变
        assert _read_file(temp_file) == original, "空 diff 后文件内容应保持不变"

    # 3. 无效 diff 格式 - 应抛出异常或返回错误
    def test_apply_invalid_diff(self, temp_file):
        """测试无效 diff 格式：传入 None 作为 old_string 应返回错误信息"""
        # 传入 None 作为 old_string 会触发 TypeError，被函数内部捕获返回错误
        result = apply_edit_with_diff(temp_file, None, "new")  # type: ignore[arg-type]
        assert "错误" in result, f"无效输入应返回错误，实际返回: {result}"

    # 4. 文件不存在 - 应返回错误信息（函数捕获异常返回错误字符串）
    def test_apply_diff_file_not_found(self, temp_dir):
        """测试文件不存在时的 diff：应返回包含'不存在'的错误信息

        注：apply_edit_with_diff 不会抛出 FileNotFoundError，而是返回错误字符串。
        """
        non_existent = os.path.join(temp_dir, "non_existent_file.py")
        result = apply_edit_with_diff(non_existent, "old", "new")
        assert "错误" in result, f"文件不存在应返回错误，实际返回: {result}"
        assert "不存在" in result, f"错误信息应包含'不存在'，实际返回: {result}"

    # 5. approval callback 拒绝 - 当 callback 返回 False 时不应修改文件
    def test_apply_diff_with_rejection(self, temp_file):
        """测试 approval callback 拒绝：callback 返回 False 时文件不应被修改"""
        original = _read_file(temp_file)
        reject_callback = lambda diff: False  # noqa: E731
        result = apply_edit_with_diff(temp_file, "line2", "REJECTED", approval_callback=reject_callback)
        assert "用户拒绝" in result, f"应提示用户拒绝，实际返回: {result}"
        # 文件内容应保持不变
        assert _read_file(temp_file) == original, "拒绝后文件内容应保持不变"

    # 6. approval callback 接受 - 当 callback 返回 True 时应修改文件
    def test_apply_diff_with_approval(self, temp_file):
        """测试 approval callback 接受：callback 返回 True 时应成功修改文件"""
        accept_callback = lambda diff: True  # noqa: E731
        result = apply_edit_with_diff(temp_file, "line2", "APPROVED", approval_callback=accept_callback)
        assert "已应用编辑" in result, f"应成功应用编辑，实际返回: {result}"
        new_content = _read_file(temp_file)
        assert "APPROVED" in new_content, "文件应包含新内容"
        assert "line2" not in new_content, "文件不应再包含旧内容"

    # 7. 多行替换 - 测试多行内容的 diff
    def test_apply_multiline_diff(self, temp_file):
        """测试多行 diff：多行 old_string 应被多行 new_string 正确替换"""
        # 准备多行内容
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write("header\nfunction old_func():\n    return 1\nfooter\n")
        old_multi = "function old_func():\n    return 1"
        new_multi = "function new_func():\n    return 2\n    print('updated')"
        result = apply_edit_with_diff(temp_file, old_multi, new_multi)
        assert "已应用编辑" in result, f"多行替换应成功，实际返回: {result}"
        new_content = _read_file(temp_file)
        assert "new_func" in new_content, "应包含新函数名"
        assert "return 2" in new_content, "应包含新返回值"
        assert "print('updated')" in new_content, "应包含新增行"
        assert "old_func" not in new_content, "不应再包含旧函数名"
        assert "header" in new_content, "header 应保留"
        assert "footer" in new_content, "footer 应保留"

    # 8. 部分匹配 - diff 中的 old_string 只匹配文件中的一部分
    def test_apply_partial_match(self, temp_file):
        """测试部分匹配：old_string 是文件内容的子串时应成功替换该子串"""
        # 文件内容: line1\nline2\nline3\n
        # old_string "ine2" 是 "line2" 的子串（部分匹配）
        result = apply_edit_with_diff(temp_file, "ine2", "INE_TWO")
        assert "已应用编辑" in result, f"部分匹配应成功替换，实际返回: {result}"
        new_content = _read_file(temp_file)
        assert "lINE_TWO" in new_content, "应替换匹配的子串部分"
        assert "line2" not in new_content, "原内容不应再存在"

    # 9. 无匹配 - diff 中的 old_string 在文件中不存在
    def test_apply_no_match(self, temp_file):
        """测试无匹配情况：old_string 不在文件中时应返回错误，文件不变"""
        original = _read_file(temp_file)
        result = apply_edit_with_diff(temp_file, "non_existent_string", "new")
        assert "错误" in result, f"无匹配应返回错误，实际返回: {result}"
        assert "未在文件中找到" in result or "不匹配" in result, \
            f"错误信息应说明未找到匹配，实际返回: {result}"
        # 文件内容应保持不变
        assert _read_file(temp_file) == original, "无匹配时文件内容应保持不变"

    # 10. 大文件 diff - 测试大文件的 diff 性能
    def test_apply_large_diff(self):
        """测试大文件 diff：10000 行文件应能快速完成 diff 编辑"""
        # 创建 10000 行的大文件
        large_content = "\n".join(f"line_{i}" for i in range(10000)) + "\n"
        path = _write_temp_file(large_content)
        try:
            # 替换中间的一行
            old_line = "line_5000"
            new_line = "line_5000_MODIFIED"
            result = apply_edit_with_diff(path, old_line, new_line)
            assert "已应用编辑" in result, f"大文件编辑应成功，实际返回: {result}"
            new_content = _read_file(path)
            assert "line_5000_MODIFIED" in new_content, "大文件应包含修改后的行"
            assert "line_0" in new_content, "大文件开头应保留"
            assert "line_9999" in new_content, "大文件结尾应保留"
            # 验证总行数不变
            assert new_content.count("\n") == 10000, "大文件行数应保持不变"
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestGenerateDiff:
    """测试 generate_diff 辅助函数"""

    def test_generate_diff_with_changes(self):
        """测试生成有差异的 diff"""
        diff = generate_diff("test.py", "a\nb\nc\n", "a\nB\nc\n")
        assert "B" in diff, "diff 应包含新内容"
        assert "b" in diff, "diff 应包含旧内容"
        assert "---" in diff or "@@" in diff, "diff 应包含 unified diff 标记"

    def test_generate_diff_no_changes(self):
        """测试生成无差异的 diff"""
        diff = generate_diff("test.py", "same\n", "same\n")
        assert "无差异" in diff, f"无差异时应提示'无差异'，实际返回: {diff}"


class TestPreviewEdit:
    """测试 preview_edit 只预览不应用"""

    def test_preview_does_not_modify(self, temp_file):
        """测试 preview_edit 不修改文件"""
        original = _read_file(temp_file)
        diff = preview_edit(temp_file, "line2", "PREVIEWED")
        assert "line2" in diff or "PREVIEWED" in diff, \
            f"预览应包含 diff 内容，实际返回: {diff}"
        # 文件内容应保持不变
        assert _read_file(temp_file) == original, "预览不应修改文件"

    def test_preview_no_match(self, temp_file):
        """测试 preview_edit 无匹配时返回错误"""
        result = preview_edit(temp_file, "non_existent", "new")
        assert "错误" in result, f"无匹配应返回错误，实际返回: {result}"


class TestApplyPatchEdit:
    """测试 apply_patch_edit 批量补丁编辑"""

    def test_apply_multiple_patches(self, temp_file):
        """测试批量应用多个补丁"""
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write("alpha\nbeta\ngamma\n")
        patches = [
            {"old": "alpha", "new": "ALPHA"},
            {"old": "beta", "new": "BETA"},
        ]
        result = apply_patch_edit(temp_file, patches)
        assert "已应用 2 个补丁" in result, f"应成功应用 2 个补丁，实际返回: {result}"
        new_content = _read_file(temp_file)
        assert "ALPHA" in new_content, "应包含第一个补丁结果"
        assert "BETA" in new_content, "应包含第二个补丁结果"
        assert "gamma" in new_content, "未修改部分应保留"

    def test_apply_patch_with_failure(self, temp_file):
        """测试批量补丁中某个失败时应中止并返回错误"""
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write("alpha\nbeta\n")
        patches = [
            {"old": "alpha", "new": "ALPHA"},
            {"old": "non_existent", "new": "X"},  # 这个会失败
        ]
        original = _read_file(temp_file)
        result = apply_patch_edit(temp_file, patches)
        assert "错误" in result, f"补丁失败应返回错误，实际返回: {result}"
        # 失败时文件不应被修改
        assert _read_file(temp_file) == original, "补丁失败时文件应保持不变"

    def test_apply_patch_with_rejection(self, temp_file):
        """测试批量补丁审批拒绝"""
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write("alpha\n")
        original = _read_file(temp_file)
        patches = [{"old": "alpha", "new": "ALPHA"}]
        result = apply_patch_edit(temp_file, patches, approval_callback=lambda d: False)
        assert "用户拒绝" in result, f"应提示用户拒绝，实际返回: {result}"
        assert _read_file(temp_file) == original, "拒绝后文件应保持不变"
