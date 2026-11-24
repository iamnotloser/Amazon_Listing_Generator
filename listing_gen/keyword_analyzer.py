"""关键词分析：从 6 个 Listing 中抽取词根、归原形、加权排序，合并西柚反查词库。

不依赖 LLM，纯规则实现，结果可复现、可解释。
"""
import re
from collections import defaultdict

# 英文停用词（冠词/介词/连词/代词/助动词/常见榜单词）
STOPWORDS = set("""
a an the and or but for with without of to in on at by from into over under up down out
is are was were be been being do does did have has had will would can could should may might
this that these those it its their your our my his her them we you they i he she
as so than then there here when where which who whom what how why if while because
not no nor only just also very more most much many some any all each every both few
new best great perfect amazing top premium quality high low big small large
""".split())

# Amazon 特有的无意义词
AMZ_NOISE = set("""
visit store brand click here see more details about item customer reviews rating stars
free shipping prime include includes including package count number unit
""".split())

# 复数/时态归一（轻量规则，覆盖电商高频词）
IRREGULAR = {
    "caddies": "caddy", "brushes": "brush", "dishes": "dish", "knives": "knife",
    "shelves": "shelf", "feet": "foot", "teeth": "tooth",
}


def normalize_word(w: str) -> str:
    w = w.lower().strip("-")
    if w in IRREGULAR:
        return IRREGULAR[w]
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("es") and w[-3] in "sxzh":
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    if w.endswith("ing") and len(w) > 5:
        return w[:-3]
    if w.endswith("ed") and len(w) > 4:
        return w[:-2]
    return w


def extract_words(text: str) -> list:
    tokens = re.findall(r"[A-Za-z][A-Za-z\-']+", text or "")
    out = []
    for t in tokens:
        n = normalize_word(t)
        if len(n) < 2 or n in STOPWORDS or n in AMZ_NOISE or n.isdigit():
            continue
        out.append(n)
    return out


def analyze(listings: list, my_asin: str, top_n: int = 40) -> dict:
    """listings: [{asin, title, bullets, ...}]，返回词根统计与候选关键词池"""
    title_freq = defaultdict(set)   # 词 -> 出现在哪些 ASIN 的标题里
    bullet_freq = defaultdict(int)  # 词 -> 在五点里出现的总次数
    phrases_title = defaultdict(int)
    phrases_bullet = defaultdict(int)

    for d in listings:
        asin = d.get("asin")
        title = d.get("title") or ""
        tw = extract_words(title)
        for w in set(tw):
            title_freq[w].add(asin)

        # 2-3 词短语（标题）
        raw = [normalize_word(x) for x in re.findall(r"[A-Za-z][A-Za-z\-']+", title)]
        raw = [x for x in raw if x and x not in STOPWORDS and x not in AMZ_NOISE]
        for n in (2, 3):
            for i in range(len(raw) - n + 1):
                phrases_title[" ".join(raw[i:i + n])] += 1

        for b in (d.get("bullets") or []):
            bw = extract_words(b)
            for w in bw:
                bullet_freq[w] += 1
            braw = [normalize_word(x) for x in re.findall(r"[A-Za-z][A-Za-z\-']+", b)]
            braw = [x for x in braw if x and x not in STOPWORDS and x not in AMZ_NOISE]
            for n in (2, 3):
                for i in range(len(braw) - n + 1):
                    phrases_bullet[" ".join(braw[i:i + n])] += 1

    total = max(len(listings), 1)

    # 单词打分：标题覆盖 ASIN 数权重最高，五点为辅
    scored = []
    for w, asins in title_freq.items():
        t_cov = len(asins) / total
        b_cnt = bullet_freq.get(w, 0)
        score = t_cov * 10 + min(b_cnt, 20) * 0.4
        scored.append({
            "keyword": w,
            "in_titles": len(asins),
            "title_coverage": round(t_cov, 2),
            "bullet_mentions": b_cnt,
            "score": round(score, 2),
        })
    scored.sort(key=lambda x: -x["score"])

    # 短语：至少出现在 2 个标题里的才留
    phrases = []
    for p, c in phrases_title.items():
        if c >= 2:
            phrases.append({"phrase": p, "title_count": c, "bullet_count": phrases_bullet.get(p, 0),
                            "score": round(c * 10 + phrases_bullet.get(p, 0) * 0.5, 2)})
    phrases.sort(key=lambda x: -x["score"])

    return {
        "asin_count": len(listings),
        "my_asin": my_asin,
        "top_words": scored[:top_n],
        "top_phrases": phrases[:25],
    }


def merge_with_xiyou(word_analysis: dict, xiyou_rows: list, top_n: int = 30,
                     exclude_tokens=None, phrase_metrics=None) -> list:
    """把西柚反查词库与竞品分析合并。

    排序：有搜索量/流量的优先，其次是竞品标题短语，单词词根最低。
    phrase_metrics: {phrase_lower: {search_volume, traffic, ...}}，用于给竞品短语补指标。
    exclude_tokens 用于剔除品牌名（如竞品品牌 cisily）。
    """
    exclude = {t.lower() for t in (exclude_tokens or []) if t}
    pm = phrase_metrics or {}

    def blocked(kw: str) -> bool:
        toks = set(re.findall(r"[a-z]{3,}", kw.lower()))
        return bool(toks & exclude)

    merged = {}
    for r in xiyou_rows:
        kw = (r.get("keyword") or "").strip()
        if not kw or blocked(kw):
            continue
        merged[kw.lower()] = {
            "keyword": kw,
            "keyword_zh": r.get("keyword_zh"),
            "search_volume": r.get("search_volume"),
            "aba_rank": r.get("aba_rank"),
            "traffic": r.get("traffic"),
            "organic_rank": r.get("organic_rank"),
            "ad_rank": r.get("ad_rank"),
            "cpc": r.get("cpc"),
            "competitive_difficulty": r.get("competitive_difficulty"),
            "source": "西柚反查",
            "competitor_score": 0.0,
            "in_titles": 0,
        }

    # 竞品标题短语：真实搜索词形态，优先于单词词根
    for p in word_analysis.get("top_phrases", []):
        kw = p["phrase"]
        if kw.lower() in merged or blocked(kw):
            continue
        m = pm.get(kw.lower(), {})
        merged[kw.lower()] = {
            "keyword": kw, "keyword_zh": m.get("keyword_zh"),
            "search_volume": m.get("search_volume"), "aba_rank": m.get("aba_rank"),
            "traffic": m.get("traffic"), "organic_rank": None,
            "ad_rank": None, "cpc": m.get("cpc"),
            "competitive_difficulty": m.get("competitive_difficulty"),
            "source": "竞品短语",
            "competitor_score": p["score"],
            "in_titles": p["title_count"],
            "bullet_mentions": p["bullet_count"],
        }

    for w in word_analysis.get("top_words", []):
        kw = w["keyword"]
        if kw in merged or blocked(kw):
            continue
        merged[kw] = {
            "keyword": kw, "keyword_zh": None, "search_volume": None,
            "aba_rank": None, "traffic": None, "organic_rank": None,
            "ad_rank": None, "cpc": None, "competitive_difficulty": None,
            "source": "竞品词根",
            "competitor_score": w["score"],
            "in_titles": w["in_titles"],
        }

    def rank_score(x):
        sv = x.get("search_volume") or 0
        traffic = x.get("traffic") or 0
        base = sv * 0.002 + traffic * 0.0004
        if x["source"] == "竞品短语":
            if sv:
                base += (x.get("in_titles") or 0) * 0.1
            else:
                # 拿不到指标时才用竞品覆盖度兜底，保证短语排在单词前面
                base += (x.get("in_titles") or 0) * 4 + (x.get("bullet_mentions") or 0) * 0.4
        elif x["source"] == "竞品词根":
            base += (x.get("competitor_score") or 0) * 0.1
        # 单词泛词降权，避免 sink / kitchen 这类词挤掉真实词组
        if " " not in x["keyword"]:
            base *= 0.5
        return base

    out = sorted(merged.values(), key=rank_score, reverse=True)
    return out[:top_n]
