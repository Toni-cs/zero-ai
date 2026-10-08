# -*- coding: utf-8 -*-
"""OpenCode V2 ↔ zero-ai 功能对齐差距分析。

## 方法

以 OpenCode V2 官方文档索引（https://opencode.ai/v2/llms.txt）的
功能项为**基准清单**，逐项在 zeroai/ 源码里检索**实现痕迹**。

不靠印象打分：每一项都必须给出命中的文件与行数，命中 0 即判为缺失。
判定只说明「有没有可辨识的实现」，不评价实现质量。

## 局限（必须说明）

- 检索是**证据导向**的：某些能力可能存在但用了非常规命名，会被漏判。
  因此结论按「已证实有 / 未找到证据」表述，不按「一定没有」。
- 面向商业 SaaS 的 Console 一节（workspace/SSO/SCIM/billing）
  不是本地 CLI 工具的能力范畴，单独归类，不计入差距。

输出 evals/results/opencode_parity.md
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "evals", "results", "opencode_parity.md")

SEARCH_ROOTS = [os.path.join(ROOT, "zeroai"),
                os.path.join(ROOT, "zeroai-tui")]


def scan():
    """返回 {文件相对路径: 源码文本}"""
    files = {}
    for base in SEARCH_ROOTS:
        if not os.path.isdir(base):
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [d for d in dn
                     if d not in ("__pycache__", "zig-cache", "zig-out")]
            for f in fn:
                if f.endswith((".py", ".zig", ".json", ".yaml", ".md")):
                    p = os.path.join(dp, f)
                    try:
                        files[os.path.relpath(p, ROOT)] = io.open(
                            p, encoding="utf-8").read()
                    except (OSError, UnicodeDecodeError):
                        pass
    return files


# (功能, 所属, 判定用的正则, 中文说明)
FEATURES = [
    # ── Configure ──
    ("Agents", "Configure", r"EXPERT_TEAM|work_mode|default_agent",
     "多智能体/专家体定义与切换"),
    ("Models", "Configure", r"MODEL_CONFIGS|model_key|base_url",
     "多模型配置与按专家分派"),
    ("Skills", "Configure", r"\bskills?\b.*\.md|SKILL|skill_",
     "可加载的技能定义"),
    ("Themes", "Configure", r"theme|ColorScheme|STYLE",
     "TUI 主题"),
    ("Commands", "Configure", r"/\w+\s*$|app_commands|_handle_command|slash",
     "斜杠命令"),
    ("Plugins", "Configure", r"\bplugin\b|register_hook|entry_point",
     "插件系统"),
    ("Providers", "Configure", r"provider|openai|anthropic|ollama|openrouter",
     "多模型提供方适配"),
    ("Websearch", "Configure", r"web_search|bing|duckduckgo|serpapi",
     "联网搜索工具"),
    ("Network", "Configure", r"proxy|no_proxy|SSRF|allowlist|denylist",
     "网络出口控制"),
    ("Snapshots", "Configure", r"snapshot|git_stash|checkpoint|restore_state",
     "状态快照与回滚"),
    ("Compaction", "Configure", r"compress_if_needed|COMPRESS_THRESHOLD|压缩历史",
     "上下文压缩"),
    ("Formatters", "Configure", r"formatter|prettier|black|clang-format",
     "输出格式化器"),
    ("References", "Configure", r"references|\bREFERENCE\b|知识库引用",
     "参考资料注入"),
    ("Attachments", "Configure", r"attachment|图片.*附|image_path|png.*read",
     "附件/图片输入"),
    ("Tools", "Configure", r"TOOL_REGISTRY|tool_registry|\"name\":.*\"description\"",
     "工具注册表"),
    ("MCP servers", "Configure", r"mcp/client|mcp/server|Model Context Protocol",
     "MCP 客户端与服务端"),
    ("Permissions", "Configure", r"allowed_commands|blocked_commands|权限|permission",
     "命令与权限控制"),
    ("Policies", "Configure", r"\bpolic(y|ies)\b|安全策略|policy_",
     "策略引擎"),
    ("Instructions", "Configure", r"system_prompt|AGENTS\.md|instructions",
     "指令/系统提示词注入"),
    ("Sharing", "Configure", r"share_|分享|export_link|gist",
     "会话分享"),
    ("Warming", "Configure", r"warm|预热|prefetch",
     "预热/预取"),

    # ── CLI ──
    ("TUI", "CLI", r"from textual|import textual|App\(.*App\)",
     "终端 UI"),
    ("Settings", "CLI", r"config\.yaml|get_config|settings",
     "配置文件与设置"),
    ("Web UI", "CLI", r"fastapi|uvicorn|@app\.|HTMLResponse",
     "浏览器界面"),
    ("CLI commands", "CLI", r"argparse|click|typer|@app\.command|__main__",
     "命令行子命令"),
    ("ACP", "CLI", r"\bACP\b|Agent Client Protocol",
     "Agent 客户端协议"),
    ("Keybinds", "CLI", r"BINDINGS|key_binding|bind\(|on_key",
     "快捷键绑定"),

    # ── Build ──
    ("Plugin API", "Build", r"load_plugin|plugin_api|hook_registry",
     "插件开发接口"),
    ("HTTP API", "Build", r"FastAPI|APIRouter|openapi|/api/",
     "对外开放 HTTP API"),
    ("JS/TS Client", "Build", r"@opencode/client|typescript.*client|sdk\.ts",
     "语言无关客户端 SDK"),
    ("SDK (embed)", "Build", r"embed|作为库引入|zeroai\.sdk",
     "可嵌入 SDK"),
]

# 面向商业 SaaS 的能力，不计入本地 CLI 工具的差距
NON_CLI = [
    "Console / Workspaces", "Members", "SSO", "SCIM", "Budgets", "Billing",
    "Cloud inference API", "Go console",
]


def main():
    files = scan()
    corpus = "\n".join(files.values())

    results = []
    for name, group, pattern, desc in FEATURES:
        hits = []
        for path, src in files.items():
            if re.search(pattern, src):
                hits.append(path)
        results.append({
            "name": name, "group": group, "desc": desc,
            "found": bool(hits), "n_files": len(hits),
            "sample": sorted(hits)[:4],
        })

    L = ["# OpenCode V2 ↔ zero-ai 功能对齐差距分析", "",
         "基准清单来源：https://opencode.ai/v2/llms.txt（官方文档索引）",
         "扫描范围：`zeroai/` + `zeroai-tui/`，共 %d 个文件" % len(files), "",
         "> **判定口径**：只回答「源码里有没有可辨识的实现痕迹」。",
         "> 命中 0 记为「未找到证据」，**不等于一定没有** —— 用了非常规命名",
         "> 的实现可能被漏判。这一限制不影响「该补什么」的排序，因为",
         "> 即便存在，不可辨识的能力在评审面前也等于没有。", ""]

    for group in ("Configure", "CLI", "Build"):
        items = [r for r in results if r["group"] == group]
        ok = [r for r in items if r["found"]]
        L += ["## %s（%d/%d 有证据）" % (group, len(ok), len(items)), "",
              "| 功能 | 判定 | 命中文件数 | 说明 |", "|---|---|---|---|"]
        for r in items:
            L.append("| **%s** | %s | %d | %s |" % (
                r["name"],
                "✅ 已证实" if r["found"] else "**❌ 未找到证据**",
                r["n_files"], r["desc"]))
        L.append("")

    missing = [r for r in results if not r["found"]]
    L += ["## 缺口汇总（按文档索引顺序）", "",
          "共 %d/%d 项未找到实现证据：" % (len(missing), len(results)), ""]
    for r in missing:
        L.append("- **%s**（%s）— %s" % (r["name"], r["group"], r["desc"]))
    L += ["", "## 不计入差距的项（商业 SaaS 范畴）", ""]
    for x in NON_CLI:
        L.append("- %s" % x)

    L += ["", "## 已证实具备的关键能力", ""]
    for r in results:
        if r["found"]:
            L.append("- **%s**（%s）: %s" % (r["name"], r["group"],
                                             ", ".join("`%s`" % s for s in r["sample"][:3])))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")

    print("扫描文件 %d 个" % len(files))
    print("有证据 %d / %d" % (len(results) - len(missing), len(results)))
    print("缺口:", ", ".join(r["name"] for r in missing))
    print("->", OUT)


if __name__ == "__main__":
    main()
