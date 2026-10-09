# -*- coding: utf-8 -*-
"""技能（Skills）系统 —— 可加载的 Markdown 指令定义

对标 OpenCode 的 Skills 机制（GitHub `anomalyco/opencode`，**MIT 许可**）。
此处为**设计对标**，非代码复制；README 中按 MIT 要求注明来源。

## 与本项目既有机制的关系

zero-ai 已有 AGENTS.md 项目上下文注入（`tui/app.py` 注释即写着"仿 OpenCode
项目上下文机制"）。Skills 是它的**同构兄弟**：

    AGENTS.md  -> 项目是什么（常驻，随对话注入）
    Skills      -> 某类任务怎么做（按需加载，不常驻）

## 为什么"只注入目录、不注入正文"

技能正文可能有几百字。全部注入会挤占上下文（本项目 CONTEXT_LIMIT=8192），
且与"技能按需加载"的语义冲突。因此：

    系统提示词里只有  name + description  的目录
    正文靠 skill_load 工具按需取回

这与 OpenCode 的做法一致，也是本模块 `render_catalog` 只输出摘要的原因。

## 目录优先级

    内置  <  用户  <  项目        （后者覆盖前者，同名时取后者）

内置：zeroai/skills/          随包分发，用户不该改
用户：~/.zeroai/skills/       跨项目通用
项目：<WORK_DIR>/.zeroai/skills/  项目专属，优先级最高

## 失败策略

单个技能文件损坏**不影响其它技能，也不抛异常** —— 错误收进
`discover_skills.errors`，由调用方决定是否展示。理由：技能是可选增强，
它坏了不应该让整个会话起不来。
"""
import io
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "Skill",
    "skill_dirs",
    "discover_skills",
    "get_skill",
    "find_relevant",
    "render_catalog",
    "inject_catalog",
    "skill_list",
    "skill_load",
    "clear_cache",
]

# 技能文件名只允许这些字符，避免路径穿越/意外匹配
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
_FRONT_MATTER = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)
_KV_LINE = re.compile(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$")

_LOCK = threading.Lock()
_CACHE: Optional[Tuple[Tuple[Tuple[str, int, int], ...], List["Skill"], List[str]]] = None


@dataclass(frozen=True)
class Skill:
    """一个技能 = 名称 + 描述 + 触发词 + 正文 + 来源路径"""

    name: str
    description: str
    triggers: Tuple[str, ...] = ()
    body: str = ""
    path: str = ""
    source: str = "builtin"  # builtin / user / project

    @property
    def summary(self) -> str:
        """目录里显示的一行摘要"""
        return self.description or self.body.strip().splitlines()[0][:60] if self.body else ""

    def match_score(self, text: str) -> int:
        """与 text 的匹配分（触发词 > 名称 > 描述）

        纯关键词打分，不用 LLM：技能发现必须廉价、确定、可复现，
        否则每条消息都要过一次模型，延迟与成本都不可控。
        """
        if not text:
            return 0
        t = text.lower()
        score = 0
        for trig in self.triggers:
            if trig and trig.lower() in t:
                score += 10
        if self.name and self.name.lower() in t:
            score += 5
        for word in re.findall(r"[\w一-鿿]{2,}", self.description.lower()):
            if word in t:
                score += 1
        return score


# ─────────────────────────── 目录发现 ───────────────────────────

def _user_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".zeroai", "skills")


def _project_dir(work_dir: Optional[str] = None) -> str:
    base = work_dir or os.environ.get("ZEROAI_WORK_DIR") or os.getcwd()
    return os.path.join(base, ".zeroai", "skills")


def skill_dirs(work_dir: Optional[str] = None) -> List[Tuple[str, str]]:
    """返回 [(目录, 来源)]，按优先级从低到高排序

    内置 < 用户 < 项目：同名技能由后者覆盖前者，
    这样项目可以覆盖本项目的通用写法，而不是反过来。
    """
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = [
        (os.path.join(here, "skills"), "builtin"),
        (_user_dir(), "user"),
        (_project_dir(work_dir), "project"),
    ]
    # 去重（比如 cwd 恰好等于包目录时 builtin 与 project 可能相同）
    seen, uniq = set(), []
    for d, s in out:
        key = os.path.normcase(os.path.abspath(d))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((d, s))
    return uniq


def _parse_front_matter(raw: str) -> Tuple[Dict[str, object], str]:
    """拆 YAML front matter 与正文

    pyyaml 是本项目已声明依赖，但技能加载不该因为它没装就整体失效 ——
    因此带一个极简回退解析器，只认 `key: value` 与单行列表。
    """
    m = _FRONT_MATTER.match(raw)
    if not m:
        return {}, raw
    head, body = m.group(1), m.group(2)
    meta: Dict[str, object] = {}
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(head)
        if isinstance(data, dict):
            return data, body
    except Exception:
        pass
    # 回退：逐行 key: value
    for line in head.splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        kv = _KV_LINE.match(line.strip())
        if not kv:
            continue
        key, val = kv.group(1), kv.group(2).strip().strip("\"'")
        if "," in val and key in ("triggers", "tags", "keywords"):
            meta[key] = [x.strip() for x in val.split(",") if x.strip()]
        else:
            meta[key] = val
    return meta, body


def _as_triggers(meta: Dict[str, object]) -> Tuple[str, ...]:
    """触发词归一化

    只按 `,，;；` 切分，**不按空白** —— 按空白切会把 `commit message`
    拆成 `commit` 和 `message` 两个词，其中 `message` 这种单词作为触发词
    过宽，会在大量不相关的输入上误命中。多词触发词必须保持完整。

    同时按小写去重：`提交` 在一行里出现三次会把匹配分放大三倍，
    让排序结果毫无意义。
    """
    val = meta.get("triggers") or meta.get("keywords") or meta.get("tags") or ()
    if isinstance(val, str):
        val = [x for x in re.split(r"[,，;；]+", val) if x]
    if not isinstance(val, (list, tuple)):
        return ()
    seen, out = set(), []
    for item in val:
        s = str(item).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return tuple(out)


def _load_one(path: str, source: str) -> Skill:
    """读单个技能文件。文件损坏时抛异常，由调用方收进 errors。"""
    name = os.path.splitext(os.path.basename(path))[0]
    raw = io.open(path, encoding="utf-8").read()
    meta, body = _parse_front_matter(raw)

    final_name = str(meta.get("name") or name).strip()
    if not _NAME_RE.match(final_name):
        # front matter 里的 name 不合法时回退到文件名，而不是拒绝整个技能
        final_name = name
    description = str(meta.get("description") or meta.get("summary") or "").strip()
    return Skill(
        name=final_name,
        description=description,
        triggers=_as_triggers(meta),
        body=body.strip(),
        path=path,
        source=source,
    )


def discover_skills(work_dir: Optional[str] = None, refresh: bool = False) -> List[Skill]:
    """扫描全部技能目录，返回按名称排序的技能列表

    带缓存，缓存键是 (路径, mtime, 大小) 三元组 —— 技能文件一改就被识别，
    不需要手动 clear_cache()。
    """
    global _CACHE
    entries: List[Tuple[str, int, int]] = []
    for d, _src in skill_dirs(work_dir):
        if not os.path.isdir(d):
            continue
        try:
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".md"):
                    continue
                p = os.path.join(d, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                entries.append((p, int(st.st_mtime), int(st.st_size)))
        except OSError:
            continue
    sig = tuple(entries)   # 保序：entries 本身已按 skill_dirs 的优先级排序
    with _LOCK:
        if not refresh and _CACHE is not None and _CACHE[0] == sig:
            return list(_CACHE[1])

    src_by_path = {p: s for d, s in skill_dirs(work_dir)
                   for p in ([os.path.join(d, f) for f in
                              (os.listdir(d) if os.path.isdir(d) else [])
                              if f.endswith(".md")])}
    skills: Dict[str, Skill] = {}
    errors: List[str] = []
    # 按 entries 的原始顺序应用 —— 即 skill_dirs() 声明的低->高优先级。
    # **不能在这里按路径排序**：那样会按目录名字典序决定谁覆盖谁，
    # `.zeroai/skills/` 会排在 `zeroai/skills/` 之前，导致项目技能被
    # 内置技能覆盖，与"项目优先"的声明正好相反。
    for path, _mt, _sz in entries:
        try:
            sk = _load_one(path, src_by_path.get(path, "builtin"))
        except Exception as e:          # 单个坏文件不能拖垮整体
            errors.append(f"{os.path.basename(path)}: {e}")
            continue
        skills[sk.name] = sk            # 后者覆盖前者（优先级已按低->高排序）

    ordered = sorted(skills.values(), key=lambda s: s.name)
    with _LOCK:
        _CACHE = (sig, ordered, errors)
    return list(ordered)


def skill_errors(work_dir: Optional[str] = None) -> List[str]:
    """最近一次扫描中损坏的技能文件（discover 之后调用才有意义）"""
    discover_skills(work_dir)
    with _LOCK:
        return list(_CACHE[2]) if _CACHE else []


def clear_cache() -> None:
    global _CACHE
    with _LOCK:
        _CACHE = None


# ─────────────────────────── 查询接口 ───────────────────────────

def get_skill(name: str, work_dir: Optional[str] = None) -> Optional[Skill]:
    for sk in discover_skills(work_dir):
        if sk.name == name:
            return sk
    return None


def find_relevant(text: str, limit: int = 3,
                  work_dir: Optional[str] = None) -> List[Skill]:
    """按匹配分返回最相关的技能（分数 <= 0 的不返回）"""
    scored = [(sk.match_score(text), sk) for sk in discover_skills(work_dir)]
    hits = [sk for s, sk in sorted(scored, key=lambda x: -x[0]) if s > 0]
    return hits[:max(0, limit)]


def render_catalog(work_dir: Optional[str] = None, max_line: int = 90) -> str:
    """系统提示词里注入的技能目录（只有 name + description，没有正文）

    无技能时返回空串 —— 调用方据此跳过注入，避免系统提示词出现空标题。
    """
    skills = discover_skills(work_dir)
    if not skills:
        return ""
    lines = ["# 可用技能（Skills）",
             "用 skill_list 查看全部，用 skill_load(name=\"...\") 取回完整正文。", ""]
    for sk in skills:
        line = f"- **{sk.name}**：{sk.summary}"
        lines.append(line[:max_line])
    return "\n".join(lines)


def inject_catalog(prompt: str, work_dir: Optional[str] = None) -> str:
    """把技能目录拼到系统提示词末尾；无技能或已注入过时原样返回"""
    cat = render_catalog(work_dir)
    if not cat or "# 可用技能（Skills）" in prompt:
        return prompt
    return prompt.rstrip() + "\n\n" + cat


# ─────────────────────────── 工具入口 ───────────────────────────

def skill_list() -> str:
    """列出全部可用技能（name / 来源 / 描述）"""
    skills = discover_skills()
    if not skills:
        return "（无可用技能。技能是 .md 文件，放在 zeroai/skills/、~/.zeroai/skills/ 或项目 .zeroai/skills/ 下。）"
    lines = [f"可用技能 {len(skills)} 个："]
    for sk in skills:
        lines.append(f"  - {sk.name} [{sk.source}]" +
                     (f"：{sk.summary}" if sk.summary else ""))
    errs = skill_errors()
    if errs:
        lines.append("（%d 个技能文件解析失败：%s）" % (len(errs), "; ".join(errs[:3])))
    return "\n".join(lines)


def skill_load(name: str) -> str:
    """按名称取回技能正文"""
    sk = get_skill(str(name).strip())
    if sk is None:
        known = ", ".join(s.name for s in discover_skills()) or "(无)"
        return f"错误：技能 {name!r} 不存在。已知技能：{known}"
    head = f"# 技能：{sk.name}"
    if sk.description:
        head += f"\n{sk.description}"
    if sk.triggers:
        head += f"\n触发词：{'、'.join(sk.triggers)}"
    return head + "\n\n" + (sk.body or "(该技能无正文)")
