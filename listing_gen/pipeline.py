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

from listing_gen import chains, xiyou
from listing_gen.config import get_site
from listing_gen.fetcher import ListingFetcher
from listing_gen.keyword_analyzer import analyze, merge_with_xiyou
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


def _auto_select(fetcher, my_asin, params, progress):
    """关键词搜索结果 → BSR 校验 → 前 5 个竞品。

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
                cand.append(c["asin"])
        if len(cand) >= 80:
            break
    cand = cand[:80]
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
    progress(f"站点 {site_code}（{site['domain']}）")

    comp_asins = [a.strip() for a in (params.get("competitors") or []) if a.strip()]
    competitors = []
    mine = None
    need_browser = bool(asin) or bool(comp_asins) or bool(params.get("auto_competitors"))

    if not need_browser:
        mine = manual_listing(manual or {}, brand)
        progress("使用手动产品信息（不抓取）")
    else:
        with ListingFetcher(site["domain"], site["locale"], site["timezone"], headless=True) as f:
            if manual:
                mine = manual_listing(manual, brand)
                tip = f"（ASIN {asin} 仅作类目参考，不覆盖手填信息）" if asin else ""
                progress(f"使用手动产品信息{tip}")
            else:
                progress(f"抓取我的 Listing：{asin}")
                mine = f.fetch(asin)
                progress(f"标题：{(mine.get('title') or '')[:70]}")
            if params.get("auto_competitors") and not comp_asins:
                comp_asins = _auto_select(f, asin, params, progress)
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
    analysis = analyze([mine] + competitors, asin)

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
    comp_titles = [d.get("title") for d in competitors if d.get("title")]
    specs = mine.get("specs") or {}
    differentiators = "、".join(filter(None, [
        specs.get("Special Feature"), specs.get("FinishType"), specs.get("Finish Type"),
        f"材质 {specs['Material']}" if specs.get("Material") else "",
    ]))

    product_input = f"{facts}\n\n竞品五点参考：\n" + "\n".join(
        f"- {d.get('asin') or '竞品'}: " + " | ".join((d.get('bullets') or [])[:2])
        for d in competitors[:3])
    progress("生成商品名称 + 商品亮点（3 个角度）")
    titles = chains.generate_titles(
        site, product_input, facts, comp_titles, keywords,
        differentiators=differentiators, brand=brand)
    chosen = titles[0]

    progress("生成五点描述")
    bullet_style = summarize_bullet_style(competitors)
    if bullet_style:
        progress(f"竞品篇幅基准：{bullet_style}")
    plans = chains.generate_bullet_plans(site, facts, keywords, bullet_style)
    plan = plans[0]
    bulletset = chains.generate_bullets(
        site, facts, keywords, plan.get("direction", ""), plan.get("angles", []), bullet_style)
    lo, hi = bullet_target_range(competitors)
    progress(f"五点长度目标：{lo}-{hi} 字符")
    bulletset = fix_bullet_lengths(site, bulletset, lo, hi, facts, progress)

    zh = {"name_zh": "", "highlights_zh": ""}
    try:
        zh = chains.translate_listing(chosen["product_name"], chosen["highlights"])
    except Exception as e:
        progress(f"中文翻译失败：{e}")

    bissues = check_bullets(bulletset["bullets"])
    result = {
        "asin": asin,
        "site": site_code,
        "brand": brand,
        "product_facts": facts,
        "keyword_pool": keywords[:30],
        "product_name": chosen["product_name"],
        "product_name_zh": zh.get("name_zh", ""),
        "highlights": chosen["highlights"],
        "highlights_zh": zh.get("highlights_zh", ""),
        "bullets": bulletset["formatted"],
        "bullets_zh": [b.get("zh", "") for b in bulletset["bullets"]],
        "title_variants": [{"angle": t["angle"], "product_name": t["product_name"],
                            "highlights": t["highlights"]} for t in titles],
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
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"{stem}_listing.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    en_txt = (
        f"[Product Name - {len(chosen['product_name'])}/75 chars]\n{chosen['product_name']}\n\n"
        f"[Product Highlights - {len(chosen['highlights'])}/125 chars]\n{chosen['highlights']}\n\n"
        "[Bullet Points]\n" + "\n".join(bulletset["formatted"]) + "\n")
    (OUT_DIR / f"{stem}_listing_EN.txt").write_text(en_txt, encoding="utf-8")
    review = (en_txt + "\n\n===== 中文对照（仅供审核，勿上传）=====\n"
              f"[名称] {result['product_name_zh']}\n[亮点] {result['highlights_zh']}\n"
              + "\n".join(f"{i}. {z}" for i, z in enumerate(result["bullets_zh"], 1)))
    (OUT_DIR / f"{stem}_listing_对照.txt").write_text(review, encoding="utf-8")
    result["output_stem"] = stem
    progress(f"已输出到 {OUT_DIR}")
    return result
