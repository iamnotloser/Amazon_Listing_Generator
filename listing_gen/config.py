"""站点配置

字符规则依据 Amazon Seller Central《商品名称要求和指南》：
- 商品名称（Item Name）≤ 75 字符（含空格）
- 商品亮点（Product Highlights）额外 125 字符，逗号分隔短语
- 同一字词最多出现 2 次（介词/冠词/连词除外，品牌名同样受限）
"""

SITES = {
    "US": {
        "domain": "https://www.amazon.com",
        "locale": "en-US",
        "language": "English (US)",
        "spelling": "美式拼写，如 color / center / specialize",
        "title_max": 75,        # 商品名称
        "highlight_max": 125,   # 商品亮点
        "bullet_count": 5,
        "currency": "USD",
        "timezone": "America/New_York",
    },
    "UK": {
        "domain": "https://www.amazon.co.uk",
        "locale": "en-GB",
        "language": "English (UK)",
        "spelling": "英式拼写，如 colour / centre / specialise / metre",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "GBP",
        "timezone": "Europe/London",
    },
    # ---------- 美洲 ----------
    "CA": {
        "domain": "https://www.amazon.ca",
        "locale": "en-CA",
        "language": "English (Canada)",
        "spelling": "加拿大英语，拼写接近美式，度量可同时标注 inch 与 cm",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "CAD",
        "timezone": "America/Toronto",
    },
    "MX": {
        "domain": "https://www.amazon.com.mx",
        "locale": "es-MX",
        "language": "Español (México)",
        "spelling": "墨西哥西班牙语拼写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "MXN",
        "timezone": "America/Mexico_City",
    },
    "BR": {
        "domain": "https://www.amazon.com.br",
        "locale": "pt-BR",
        "language": "Português (Brasil)",
        "spelling": "巴西葡萄牙语拼写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "BRL",
        "timezone": "America/Sao_Paulo",
    },
    # ---------- 欧洲 ----------
    "DE": {
        "domain": "https://www.amazon.de",
        "locale": "de-DE",
        "language": "Deutsch",
        "spelling": "德语，名词首字母大写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "EUR",
        "timezone": "Europe/Berlin",
    },
    "FR": {
        "domain": "https://www.amazon.fr",
        "locale": "fr-FR",
        "language": "Français",
        "spelling": "法语拼写，注意重音符号",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "EUR",
        "timezone": "Europe/Paris",
    },
    "IT": {
        "domain": "https://www.amazon.it",
        "locale": "it-IT",
        "language": "Italiano",
        "spelling": "意大利语拼写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "EUR",
        "timezone": "Europe/Rome",
    },
    "ES": {
        "domain": "https://www.amazon.es",
        "locale": "es-ES",
        "language": "Español (España)",
        "spelling": "西班牙西班牙语拼写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "EUR",
        "timezone": "Europe/Madrid",
    },
    "NL": {
        "domain": "https://www.amazon.nl",
        "locale": "nl-NL",
        "language": "Nederlands",
        "spelling": "荷兰语拼写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "EUR",
        "timezone": "Europe/Amsterdam",
    },
    "SE": {
        "domain": "https://www.amazon.se",
        "locale": "sv-SE",
        "language": "Svenska",
        "spelling": "瑞典语拼写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "SEK",
        "timezone": "Europe/Stockholm",
    },
    "PL": {
        "domain": "https://www.amazon.pl",
        "locale": "pl-PL",
        "language": "Polski",
        "spelling": "波兰语拼写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "PLN",
        "timezone": "Europe/Warsaw",
    },
    "BE": {
        "domain": "https://www.amazon.com.be",
        "locale": "nl-BE",
        "language": "Nederlands (België)",
        "spelling": "比利时荷兰语拼写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "EUR",
        "timezone": "Europe/Brussels",
    },
    "TR": {
        "domain": "https://www.amazon.com.tr",
        "locale": "tr-TR",
        "language": "Türkçe",
        "spelling": "土耳其语拼写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "TRY",
        "timezone": "Europe/Istanbul",
    },
    # ---------- 亚太 ----------
    "JP": {
        "domain": "https://www.amazon.co.jp",
        "locale": "ja-JP",
        "language": "日本語",
        "spelling": "日语，使用です・ます敬体",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "JPY",
        "timezone": "Asia/Tokyo",
    },
    "AU": {
        "domain": "https://www.amazon.com.au",
        "locale": "en-AU",
        "language": "English (Australia)",
        "spelling": "澳式拼写，如 colour / centre",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "AUD",
        "timezone": "Australia/Sydney",
    },
    "IN": {
        "domain": "https://www.amazon.in",
        "locale": "en-IN",
        "language": "English (India)",
        "spelling": "印度英语，拼写接近英式",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "INR",
        "timezone": "Asia/Kolkata",
    },
    "SG": {
        "domain": "https://www.amazon.sg",
        "locale": "en-SG",
        "language": "English (Singapore)",
        "spelling": "新加坡英语，拼写接近英式",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "SGD",
        "timezone": "Asia/Singapore",
    },
    # ---------- 中东 ----------
    "AE": {
        "domain": "https://www.amazon.ae",
        "locale": "en-AE",
        "language": "English (UAE)",
        "spelling": "英式拼写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "AED",
        "timezone": "Asia/Dubai",
    },
    "SA": {
        "domain": "https://www.amazon.sa",
        "locale": "ar-SA",
        "language": "العربية",
        "spelling": "阿拉伯语，从右到左书写",
        "title_max": 75,
        "highlight_max": 125,
        "bullet_count": 5,
        "currency": "SAR",
        "timezone": "Asia/Riyadh",
    },
}

DEFAULT_SITE = "US"

# 各站点本地化表达要求：不仅翻译语言，还要符合当地用词与单位习惯
STYLE_NOTES = {
    "en-GB": "英式英语：-ise/-our 拼写（organise / colour / centre）；厨房场景用 tap 而非 faucet，"
             "可用 washing-up、hob、kitchen roll；单位优先 cm / ml；语气克制，避免 awesome / super 等美式夸张词",
    "en-US": "美式英语：organize / color / center；单位 inch / oz；避免绝对化宣称",
    "en-CA": "加拿大英语：拼写接近美式，可并列标注 inch 与 cm",
    "en-AU": "澳式英语：organise / colour；单位 cm；本地用词如 tap、bench",
    "en-IN": "印度英语：拼写偏英式，表达正式克制",
    "en-SG": "新加坡英语：拼写偏英式，表达简洁直接",
    "de-DE": "德语：名词首字母大写，复合名词连写；使用 Sie 敬语；单位 cm / ml；避免英文词混用",
    "fr-FR": "法语：注意重音符号与性数配合；单位 cm / ml；避免英文直译语序",
    "it-IT": "意大利语：注意阴阳性与冠词搭配；单位 cm / ml",
    "es-ES": "西班牙西班牙语：使用当地常用词；单位 cm / ml",
    "es-MX": "拉美西班牙语：避免西班牙本土用词；单位 cm / ml",
    "pt-BR": "巴西葡萄牙语：使用 você 等本地表达；单位 cm / ml",
    "nl-NL": "荷兰语：复合词连写；单位 cm / ml",
    "sv-SE": "瑞典语：复合词连写；单位 cm / ml",
    "pl-PL": "波兰语：注意格变化与词尾；单位 cm / ml",
    "tr-TR": "土耳其语：注意元音和谐与后缀；单位 cm / ml",
    "ja-JP": "日语：使用です・ます体；外来语按日本常用片假名写法；单位 cm / ml",
    "ar-SA": "阿拉伯语：从右到左书写，使用现代标准阿拉伯语；单位 cm / ml",
    "ar-AE": "阿拉伯语：从右到左书写，使用现代标准阿拉伯语；单位 cm / ml",
}


def get_site(code: str = DEFAULT_SITE) -> dict:
    if code not in SITES:
        raise ValueError(f"不支持的站点: {code}，可选 {list(SITES)}")
    site = dict(SITES[code])
    site["style_notes"] = STYLE_NOTES.get(
        site["locale"], f"使用{site['language']}母语表达，避免英文直译语序；单位按当地习惯")
    return site


# ASIN 站点推断：按主流销售站点顺序探测，第一个抓到 title 的即是源站点
ASIN_PROBE_ORDER = ["US", "UK", "CA", "DE", "FR", "IT", "ES", "JP", "AU", "MX"]


def guess_asin_site(asin: str) -> str | None:
    """尝试在主流站点抓一次 ASIN，返回第一个成功的站点 code。

    失败时返回 None（调用方应该退回到手动/目标站点模式）。
    """
    from listing_gen.fetcher import ListingFetcher
    for code in ASIN_PROBE_ORDER:
        try:
            site = SITES[code]
            with ListingFetcher(site["domain"], site["locale"], site["timezone"],
                                site.get("currency", "USD"), headless=True) as f:
                d = f.fetch(asin)
                if d and d.get("title"):
                    return code
        except Exception:
            continue
    return None

# 官方明确禁用的特殊字符
TITLE_FORBIDDEN_SYMBOLS = ["!", "$", "?", "_", "{", "}", "^", "¬", "¦"]

# 允许的必要标点（连字符、斜杠、逗号、&、句点）
TITLE_ALLOWED_PUNCT = ["-", "/", ",", "&", "."]

# 促销/主观评价用语
TITLE_FORBIDDEN_PHRASES = [
    "free shipping", "100% quality guaranteed", "best seller", "hot item",
    "sale", "cheap", "discount", "amazing", "perfect", "top rated",
    "great deal", "limited time",
]

# 介词/冠词/连词：可重复出现，且不计入重复次数
FUNCTION_WORDS = set("""
a an the and or for with in on over of to by from at as
""".split())


# ========== 商品要点（Bullet Points）规则 ==========
# 依据 Amazon Seller Central《商品要点要求》
BULLET_MIN_COUNT = 3          # 至少 3 条
BULLET_FORBIDDEN_SYMBOLS = ["™", "®", "€", "…", "†", "‡", "¢", "£", "¥", "©", "±", "~"]

BULLET_FORBIDDEN_CLAIMS = [
    "eco-friendly", "environmentally friendly", "environment friendly",
    "antimicrobial", "antibacterial", "bamboo", "made from soy", "soy-based",
    "full refund", "money back", "lifetime warranty", "unconditional guarantee",
]

BULLET_PLACEHOLDERS = ["n/a", "na", "tbd", "not applicable", "to be determined",
                       "待定", "不适用", "文案待定"]

# 测量单位（这些单位前的阿拉伯数字是允许的）
MEASURE_UNITS = {"in", "inch", "inches", "ft", "cm", "mm", "m", "oz", "lb", "lbs",
                 "g", "kg", "ml", "l", "pcs", "count", "pack", "w", "v", "db"}
