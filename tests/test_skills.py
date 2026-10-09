# -*- coding: utf-8 -*-
"""技能（Skills）系统测试

钉住五件事：
1. front matter 解析（pyyaml 与回退解析器两条路径都要对）
2. 触发词归一化 —— **不按空白切**、去重（回归：首版把 commit message
   切成了 message，且 提交 重复三次放大匹配分）
3. 目录优先级 builtin < user < project
4. 单个坏技能不能拖垮整体（技能是可选增强，不该让会话起不来）
5. 上下文纪律 —— 注入的只有目录，正文必须靠 skill_load 取
"""
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zeroai.core import skills as S  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_cache():
    """模块级缓存会跨测试泄漏，每个测试前后都清"""
    S.clear_cache()
    yield
    S.clear_cache()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    io.open(path, "w", encoding="utf-8").write(text)
    return path


# ─────────────────────── front matter 解析 ───────────────────────

def test_parse_yaml_list_triggers():
    meta, body = S._parse_front_matter(
        "---\nname: a\ndescription: d\ntriggers:\n  - 提交\n  - commit message\n---\n正文\n")
    assert meta["name"] == "a"
    assert body.strip() == "正文"
    assert S._as_triggers(meta) == ("提交", "commit message")


def test_parse_no_front_matter_returns_whole_body():
    meta, body = S._parse_front_matter("# 纯正文，没有 front matter\n内容")
    assert meta == {}
    assert body.startswith("# 纯正文")


def test_parse_fallback_when_yaml_unavailable(monkeypatch):
    """pyyaml 导入失败时回退解析器仍能工作（技能不能因缺依赖而失效）"""
    import builtins
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "yaml":
            raise ImportError("blocked for test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    meta, body = S._parse_front_matter(
        "---\nname: a\ntriggers: 提交, commit message\n---\nbody\n")
    assert meta.get("name") == "a"
    assert S._as_triggers(meta) == ("提交", "commit message")


def test_front_matter_requires_closing_fence():
    """没有闭合 --- 的不算 front matter，整段都是正文"""
    meta, body = S._parse_front_matter("---\nname: a\n正文")
    assert meta == {}
    assert body.startswith("---")


# ──────────────────── 触发词归一化（回归重点） ────────────────────

def test_triggers_do_not_split_on_whitespace():
    """多词触发词必须保持完整 —— 首版按空白切，`commit message` 变成了
    单词 `message`，那种宽词会在大量不相关输入上误命中。"""
    meta = {"triggers": "提交, commit message, git 提交"}
    assert S._as_triggers(meta) == ("提交", "commit message", "git 提交")


def test_triggers_deduped_case_insensitively():
    """重复触发词会把 match_score 放大数倍，让排序失去意义"""
    meta = {"triggers": ["提交", "提交", "Commit", "commit", "改动说明"]}
    out = S._as_triggers(meta)
    assert out == ("提交", "Commit", "改动说明")
    assert len(set(x.lower() for x in out)) == len(out)


def test_triggers_accepts_string_and_ignores_empty():
    assert S._as_triggers({"triggers": "a，b; c"}) == ("a", "b", "c")
    assert S._as_triggers({"triggers": "  "}) == ()
    assert S._as_triggers({}) == ()
    assert S._as_triggers({"triggers": 123}) == ()


# ─────────────────────── 发现与优先级 ───────────────────────

def test_builtin_skills_are_discoverable():
    names = [s.name for s in S.discover_skills()]
    assert "git-commit" in names, "内置技能应被发现"
    assert "python-style" in names


def test_project_overrides_builtin(tmp_path, monkeypatch):
    """优先级 builtin < user < project：同名时项目技能胜出"""
    d = tmp_path / "projskills"
    _write(str(d / "git-commit.md"),
           "---\nname: git-commit\ndescription: 项目自己的写法\n---\n项目正文")
    monkeypatch.setattr(S, "skill_dirs",
                        lambda wd=None: [(os.path.join(ROOT, "zeroai", "skills"), "builtin"),
                                         (str(tmp_path / "user"), "user"),
                                         (str(d), "project")])
    sk = S.get_skill("git-commit")
    assert sk is not None
    assert sk.source == "project"
    assert sk.body == "项目正文"


def test_broken_skill_file_does_not_kill_discovery(tmp_path, monkeypatch):
    """单个坏文件收进 errors，其余技能照常可用"""
    good = tmp_path / "good.md"
    _write(str(good), "---\nname: good\ndescription: ok\n---\n正文")
    bad = tmp_path / "bad.md"
    # **必须写字节**：写 str 的话 \x80 会被编码成合法 UTF-8，
    # 根本触发不了解码错误（首版就是这么写错的）。
    io.open(str(bad), "wb").write(b"---\nname: bad\n---\n\xff\xfe\x80 broken")
    monkeypatch.setattr(S, "skill_dirs", lambda wd=None: [(str(tmp_path), "builtin")])

    names = [s.name for s in S.discover_skills()]
    assert "good" in names, "坏文件不应影响其它技能"
    assert "bad" not in names
    assert S.skill_errors(), "坏文件应被记录"


def test_discovery_cache_invalidates_on_change(tmp_path, monkeypatch):
    d = tmp_path / "s"
    _write(str(d / "a.md"), "---\nname: a\ndescription: 1\n---\nx")
    monkeypatch.setattr(S, "skill_dirs", lambda wd=None: [(str(d), "builtin")])
    assert [s.name for s in S.discover_skills()] == ["a"]

    _write(str(d / "b.md"), "---\nname: b\ndescription: 2\n---\ny")
    # 缓存键含 mtime/size，改了文件必须自己发现，不需要手动 clear_cache
    names = [s.name for s in S.discover_skills()]
    assert "b" in names, "新增技能应被自动发现"


def test_non_md_files_ignored(tmp_path, monkeypatch):
    _write(str(tmp_path / "a.md"), "---\nname: a\n---\nx")
    _write(str(tmp_path / "note.txt"), "这不是技能")
    monkeypatch.setattr(S, "skill_dirs", lambda wd=None: [(str(tmp_path), "builtin")])
    assert [s.name for s in S.discover_skills()] == ["a"]


# ─────────────────────── 目录与注入的上下文纪律 ───────────────────────

def test_catalog_contains_names_but_not_bodies():
    """**上下文纪律**：系统提示词里只能有 name+description，
    正文一旦进去就会挤占 CONTEXT_LIMIT=8192。"""
    cat = S.render_catalog()
    assert "git-commit" in cat
    # 取一个技能正文里的独特片段，断言它没进目录
    sk = S.get_skill("git-commit")
    marker = sk.body.strip().splitlines()[0]
    assert marker not in cat, "技能正文泄漏进了系统提示词"
    # 目录要告诉模型怎么取正文
    assert "skill_load" in cat


def test_catalog_empty_when_no_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "skill_dirs", lambda wd=None: [(str(tmp_path), "builtin")])
    assert S.render_catalog() == "", "无技能必须返回空串，否则系统提示词出现空标题"


def test_inject_is_idempotent():
    once = S.inject_catalog("BASE")
    assert S.inject_catalog(once) == once, "重复注入必须幂等"


def test_inject_noop_without_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "skill_dirs", lambda wd=None: [(str(tmp_path), "builtin")])
    assert S.inject_catalog("BASE") == "BASE"


def test_catalog_line_is_bounded():
    """长描述不能把单行撑爆（注入是逐行拼接的，超长行会浪费上下文）"""
    assert all(len(l) <= 92 for l in S.render_catalog().splitlines())


# ─────────────────────── 工具入口 ───────────────────────

def test_skill_list_mentions_every_skill():
    out = S.skill_list()
    for s in S.discover_skills():
        assert s.name in out


def test_skill_list_empty_is_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "skill_dirs", lambda wd=None: [(str(tmp_path), "builtin")])
    out = S.skill_list()
    assert "无可用技能" in out
    assert ".zeroai/skills" in out, "空列表要告诉用户往哪放技能"


def test_skill_load_returns_body():
    out = S.skill_load("git-commit")
    assert out.startswith("# 技能：git-commit")
    assert S.get_skill("git-commit").body in out


def test_skill_load_missing_lists_known():
    out = S.skill_load("不存在的技能")
    assert out.startswith("错误")
    assert "git-commit" in out, "报错时应列出可用技能，否则模型只能瞎猜"


def test_skill_load_strips_whitespace_input():
    assert S.skill_load("  git-commit  ").startswith("# 技能：git-commit")


# ─────────────────────── 匹配打分 ───────────────────────

def test_match_score_prefers_triggers_over_description():
    sk = S.Skill(name="git-commit", description="一般性描述",
                 triggers=("提交",))
    assert sk.match_score("帮我提交") > sk.match_score("描述里提到一般性")


def test_match_score_zero_when_no_overlap():
    sk = S.Skill(name="git-commit", description="提交规范", triggers=("提交",))
    assert sk.match_score("今天天气怎么样") == 0
    assert sk.match_score("") == 0


def test_find_relevant_excludes_zero_score():
    out = S.find_relevant("今天天气怎么样")
    assert out == [], "无关输入不该硬凑技能"


def test_find_relevant_respects_limit(monkeypatch, tmp_path):
    for i in range(5):
        _write(str(tmp_path / f"s{i}.md"),
               f"---\nname: s{i}\ndescription: 规范\ntriggers:\n  - 规范\n---\nx")
    monkeypatch.setattr(S, "skill_dirs", lambda wd=None: [(str(tmp_path), "builtin")])
    assert len(S.find_relevant("规范", limit=2)) == 2


def test_builtin_skills_have_valid_names():
    """front matter 里的 name 不合法时会回退到文件名 —— 断言内置技能都合法"""
    import re
    for sk in S.discover_skills():
        assert re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$", sk.name), sk.name
