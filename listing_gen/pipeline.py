"""可复用的 Listing 生成流程：CLI 与 Web 共用。

输入既可以是 ASIN（抓 Amazon），也可以是手动填写的产品信息（未上架产品）。
"""
import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from listing_gen import chains, reviewer, xiyou
from listing_gen.config import ASIN_PROBE_ORDER, get_site
from listing_gen.fetcher import ListingFetcher
from listing_gen.keyword_analyzer import (analyze, keyword_coverage,
                                          merge_with_xiyou, split_keywords)
from listing_gen.title_builder import (check_bullets, check_highlights,
                                       check_product_name, format_bullet)

DATA_DIR = Path(__file__).parent / "data"
OUT_DIR = Path(__file__).parent.parent / "output"


def _noop(msg):
    pass


def manual_listing(manual: dict, brand: str) -> dict:
    """把表单里的手动产品信息整理成与抓取结果同构的 dict。"""
    manual = manual or {}
    specs = {k: v for k, v in (manual.get("specs") or {}).items() if v}
    bullets = [b.strip() for b in (manual.get("bullets") or []) if b and b.strip()]
    crumbs = [x.strip() for x in re.split(r"[>›/,，]", manual.get("breadcrumbs") or "") if x.strip()]
    return {
        "asin": manual.get("asin") or "",
        "title": manual.get("title") or "",
        "byline": manual.get("brand") or brand,
        "price": manual.get("price") or "",
        "rating": None,
        "review_count": None,
        "bullets": bullets,
        "breadcrumbs": crumbs,
        "specs": specs,
    }


def build_product_facts(mine: dict, extra: str, drop_specs=None) -> str:
    specs = mine.get("specs") or {}
    if isinstance(drop_specs, str):
        drop_specs = drop_specs.split(",")
    drop = {s.strip().lower() for s in (drop_specs or []) if s.strip()}
    keep = ["Brand", "Color", "Product Dimensions", "Item Weight", "Material",
            "Number of Compartments", "Shape", "Special Feature", "Style", "Size"]
    lines = [f"类目：{' > '.join(mine.get('breadcrumbs') or [])}",
             f"品牌：{(mine.get('byline') or '').replace('Visit the ', '').replace(' Store', '')}",
             f"当前售价：{mine.get('price')}",
             f"现有标题：{mine.get('title')}"]
    bullets = [b.strip() for b in (mine.get("bullets") or []) if b and b.strip()]
    if bullets:
        lines.append("产品卖点（五点必须逐条覆盖，不得改写成其他品类）：")
        lines.extend(f"- {b}" for b in bullets[:6])
    for k in keep:
        if k in specs and k.lower() not in drop:
            lines.append(f"{k}：{specs[k]}")
    for k, v in specs.items():
        if k not in keep and k.lower() not in drop and len(lines) < 18:
            lines.append(f"{k}：{v}")
    if extra:
        lines.append(f"补充说明：{extra}")
    return "\n".join(lines)


def summarize_bullet_style(competitors: list) -> str:
    """统计竞品五点篇幅，给五点生成提供长度基准（不传原文）。"""
    lens, words, per_asin = [], [], []
    for d in competitors:
        bs = [b for b in (d.get("bullets") or []) if len(b) >= 40]
        if not bs:
            continue
        cur = [len(b) for b in bs]
        lens.extend(cur)
        words.extend(len(b.split()) for b in bs)
        per_asin.append(round(sum(cur) / len(cur)))
    if not lens:
        return ""
    avg = sum(lens) / len(lens)
    avg_w = sum(words) / len(words) if words else 0
    return (f"{len(per_asin)} 个竞品共 {len(lens)} 条，平均每条 {round(avg)} 字符 / {round(avg_w)} 词，"
            f"单条区间 {min(lens)}-{max(lens)} 字符；各 ASIN 平均：{'、'.join(map(str, per_asin))} 字符。"
            f"建议每条 {round(avg * 0.9)}-{round(avg * 1.1)} 字符，约 {max(round(avg_w * 0.9), 1)}-{round(avg_w * 1.1)} 词。")


def extract_feature_points(mine: dict, source_mine: dict | None = None,
                           extra: str = "") -> list[str]:
    """从产品事实里抽出独立卖点列表，用来决定五点条数。

    优先级：源站点原生五点（跨站场景）→ 手动 bullets → 规格里的差异化字段。
    返回的每条卖点都是一个独立的产品特征/利益点，不合并也不拆分。
    """
    points: list[str] = []

    # 1) 源站点原生五点（跨站改写的主要依据）
    if source_mine and source_mine.get("bullets"):
        points.extend(b.strip() for b in source_mine["bullets"] if b and len(b.strip()) >= 20)

    # 2) 手动输入的 bullets 或目标站点抓到的 bullets（去重）
    existing_lower = {p.lower() for p in points}
    for b in mine.get("bullets") or []:
        b = b.strip()
        if not b or len(b) < 20:
            continue
        if b.lower() not in existing_lower:
            points.append(b)
            existing_lower.add(b.lower())

    # 3) specs 里可能含独立卖点的字段
    specs = mine.get("specs") or {}
    useful_keys = {"special feature", "specialfeature", "material", "shape", "style",
                   "product dimensions", "dimensions", "color", "finish",
                   "number of compartments", "item weight", "size"}
    for k, v in specs.items():
        if not v:
            continue
        if k.strip().lower() in useful_keys:
            s = f"{k.strip()}: {v.strip()}"
            if s.lower() not in existing_lower and len(s) >= 10:
                points.append(s)
                existing_lower.add(s.lower())

    # 4) 补充说明里如果分条了也补上
    for line in (extra or "").split("\n"):
        line = line.strip().lstrip("-•*")
        if not line or len(line) < 20:
            continue
        if line.lower() not in existing_lower:
            points.append(line)
            existing_lower.add(line.lower())

    return points


def jaccard(a: str, b: str) -> float:
    """两个字符串的词集合 Jaccard 相似度，0=完全不同，1=完全相同。"""
    toks = lambda s: set(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿäöüßñçéèêëàâäùûüôîï]{2,}", s.lower()))
    sa, sb = toks(a), toks(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def dedupe_bullets(site, bulletset, facts, lo, hi, progress, threshold: float = 0.38) -> dict:
    """检查五点之间的语义重复（Jaccard ≥ threshold），把重复的条改写。"""
    bullets = bulletset["bullets"]
    formatted = list(bulletset["formatted"])
    rewritten = 0
    for i in range(len(bullets)):
        for j in range(i + 1, len(bullets)):
            sim = jaccard(formatted[i], formatted[j])
            if sim >= threshold:
                progress(f"检测到第 {i+1} 条与第 {j+1} 条相似（{sim:.2f}），重写第 {j+1} 条")
                try:
                    new = chains.rewrite_bullet_feature(
                        site, bullets[j].get("benefit", ""),
                        bullets[j].get("feature", ""),
                        lo, hi, facts,
                        feedback=(f"与第 {i+1} 条语义重复（相似度 {sim:.2f}），"
                                  "请换一个完全不同的卖点/角度，确保两条讲的是不同的产品特征"))
                    if new["feature"]:
                        bullets[j] = dict(bullets[j], feature=new["feature"], zh=new.get("zh", bullets[j].get("zh", "")))
                        formatted[j] = format_bullet(bullets[j])
                        rewritten += 1
                except Exception as e:
                    progress(f"  重写失败：{e}")
    bulletset["formatted"] = formatted
    if rewritten:
        progress(f"去重重写 {rewritten} 条")
    return bulletset


def pick_diverse(passed, limit=10):
    """去重同父体变体（同类目同排名视为同一链接），并把排名拉开。

    passed 已按排名升序。先按 (叶子类目, 排名) 去重，
    再在剩余结果里等距抽样，避免 5 个竞品全是 #1。
    """
    uniq, seen = [], set()
    for x in passed:
        key = (x.get("leaf_id"), x.get("matched_rank"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(x)
    if len(uniq) <= limit:
        return uniq
    idx = sorted({round(i * (len(uniq) - 1) / (limit - 1)) for i in range(limit)})
    return [uniq[i] for i in idx]


def _is_variant(d, chosen):
    """同品牌 + 标题词重合度 >= 0.8，视为同一父体的子变体。"""
    def toks(s):
        return set(re.findall(r"[a-z0-9]+", (s or "").lower()))
    brand = (d.get("byline") or "").strip().lower()
    t = toks(d.get("title"))
    if not brand or not t:
        return False
    for c in chosen:
        if brand != (c.get("byline") or "").strip().lower():
            continue
        u = toks(c.get("title"))
        if u and len(t & u) / len(t | u) >= 0.8:
            return True
    return False


def _auto_select(fetcher, mine, my_asin, params, progress):
    """关键词搜索结果 → 同品类过滤 → BSR 校验 → 前 5 个竞品。

    关键词常有歧义（如德语 abtropfgestell geschirr 搜出餐具沥水架），
    先用目标商品信息做一次同品类语义过滤，避免选到其他产品。
    有 ASIN 时用它的叶子类目作为类目约束；
    手动模式没有 ASIN 时，按候选自身的叶子类目聚类，取成员最多的类目。
    """
    pages = int(params.get("search_pages") or 5)
    keywords = [k.strip() for k in (params.get("search_keywords")
                                    or params.get("seed_keywords")
                                    or [params.get("search_keyword") or ""]) if k]
    if not keywords:
        raise RuntimeError("自动选品需要至少一个搜索关键词（或核心关键词）")
    bsr_max = int(params.get("bsr_max") or 100)
    per_pages = max(1, pages // max(len(keywords), 1))

    cand, seen = [], {my_asin} if my_asin else set()
    for kw in keywords:
        progress(f"搜索 {per_pages} 页：{kw}")
        for c in fetcher.search_competitors(kw, limit=per_pages * 20, pages=per_pages):
            if c["asin"] not in seen:
                seen.add(c["asin"])
                cand.append(c)
        if len(cand) >= 80:
            break
    cand = cand[:80]
    if not cand:
        raise RuntimeError(f"关键词搜索没有拿到候选 ASIN（{keywords}），"
                           "可能是搜索页被拦截或该站点无结果，请检查网络后重试")
    progress(f"候选 {len(cand)} 个，先按产品类型过滤")

    # 同品类过滤：只用标题判断，不消耗西柚额度
    product_ref = build_product_facts(mine, params.get("extra") or "")
    product_ref += "\n搜索关键词：" + "、".join(keywords)
    try:
        keep, dropped = chains.filter_competitors(product_ref, cand)
        if keep:
            keep_n = len([c for c in cand if c["asin"] in keep])
            progress(f"同品类保留 {keep_n} 个，排除 {len(cand) - keep_n} 个")
            for asin, reason in dropped[:5]:
                progress(f"  ✗ {asin} {reason}")
            cand = [c for c in cand if c["asin"] in keep]
        else:
            progress("同品类过滤无结果，沿用关键词搜索原始候选")
    except Exception as e:
        progress(f"同品类过滤失败：{e}（沿用原始候选）")
    if not cand:
        raise RuntimeError("关键词搜索到的商品都与目标产品不同类，"
                           "请换更精准的关键词（如带 sink caddy / Spülen-Organizer 这类产品词）")
    cand = [c["asin"] for c in cand]
    progress(f"候选 {len(cand)} 个，开始 BSR 校验（≤{bsr_max}）")

    target_ids = None
    if my_asin:
        try:
            my_bsr = xiyou.fetch_asin_bsr(my_asin, params.get("site") or "US")
            leaf = my_bsr.get("leaf_id")
            target_ids = {leaf} if leaf else set(my_bsr.get("tree_ids") or [])
            progress(f"我的小类：{my_bsr.get('bsr_category')}（BSR #{my_bsr.get('bsr_rank')}）")
        except Exception as e:
            progress(f"获取我的小类失败：{e}（改为按候选类目聚类）")
    else:
        progress("无 ASIN，按候选叶子类目聚类确定目标类目")

    def _p(i, asin, rank, status):
        if status == "checked":
            progress(f"· {i}/{len(cand)} {asin} 类目排名 {rank}")
        elif status == "cluster":
            progress(f"主类目：{asin}，命中 {rank} 个候选")
        elif status == "error":
            progress(f"✗ {i}/{len(cand)} {asin} 查询失败")

    passed, rejected, errors = xiyou.filter_by_bsr(
        cand, params.get("site") or "US", bsr_max, category_ids=target_ids, on_progress=_p)
    progress(f"通过 {len(passed)} / 淘汰 {len(rejected)} / 失败 {len(errors)}")
    picked = pick_diverse(passed, limit=7)
    if picked:
        progress("竞品候选（已去重、排名拉开）：" +
                 "、".join(f"{x['asin']}(#{x['matched_rank']})" for x in picked))
    asins = [x["asin"] for x in picked]
    if not asins:
        asins = cand[:10]
        progress("无达标商品，退回搜索结果前 10")
    return asins


def bullet_target_range(competitors: list, fallback=(180, 250)):
    """按竞品五点平均长度给出目标字符区间（上限 250，兼顾 32 词以内的规则）。"""
    lens = [len(b) for d in competitors for b in (d.get("bullets") or []) if len(b) >= 40]
    if not lens:
        return fallback
    avg = sum(lens) / len(lens)
    return max(int(avg * 0.9), 120), min(int(avg * 1.1), 250)


def _clean_feature(feature: str, benefit: str) -> str:
    """防止模型把利益点前缀一起返回，导致 format_bullet 重复拼接。"""
    feat = (feature or "").strip().rstrip(".:!?。！")
    b = (benefit or "").strip()
    if b and feat.lower().startswith(b.lower()):
        feat = feat[len(b):].lstrip(":： ").strip()
    return feat


def _rewrite_to_range(site, item, lo, hi, facts, progress, attempts=3):
    """单条重写，带长度反馈重试，返回 (feature, zh, 最终长度)。"""
    benefit = item.get("benefit", "")
    feature, zh = item.get("feature", ""), item.get("zh", "")
    full = len(f"{benefit}: {feature}")
    for a in range(1, attempts + 1):
        if lo <= full <= hi:
            break
        delta = lo - full if full < lo else full - hi
        fb = "" if a == 1 else (
            f"上一版含利益点共 {full} 字符，目标 {lo}-{hi}；请再"
            f"{'扩写约 ' + str(delta) + ' 字符' if full < lo else '精简约 ' + str(delta) + ' 字符'}")
        fixed = chains.rewrite_bullet_feature(site, benefit, feature, lo, hi, facts, feedback=fb)
        if not fixed["feature"]:
            break
        feature = _clean_feature(fixed["feature"], benefit)
        zh = fixed["zh"] or zh
        full = len(f"{benefit}: {feature}")
        progress(f"    第 {a} 次 → {full} 字符")
    return feature, zh, full


def fix_bullet_lengths(site, bulletset, lo, hi, facts, progress, rounds=3):
    """长度校验 + 逐条重写（带反馈重试），最多 rounds 轮。允许下限 15 字符容差。"""
    soft_lo = max(lo - 15, 1)
    for r in range(1, rounds + 1):
        bad = [i for i, f in enumerate(bulletset["formatted"]) if not (soft_lo <= len(f) <= hi)]
        if not bad:
            progress(f"五点长度校验通过（目标 {lo}-{hi}，下限容差 {soft_lo}）")
            return bulletset
        progress(f"长度修正第 {r} 轮：{len(bad)} 条超界（目标 {lo}-{hi} 字符）")
        for i in bad:
            item = bulletset["bullets"][i]
            try:
                feature, zh, full = _rewrite_to_range(site, item, lo, hi, facts, progress)
                new = dict(item, feature=feature, zh=zh)
                bulletset["bullets"][i] = new
                bulletset["formatted"][i] = format_bullet(new)
                progress(f"  第 {i+1} 条收敛到 {full} 字符")
            except Exception as e:
                progress(f"  第 {i+1} 条重写失败：{e}")
    bad = [i for i, f in enumerate(bulletset["formatted"]) if not (soft_lo <= len(f) <= hi)]
    if bad:
        progress(f"仍有 {len(bad)} 条未达标（第 {'、'.join(str(i+1) for i in bad)} 条），已保留最后结果")
    else:
        progress(f"五点长度校验通过（目标 {lo}-{hi}，下限容差 {soft_lo}）")
    return bulletset


def generate(params: dict, progress=_noop) -> dict:
    """执行完整生成流程，返回结构化结果。params 见 web/app.py 与 main.py。"""
    site_code = (params.get("site") or os.getenv("SITE", "US")).upper()
    site = get_site(site_code)
    brand = params.get("brand") or ""
    asin = (params.get("asin") or "").strip()
    manual = params.get("manual")
    progress(f"目标站点 {site_code}（{site['domain']}）")

    comp_asins = [a.strip() for a in (params.get("competitors") or []) if a.strip()]
    competitors = []
    mine = None            # 目标站点下我的 Listing（或手动填写的）
    source_mine = None     # 源站点下的原生 Listing（跨站改写用）
    source_site_code = None
    source_site = None
    need_browser = bool(asin) or bool(comp_asins) or bool(params.get("auto_competitors"))

    # ----- 跨站改写：先尝试在主流站点抓到源 -----
    if asin and not manual:
        progress("探测 ASIN 源站点…")
        for probe_code in ASIN_PROBE_ORDER:
            if probe_code == site_code:
                continue
            try:
                ps = get_site(probe_code)
                with ListingFetcher(ps["domain"], ps["locale"], ps["timezone"],
                                    ps.get("currency", "USD"), headless=True) as pf:
                    d = pf.fetch(asin)
                    if d and d.get("title"):
                        source_site_code = probe_code
                        source_site = ps
                        source_mine = d
                        progress(f"ASIN 在 {probe_code}（{ps['domain']}）有原生 Listing，"
                                 f"原生五点 {len(d.get('bullets') or [])} 条")
                        break
            except Exception:
                continue

    # ----- 抓目标站点下我的 Listing（或用手动）-----
    if not need_browser:
        mine = manual_listing(manual or {}, brand)
        progress("使用手动产品信息（不抓取）")
    else:
        with ListingFetcher(site["domain"], site["locale"], site["timezone"],
                            site.get("currency", "USD"), headless=True) as f:
            if manual:
                mine = manual_listing(manual, brand)
                tip = f"（ASIN {asin} 仅作类目参考，不覆盖手填信息）" if asin else ""
                progress(f"使用手动产品信息{tip}")
            else:
                if source_site_code == site_code:
                    mine = source_mine   # 源就是目标，直接复用省一次抓取
                    source_mine = None   # 不再当作「跨站的额外参考」
                    source_site_code = None
                    source_site = None
                    progress(f"抓取我的 Listing（目标即源站点）：{asin}")
                else:
                    progress(f"抓取我的 Listing（目标站点）：{asin}")
                    try:
                        mine = f.fetch(asin)
                        progress(f"标题：{(mine.get('title') or '')[:70]}")
                    except Exception as e:
                        if source_mine:
                            progress(f"目标站点抓取失败：{e}，退回到源站点 Listing 作为主数据")
                            mine = source_mine
                            source_mine = None
                            source_site_code = None
                            source_site = None
                        else:
                            raise
            if params.get("auto_competitors") and not comp_asins:
                comp_asins = _auto_select(f, mine, asin, params, progress)
            progress(f"竞品：{comp_asins}")
            auto_pick = bool(params.get("auto_competitors")) and not params.get("competitors")
            for a in comp_asins:
                if len(competitors) >= 5:
                    break
                try:
                    d = f.fetch(a)
                    if auto_pick and _is_variant(d, competitors):
                        progress(f"↷ {a} 与已选竞品高度相似（同父体变体），跳过")
                        continue
                    competitors.append(d)
                    progress(f"✓ {a} 五点 {len(d.get('bullets') or [])} 条")
                except Exception as e:
                    progress(f"✗ {a} {e}")

    progress("竞品词根分析 + 关键词池")
    # 关键词池只从竞品 Listing 抽取：手动模式下 mine.title/bullets 是中文产品说明，
    # 混进来会产出「洗洁精」「矩形水槽收纳架」这类中文“关键词”
    analysis = analyze(competitors or [mine], asin)

    # 竞品标题里抽出的短语也补上搜索量，避免「无指标的词」排到有指标的前面
    phrase_terms = [p["phrase"] for p in analysis.get("top_phrases", [])][:60]
    phrase_metrics = {}
    if phrase_terms and not params.get("no_xiyou"):
        try:
            phrase_metrics = xiyou.enrich_keyword_info(phrase_terms, site_code)
            hit = sum(1 for k in phrase_terms
                      if (phrase_metrics.get(k.lower()) or {}).get("search_volume"))
            progress(f"竞品短语补指标 {hit}/{len(phrase_terms)}")
        except Exception as e:
            progress(f"竞品短语补指标失败：{e}")

    xiyou_rows = []
    seed = [k.strip() for k in (params.get("seed_keywords") or []) if k.strip()]
    if not params.get("no_xiyou"):
        if asin:
            try:
                xiyou_rows = xiyou.fetch_asin_keywords(asin, site_code, top=int(params.get("xiyou_top") or 50))
                progress(f"西柚反查 {len(xiyou_rows)} 个词")
                if xiyou_rows and not params.get("no_xiyou_enrich"):
                    info = xiyou.enrich_keyword_info([r["keyword"] for r in xiyou_rows], site_code)
                    for r in xiyou_rows:
                        r.update(info.get(r["keyword"].lower(), {}))
            except Exception as e:
                progress(f"西柚反查失败：{e}")
        if seed:
            try:
                info = xiyou.enrich_keyword_info(seed, site_code)
                xiyou_rows = [{"keyword": k, **info.get(k.lower(), {})} for k in seed] + xiyou_rows
                progress(f"已补入手动关键词 {len(seed)} 个")
            except Exception as e:
                progress(f"手动关键词补指标失败：{e}")
                xiyou_rows = [{"keyword": k} for k in seed] + xiyou_rows

    brand_tokens = set()
    for name in [brand, mine.get("byline")] + [d.get("byline") for d in competitors]:
        for w in re.findall(r"[A-Za-z]{3,}", str(name or "")):
            w = w.lower()
            if w not in {"the", "visit", "store", "brand", "from"}:
                brand_tokens.add(w)

    ranked = merge_with_xiyou(analysis, xiyou_rows,
                              exclude_tokens=brand_tokens, phrase_metrics=phrase_metrics)
    keywords = [x["keyword"] for x in ranked]
    src = {}
    for x in ranked:
        src[x["source"]] = src.get(x["source"], 0) + 1
    progress(f"关键词来源统计：{src}")
    progress(f"关键词池前 15：{keywords[:15]}")

    facts = build_product_facts(mine, params.get("extra") or "", params.get("drop_spec"))
    # 跨站改写时，把源站点原生五点也写进 product_facts，作为主要卖点依据
    if source_mine and source_mine.get("bullets"):
        facts += "\n\n源站点（" + (source_site_code or "") + "）原生五点参考："
        facts += "\n" + "\n".join(f"- {b}" for b in source_mine["bullets"][:6])

    # ----- 卖点数量 → 决定五点条数 -----
    feature_points = extract_feature_points(mine, source_mine, params.get("extra") or "")
    n_points = len(feature_points)
    bullet_count = min(max(n_points, 1), 5)   # 最少 1 条，最多 5 条
    info_insufficient = n_points < 3
    progress(f"产品事实中抽到 {n_points} 个独立卖点，生成 {bullet_count} 条五点")
    if info_insufficient:
        progress(f"⚠ 产品信息偏少（只有 {n_points} 个独立卖点），"
                 "只能生成较少的五点；请补充更多产品特征、规格或卖点以获得完整 5 条")

    comp_titles = [d.get("title") for d in competitors if d.get("title")]
    specs = mine.get("specs") or {}
    differentiators = "、".join(filter(None, [
        specs.get("Special Feature"), specs.get("FinishType"), specs.get("Finish Type"),
        f"材质 {specs['Material']}" if specs.get("Material") else "",
    ]))

    product_input = f"{facts}\n\n竞品五点参考：\n" + "\n".join(
        f"- {d.get('asin') or '竞品'}: " + " | ".join((d.get('bullets') or [])[:2])
        for d in competitors[:3])
    if source_mine and source_mine.get("bullets"):
        product_input += (f"\n\n源站点（{source_site_code}）原生五点（改写时保留每个卖点的核心信息，"
                          f"但要用目标站点的语言/表达重新写，且要埋入目标站点关键词）：\n"
                          + "\n".join(f"- {b}" for b in source_mine["bullets"][:6]))
    progress("生成商品名称 + 商品亮点（3 个角度）")
    titles = chains.generate_titles(
        site, product_input, facts, comp_titles, keywords,
        differentiators=differentiators, brand=brand)
    chosen = titles[0]
    kw_plan = split_keywords(keywords, chosen["product_name"])
    progress(f"标题已覆盖 {len(kw_plan['covered'])} 个词，五点优先埋入剩余 "
             f"{len(kw_plan['remaining'])} 个")

    progress(f"生成 {bullet_count} 条五点描述（按卖点数量）")
    bullet_style = summarize_bullet_style(competitors)
    if bullet_style:
        progress(f"竞品篇幅基准：{bullet_style}")
    plans = chains.generate_bullet_plans(site, facts,
                                         kw_plan["remaining"] or keywords, bullet_style,
                                         n_bullets=bullet_count)
    plan = plans[0]
    bulletset = chains.generate_bullets(
        site, facts, kw_plan["remaining"] or keywords, plan.get("direction", ""),
        plan.get("angles", []), bullet_style, covered_keywords=kw_plan["covered"],
        n_bullets=bullet_count)
    lo, hi = bullet_target_range(competitors)
    progress(f"五点长度目标：{lo}-{hi} 字符")
    bulletset = fix_bullet_lengths(site, bulletset, lo, hi, facts, progress)

    # ----- 五点去重 -----
    bulletset = dedupe_bullets(site, bulletset, facts, lo, hi, progress)

    zh = {"name_zh": "", "highlights_zh": ""}
    try:
        zh = chains.translate_listing(chosen["product_name"], chosen["highlights"])
    except Exception as e:
        progress(f"中文翻译失败：{e}")

    bissues = check_bullets(bulletset["bullets"])
    title_variants = [{"angle": t["angle"], "product_name": t["product_name"],
                       "highlights": t["highlights"]} for t in titles]
    try:
        trans = chains.translate_titles(title_variants)
        for v, tr in zip(title_variants, trans):
            v["product_name_zh"] = tr.get("name_zh", "")
            v["highlights_zh"] = tr.get("highlights_zh", "")
    except Exception as e:
        progress(f"其他标题方案中文翻译失败：{e}")
    result = {
        "asin": asin,
        "site": site_code,
        "source_site": source_site_code,
        "brand": brand,
        "product_facts": facts,
        "feature_points": feature_points,
        "bullet_count": bullet_count,
        "info_insufficient": info_insufficient,
        "keyword_pool": keywords[:30],
        "product_name": chosen["product_name"],
        "product_name_zh": zh.get("name_zh", ""),
        "highlights": chosen["highlights"],
        "highlights_zh": zh.get("highlights_zh", ""),
        "bullets": bulletset["formatted"],
        "bullets_zh": [b.get("zh", "") for b in bulletset["bullets"]],
        "title_variants": title_variants,
        "bullet_angle": plan.get("direction", ""),
        "competitors": [{
            "asin": d.get("asin"),
            "title": d.get("title") or "",
            "url": d.get("url") or "",
            "price": d.get("price") or "",
            "rating": d.get("rating") or "",
            "review_count": d.get("review_count") or "",
            "bullets": (d.get("bullets") or [])[:8],
        } for d in competitors],
        "compliance": {
            "name_issues": check_product_name(chosen["product_name"], brand),
            "highlight_issues": check_highlights(chosen["highlights"]),
            "bullet_issues": bissues,
        },
    }

    stem = asin or f"custom_{time.strftime('%Y%m%d_%H%M%S')}"
    if params.get("review", True):
        progress("运行合规评估（amazon-ops-optimizer）")
        result["review"] = reviewer.review_and_adjust(
            dict(site, code=site_code, brand=brand), result, facts, keywords, progress)
    else:
        result["review"] = {"verdict": "skipped", "issues": [], "adjusted": False}

    cov = keyword_coverage(keywords, result["product_name"], result["bullets"])
    result["keyword_coverage"] = cov
    progress(f"关键词覆盖 {cov['hit']}/{cov['total']}，未覆盖："
             f"{', '.join(cov['missing'][:8]) or '无'}")

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"{stem}_listing.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    en_txt = (
        f"[Product Name - {len(result['product_name'])}/75 chars]\n{result['product_name']}\n\n"
        f"[Product Highlights - {len(result['highlights'])}/125 chars]\n{result['highlights']}\n\n"
        "[Bullet Points]\n" + "\n".join(result["bullets"]) + "\n")
    (OUT_DIR / f"{stem}_listing_EN.txt").write_text(en_txt, encoding="utf-8")
    review = (en_txt + "\n\n===== 中文对照（仅供审核，勿上传）=====\n"
              f"[名称] {result['product_name_zh']}\n[亮点] {result['highlights_zh']}\n"
              + "\n".join(f"{i}. {z}" for i, z in enumerate(result["bullets_zh"], 1)))
    (OUT_DIR / f"{stem}_listing_对照.txt").write_text(review, encoding="utf-8")
    result["output_stem"] = stem
    progress(f"已输出到 {OUT_DIR}")
    return result
