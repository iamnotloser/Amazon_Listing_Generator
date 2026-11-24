"""商品名称与商品亮点的拼装、校验。

规则依据 Amazon Seller Central《商品名称要求和指南》：
- 商品名称 ≤ 75 字符，信息顺序：品牌 → 款式/风味 → 商品类型 → 关键属性 → 颜色 → 尺寸/包装数量 → 型号
- 商品亮点 ≤ 125 字符，逗号分隔短语，不用完整句子
- 同一字词最多出现 2 次（介词/冠词/连词除外，品牌名同样受限）
- 禁用特殊字符 ! $ ? _ { } ^ ¬ ¦
- 禁止促销语、主观评价用语、全大写
- 使用阿拉伯数字，不用英文数字单词
"""
import re
from collections import Counter

from listing_gen.config import (BULLET_FORBIDDEN_CLAIMS,
                                BULLET_FORBIDDEN_SYMBOLS, BULLET_MIN_COUNT,
                                BULLET_PLACEHOLDERS, FUNCTION_WORDS,
                                MEASURE_UNITS, TITLE_FORBIDDEN_PHRASES,
                                TITLE_FORBIDDEN_SYMBOLS)

EN_NUMBERS = {"one", "two", "three", "four", "five", "six", "seven", "eight",
              "nine", "ten", "eleven", "twelve", "twenty", "thirty", "fifty", "hundred"}


def _join(parts):
    return re.sub(r"\s+", " ", " ".join(p.strip() for p in parts if p and p.strip())).strip()


def _fit(text: str, max_len: int) -> str:
    """截断到 max_len，不切断单词，去掉残留标点"""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;-/&.")


def build_product_name(f: dict, max_len: int = 75) -> str:
    """按官方信息顺序拼装商品名称，超长时从尾部依次舍弃低优先级字段"""
    # 优先级从高到低（官方顺序）
    ordered = [
        f.get("brand"),
        f.get("style"),
        f.get("product_type"),
        f.get("attribute_1"),
        f.get("attribute_2"),
        f.get("color"),
        f.get("size"),
        f.get("model"),
    ]
    name = _join(ordered)
    # 超长时按优先级从后往前砍
    while len(name) > max_len and len([p for p in ordered if p]) > 2:
        for i in range(len(ordered) - 1, -1, -1):
            if ordered[i]:
                ordered[i] = None
                break
        name = _join(ordered)
    return _fit(name, max_len)


def build_highlights(f: dict, max_len: int = 125) -> str:
    """商品亮点：逗号分隔短语，不是完整句子"""
    phrases = [p.strip().rstrip(".") for p in (f.get("highlights") or []) if p and p.strip()]
    # 去重（不区分大小写）
    seen, uniq = set(), []
    for p in phrases:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    text = ", ".join(uniq)
    if len(text) <= max_len:
        return text
    # 超长则逐个短语从尾部舍弃
    while uniq and len(", ".join(uniq)) > max_len:
        uniq.pop()
    return _fit(", ".join(uniq), max_len)


def word_repeats(text: str) -> dict:
    """返回出现超过 2 次的词 -> 次数。介词/冠词/连词不计。"""
    words = [w for w in re.findall(r"[A-Za-z0-9'\-]+", (text or "").lower())]
    counter = Counter(w for w in words if w not in FUNCTION_WORDS)
    return {w: c for w, c in counter.items() if c > 2}


def check_product_name(name: str, brand: str = "") -> list:
    """返回问题列表，空列表代表通过"""
    issues = []
    if not name:
        return ["商品名称为空"]
    if len(name) > 75:
        issues.append(f"超过 75 字符（当前 {len(name)}）")

    low = (name or "").lower()
    for p in TITLE_FORBIDDEN_PHRASES:
        if p in low:
            issues.append(f"含促销/主观评价用语: {p}")

    for s in TITLE_FORBIDDEN_SYMBOLS:
        if s in name:
            issues.append(f"含禁用特殊字符: {s}")

    brand_up = (brand or "").upper()
    for word in re.findall(r"[A-Za-z]{4,}", name):
        if word.isupper() and word != brand_up:
            issues.append(f"全大写单词: {word}")

    for w, c in word_repeats(name).items():
        issues.append(f"字词重复超限: '{w}' 出现 {c} 次（最多 2 次）")

    for w in re.findall(r"[A-Za-z]+", low):
        if w in EN_NUMBERS:
            issues.append(f"建议用阿拉伯数字替代英文数字: {w}")

    if "  " in name:
        issues.append("存在连续空格")
    if re.search(r"<[^>]+>", name):
        issues.append("含 HTML 标签")

    return issues


def check_highlights(text: str) -> list:
    issues = []
    if not text:
        return ["商品亮点为空"]
    if len(text) > 125:
        issues.append(f"超过 125 字符（当前 {len(text)}）")
    if text.count(",") == 0 and " " in text:
        issues.append("应为逗号分隔的短语，而不是整句")
    if text.rstrip().endswith("."):
        issues.append("亮点不应是完整句子（结尾不要句号）")
    if re.search(r"[A-Z][a-z]+ [a-z]+ed ", text):
        issues.append("疑似完整句子，请改成短语")
    for s in TITLE_FORBIDDEN_SYMBOLS:
        if s in text:
            issues.append(f"含禁用特殊字符: {s}")
    return issues


def _title_case(text: str) -> str:
    """每个实词首字母大写，介词/连词/冠词小写（不在全大写，符合官方要点要求）"""
    words = text.split()
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if i > 0 and low in FUNCTION_WORDS:
            out.append(low)
        else:
            out.append(low[:1].upper() + low[1:])
    return " ".join(out)


def format_bullet(b: dict) -> str:
    """格式：标题 + 冒号 + 描述；首字母大写；结尾不加标点"""
    benefit = b["benefit"].strip().rstrip(":")
    if benefit.isupper():
        benefit = _title_case(benefit)
    feature = b["feature"].strip().rstrip(".:!?。！")
    if feature:
        feature = feature[0].upper() + feature[1:]
    return f"{benefit}: {feature}"


def check_bullets(bullets: list, max_words=40) -> list:
    """按官方《商品要点要求》校验"""
    issues = []
    if len(bullets) < BULLET_MIN_COUNT:
        issues.append(f"要点少于 {BULLET_MIN_COUNT} 条（当前 {len(bullets)}）")

    seen = []
    for i, b in enumerate(bullets, 1):
        benefit = b["benefit"].strip().rstrip(":")
        feature = b["feature"].strip()
        full = f"{benefit}: {feature}"
        low = full.lower()

        # 结尾不应有标点
        if feature.endswith((".", "!", "?", "。", "！")):
            issues.append(f"第{i}条结尾不应有标点")

        # 不应全大写
        if benefit.isupper() and len(benefit) > 3:
            issues.append(f"第{i}条利益点不应全大写")

        # 特殊字符 / 表情
        for s in BULLET_FORBIDDEN_SYMBOLS:
            if s in full:
                issues.append(f"第{i}条含禁用符号: {s}")
        if any(ord(ch) > 0x2190 and not ch.isalnum() for ch in full):
            issues.append(f"第{i}条含表情或非语言字符")

        # 占位符（英文用单词边界，避免误伤 drainage 之类）
        for p in BULLET_PLACEHOLDERS:
            if p.isascii():
                if re.search(rf"\b{re.escape(p)}\b", low):
                    issues.append(f"第{i}条含占位符文本: {p}")
            elif p in full:
                issues.append(f"第{i}条含占位符文本: {p}")

        # ASIN
        if re.search(r"\bB0[A-Z0-9]{8}\b", full):
            issues.append(f"第{i}条含 ASIN")

        # 禁用声明/保证类
        for c in BULLET_FORBIDDEN_CLAIMS:
            if c in low:
                issues.append(f"第{i}条含禁用声明: {c}")

        # 外部链接/联系方式
        if re.search(r"https?://|www\.|\S+@\S+\.\S+", full):
            issues.append(f"第{i}条含外部链接或联系方式")

        # 数字 1~9 应用文字（测量值除外）：先剔除尺寸/带单位数值再检测
        cleaned = re.sub(
            r'\d+(?:\.\d+)?\s*"?\s*(?:in|inch|inches|ft|cm|mm|m|oz|lb|lbs|g|kg|ml|l|pcs|count|pack|w|v|db)\b',
            " ", feature, flags=re.I)
        cleaned = re.sub(r'\d+(?:\.\d+)?"', " ", cleaned)   # 形如 9.65"
        cleaned = re.sub(r"\d+(?:\.\d+)+", " ", cleaned)    # 小数整体
        for m in re.finditer(r"\b([1-9])\b", cleaned):
            issues.append(f"第{i}条数字 {m.group(1)} 建议改成英文单词（测量值除外）")
            break

        # 重复内容
        key = re.sub(r"\W+", "", feature.lower())[:40]
        if key and any(key == s for s in seen):
            issues.append(f"第{i}条与其他要点内容重复")
        seen.append(key)

        # 词数
        wc = len(re.findall(r"[A-Za-z']+", feature))
        if wc > max_words:
            issues.append(f"第{i}条描述过长（{wc} 词）")

    return issues
