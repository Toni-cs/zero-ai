"""专家路由与 OpenRouter 熔断器

迁移来源：tui_agent.py 行 1002-1155

提供：
- route_expert：关键词快速预判（GLM 语义路由的降级方案）
- LRUCache / _expert_route_cache：GLM 语义路由结果缓存
- route_expert_glm：基于 GLM 的语义路由（异步）
- get_expert_config：获取专家对应的模型配置
- OpenRouter 熔断器：
  * _is_openrouter_expert：判断专家是否依赖 OpenRouter
  * _check_openrouter_circuit_breaker：检查是否已熔断
  * _record_openrouter_failure / _record_openrouter_success：记录调用结果

依赖关系：
- constants.py：EXPERT_TEAM, MODEL_CONFIGS
- secrets.py：_make_openai_client
- runtime.py：_interruptible_await
"""
import re
import hashlib
from collections import OrderedDict

from .constants import EXPERT_TEAM, MODEL_CONFIGS
from .secrets import _make_openai_client
from .runtime import _interruptible_await


# 专家并列裁决顺序：评分的三个分量完全相同时按此定胜负。
# 保留它是为了让结果确定（不依赖 dict 插入顺序），而不是用来抢先命中。
_ROUTE_ORDER = ("coder", "security", "devops", "data", "reasoner",
                "academic", "chinese", "pm")
_PRIORITY_IDX = {k: i for i, k in enumerate(_ROUTE_ORDER)}
_PRIORITY_IDX["vision"] = -1

# knowledge 的并列位置 = 3：排在 reasoner/academic/chinese/pm 之前、
# coder/security/devops 之后。
#
# 这是实测选出来的，不是拍脑袋 —— knowledge 原本零关键词且不在遍历顺序
# 里，只能当兜底，使其成为两个评测集上错判率最高的专家（dev 9/23）。
# 给它词表后，「为什么天空是蓝色的」这类**事实性为什么**与 reasoner 的
# 「为什么」必然撞分，此时谁排前谁赢：knowledge 必须排在 reasoner 前。
# 而 coder/security/devops 排在它前面，是因为这三家的词（代码/安全/
# 部署）比「为什么」更具体，撞分时更可信。
# 位置 0 与 3 实测结果完全相同（见 evals/results/route_variants.md
# V5a/V5b），说明只需"在 reasoner 之前"，取 3 更保守。
_PRIORITY_IDX["knowledge"] = 3

# vision 命中即几乎必胜的加成。取值只需远大于单个专家可能的命中词数
# （最多 ~40），100 足够；效果等价于原来的"vision 最先判、命中即返回"，
# 避免「看这张截图」被 coder 的「看/打开/浏览」抢走。
_VISION_BONUS = 100


def _kw_hit(keyword: str, text: str, normalize: bool = False) -> bool:
    """单个关键词是否命中。text 必须已 lower()。

    前两种情况与历史行为完全一致：
    1. 纯英文且长度 >4 —— 用 \\b 词边界。注：中英文混排时 \\b 会失效
       （中文和英文字符同属 \\w），所以短英文词走第 2 种。
    2. 其余用子串匹配。

    第 3 种是新增的：关键词含中文时，**再拿去空格的文本匹配一次**。
    动机是实测而非猜测 —— 「SQL 注入的参数化查询怎么写」里 "SQL" 与
    "注入" 之间有空格，security 的 `sql注入` 因此永远匹配不上，只剩
    coder 的 `sql` 命中，安全问题被判给编程专家。中文正文里给英文词
    加空格是常见排版习惯，这不是个别样本的问题。
    """
    k = keyword.lower()
    if k.isascii() and k.isalpha() and len(k) > 4:
        return re.search(r"\b" + re.escape(k) + r"\b", text) is not None
    if k in text:
        return True
    if normalize and any("一" <= ch <= "鿿" for ch in k):
        return k in text.replace(" ", "").replace("　", "")
    return False


def route_expert(user_input: str) -> str:
    """关键词快速预判（作为 GLM 语义判断的降级方案）

    ## 算法：竞争式评分（2026-10-08 起）

        对每个专家收集命中的关键词 -> score = (
            该专家命中词数 (+ vision 的固定加成),
            命中词总字长,
            -并列裁决位次
        )
        取 score 最大者；一个都没命中则返回 knowledge。

    ## 为什么不是「首命中即胜 + 固定顺序」

    旧算法按 vision -> coder -> security -> ... -> pm 的固定顺序，谁先
    命中就返回。三级评测集（dev 217 / holdout 278 / test 284）暴露出它
    的三个结构性问题：

    1. coder 排第一，「写一个通知的公文」「SQL 注入」这类被 coder 抢走；
    2. pm 的原词表含「什么/怎么/如何/帮助/介绍/解释」6 个超泛化词，
       几乎命中任何问句；
    3. **knowledge 零关键词且不在遍历顺序里**，只能当兜底，结构上不可能
       主动胜出 —— 这使它成为两个开发集上错判率最高的专家。

    三项修复后（evals/results/route_variants.md，dev 池 495 条）：

    | 指标              | 改前   | 改后   |
    |-------------------|--------|--------|
    | dev(217)          | 79.7%  | 84.3%  |
    | holdout(278)      | 54.7%  | 73.4%  |
    | 两集合差（泛化缺口）| 25.0pp | 11.0pp |
    | 用户实际收到错答案  | 29.1%  | 12.7%  |
    | 短路到错误专家     | 130    | 46     |

    其中 holdout 上 +18.7pp 是主要收益：dev 集在多轮调优中已与关键词表
    高度耦合，其分数不可外推。

    ## 注意

    返回 knowledge **不等于**结论 —— route_expert_glm 会把 knowledge
    交给 L2 GLM 做语义判断；只有返回具体专家时才会短路。因此「判成
    knowledge」是安全降级，「短路到错专家」才是用户实际收到的错答案。
    两个集合上的错判明细见 evals/results/。
    """
    text = user_input.lower()
    best_score = None
    best_expert = None

    # 固定顺序遍历（vision 放最前，配合其大额加成），保证同分时结果确定。
    # vision 必须**参与评分**而不是事后单独判 —— 它的 +100 加成使其
    # 只要命中就几乎必胜，两种写法等价；拆出去单独判则会在"其他专家
    # 也命中"时改变结果，与评测时的行为不一致。
    for expert_key in ("vision",) + _ROUTE_ORDER + ("knowledge",):
        keywords = EXPERT_TEAM[expert_key].get("keywords") or []
        matched = []
        seen = set()
        for kw in keywords:
            # 去重：data 原词表里「数据分析」出现 3 次。首命中算法下
            # 重复无影响，按命中词数计分则会凭空多算，必须先去重。
            if kw in seen:
                continue
            seen.add(kw)
            if _kw_hit(kw, text, normalize=True):
                matched.append(kw)
        if not matched:
            continue
        bonus = _VISION_BONUS if expert_key == "vision" else 0
        score = (bonus + len(matched), sum(len(k) for k in matched),
                 -_PRIORITY_IDX.get(expert_key, 99))
        if best_score is None or score > best_score:
            best_score, best_expert = score, expert_key

    if best_expert is not None:
        return best_expert
    return "knowledge"


# GLM语义路由的缓存（避免重复判断）- 使用 LRU 防止内存泄漏
class LRUCache:
    """线程安全的 LRU 缓存"""
    def __init__(self, maxsize=256):
        self.cache = OrderedDict()
        self.maxsize = maxsize

    def get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def set(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.maxsize:
            self.cache.popitem(last=False)

    def __contains__(self, key):
        return key in self.cache

    def __len__(self):
        return len(self.cache)


_expert_route_cache = LRUCache(maxsize=256)


async def route_expert_glm(user_input: str) -> str:
    """混合路由：关键词快速匹配优先，匹配失败才走 GLM 语义路由

    优化策略（v1.1.3+）：
    1. 短消息（<10字）→ 纯关键词，0 延迟
    2. 关键词命中明确专家（非 knowledge）→ 直接返回，0 延迟
    3. 关键词返回 knowledge（兜底）→ 走 GLM 语义路由，1-2 秒延迟
    4. 缓存命中 → 直接返回，0 延迟

    这样大部分问题（代码/论文/中文写作等关键词明确的）零延迟路由，
    只有模糊问题才需要 GLM 语义判断。
    """
    # 短消息用关键词快速预判（省时间）
    if len(user_input) < 10:
        return route_expert(user_input)

    # 缓存命中（使用 MD5 摘要作为 key，避免长 JSON 链因前 200 字符相同而冲突）
    cache_key = hashlib.md5(user_input.encode("utf-8")).hexdigest()[:16]
    cached = _expert_route_cache.get(cache_key)
    if cached is not None:
        return cached

    # ── 混合优化：先跑关键词匹配 ──
    # 关键词命中明确专家（非 knowledge）→ 直接返回，跳过 GLM API 调用
    kw_result = route_expert(user_input)
    if kw_result != "knowledge":
        _expert_route_cache.set(cache_key, kw_result)
        return kw_result

    # ── 关键词未命中（返回 knowledge），走 GLM 语义路由 ──
    glm_cfg = MODEL_CONFIGS["glm-v"]  # 用多模态模型做路由（支持图片消息）
    try:
        client = _make_openai_client("glm-v")
        prompt = f"""判断以下用户问题属于哪个专家领域，只回复一个词：
- coder：编程开发、代码、函数、bug、技术实现、文件操作（查看/读取/修改/搜索文件、浏览目录）、项目仓库管理
- reasoner：数学推理、逻辑证明、算法分析、复杂计算
- academic：学术论文、公式推导、文献综述、研究方法、LaTeX、定理证明
- chinese：中文写作、文章、报告、文案、邮件
- vision：图片理解、截图分析、视觉（仅当用户明确提到图片/截图/图像时）
- pm：任务分析、计划制定、翻译、通用问答、解释说明
- knowledge：百科知识、事实查询、翻译、其他

注意：用户说"查看文件"/"看看代码"/"读取文件"时，应分类为 coder（编程专家，可调用 read_file 等工具），不是 vision。
只有用户明确提到"图片"/"截图"/"图像"/"png"/"jpg"等图片相关词时，才分类为 vision。

用户问题：{user_input[:300]}

只回复上面列出的一个词，不要回复其他任何内容。"""

        resp = await _interruptible_await(client.chat.completions.create(
            model=glm_cfg["model"],
            messages=[{"role": "system", "content": "你是 ZeroAI 路由分析器，只负责把用户问题分类到一个专家。严格只输出一个英文标识词，不要做解释。"},
                      {"role": "user", "content": prompt}],
            temperature=0.01,
            max_tokens=10,
            stream=False,
            timeout=15,
        ))
        if resp is None:
            # 被 Ctrl+C 中断
            return "knowledge"
        result = resp.choices[0].message.content.strip().lower()
        # 验证返回值
        valid_keys = {"coder", "reasoner", "academic", "chinese", "vision", "pm", "knowledge"}
        for vk in valid_keys:
            if vk in result:
                _expert_route_cache.set(cache_key, vk)
                return vk
        # 无效返回，降级到关键词
        _expert_route_cache.set(cache_key, "knowledge")
        return "knowledge"
    except Exception:
        # GLM判断失败，降级到关键词
        _expert_route_cache.set(cache_key, "knowledge")
        return "knowledge"


def get_expert_config(expert_key: str) -> dict:
    """获取专家对应的模型配置（base_url, api_key, model, model_key）"""
    expert = EXPERT_TEAM[expert_key]
    model_key = expert["model_key"]
    base_cfg = MODEL_CONFIGS[model_key]
    return {
        "base_url": base_cfg["base_url"],
        "api_key": base_cfg["api_key"],
        "model": expert["model"],
        "label": expert["label"],
        "model_key": model_key,  # v1.1.0 新增：供 _make_openai_client 识别
    }


# ====== OpenRouter 熔断器（连续失败自动降级，避免用户卡在"思考中…"） ======
_OPENROUTER_FAIL_COUNTS = {}  # {expert_key: 连续失败次数}
_OPENROUTER_CIRCUIT_THRESHOLD = 3  # 连续失败 3 次即熔断


def _is_openrouter_expert(expert_key: str) -> bool:
    """判断专家是否依赖 OpenRouter（需要熔断保护）"""
    try:
        return EXPERT_TEAM[expert_key].get("model_key") == "openrouter"
    except Exception:
        return False


def _check_openrouter_circuit_breaker(expert_key: str) -> bool:
    """检查专家是否已熔断（返回 True 表示应跳过该专家直接降级）"""
    if not _is_openrouter_expert(expert_key):
        return False
    return _OPENROUTER_FAIL_COUNTS.get(expert_key, 0) >= _OPENROUTER_CIRCUIT_THRESHOLD


def _record_openrouter_failure(expert_key: str) -> int:
    """记录一次 OpenRouter 专家失败，返回当前连续失败次数"""
    if not _is_openrouter_expert(expert_key):
        return 0
    cnt = _OPENROUTER_FAIL_COUNTS.get(expert_key, 0) + 1
    _OPENROUTER_FAIL_COUNTS[expert_key] = cnt
    return cnt


def _record_openrouter_success(expert_key: str) -> None:
    """记录一次成功，重置连续失败计数"""
    if _is_openrouter_expert(expert_key):
        _OPENROUTER_FAIL_COUNTS[expert_key] = 0
