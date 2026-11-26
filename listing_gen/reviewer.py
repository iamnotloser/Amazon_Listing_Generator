"""Listing 评估与定向调整。

复用 amazon-ops-optimizer 技能的确定性扫描脚本与公开硬规则：
1. 跑技能自带 compliance_scan.py（字符数、禁符号、敏感词、占位词等）
2. LLM 按技能的内容质量清单做语义复核（价值陈述、埋词、人群场景、量化表述）
3. 有 FAIL/WARN 时做一轮定向重写，再复扫确认
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from listing_gen import chains
from listing_gen.title_builder import check_bullets, check_highlights, check_product_name

SKILL_DIR = Path(os.getenv("AMAZON_OPS_SKILL_DIR") or
                 (Path.home() / ".trae-cn" / "skills" / "amazon-ops-optimizer"))
SCAN_SCRIPT = SKILL_DIR / "scripts" / "compliance_scan.py"


class Issue(BaseModel):
    level: Literal["FAIL", "WARN", "INFO"]
    location: str = Field(description="标题 / 亮点 / 第几条第几点")
    problem: str
    fix: str = ""


class SemanticReview(BaseModel):
    verdict: Literal["ready", "fix", "blocked"]
    summary: str = ""
    issues: list[Issue] = Field(default_factory=list)


class Adjusted(BaseModel):
    product_name: str
    highlights: str
    bullets: list[str] = Field(description="5 条英文五点，格式为「利益点: 描述」")


class BulletTranslations(BaseModel):
    items: list[str] = Field(description="与输入一一对应的中文翻译")


REVIEW_SYSTEM = """你是亚马逊 Listing 合规与内容质量审核员，依据以下公开硬规则审核，只输出问题，不要重写文案。

【标题】
- 品牌名放在第 1 或第 2 个词；主推词尽量在前 33 字符，主推+次主推在前 80 字符
- 每个单词重复不超过 3 次；用阿拉伯数字；单位写完整（6 inches 而非 6"）
- 禁止促销/主观评价（Best Seller、Hot Sale、100% Quality 等）、价格、特殊符号、表情
- 标题应同时是价值陈述：核心功能 + 使用场景 + 首要收益

【商品亮点】
- 与标题不重复堆词，优先量化参数（材质、尺寸、承重），避免 Premium / Excellent / Heavy Duty 这类空泛形容词

【五点描述】
- 每条至少埋 2 个不重复关键词，语义自然，禁止关键词堆砌
- 第 1-3 条承担卖点与差异化；第 4 条为使用场景/人群标签，需带具体约束（如 fits sinks under 32 inches）；第 5 条为包装清单或服务，用客观表述
- 空泛表述必须换成可验证的量化表述；不得出现保证类承诺、公司信息、链接、ASIN
- 结尾不加标点，每条不超过 500 字符

【跨模块】
- 标题、亮点、五点中的数量/规格/材质必须一致
- 不得出现与事实不符的功能或配件描述
- 文案描述的产品形态必须与下面「产品事实」一致；若写成其他品类（如把水槽收纳架写成餐具沥水架）记 FAIL
- 产品事实中列出的核心卖点，五点里缺失的记 WARN

判断标准：违反硬规则记 FAIL；内容质量不足（空泛、缺人群场景、缺量化、关键词覆盖不足）记 WARN；可选优化记 INFO。"""


def _split_bullet(text: str) -> dict:
    """把「利益点: 描述」拆成 check_bullets 需要的结构"""
    if ": " in text:
        benefit, feature = text.split(": ", 1)
        return {"benefit": benefit, "feature": feature}
    return {"benefit": text, "feature": ""}


def _parse_scan(output: str) -> list[dict]:
    """解析 compliance_scan.py 的文本输出"""
    issues, pending = [], None
    for raw in (output or "").splitlines():
        line = raw.strip()
        m = re.search(r"\b(FAIL|WARN)\s*\|\s*(.+)$", line)
        if m:
            if pending:
                issues.append(pending)
            pending = {"level": m.group(1), "location": m.group(2).strip(),
                       "problem": "", "fix": ""}
            continue
        if not pending:
            continue
        if line.startswith("问题:"):
            pending["problem"] = line.split(":", 1)[1].strip()
        elif line.startswith("建议:"):
            pending["fix"] = line.split(":", 1)[1].strip()
    if pending:
        issues.append(pending)
    return issues


def deterministic_scan(title: str, highlights: str, bullets: list,
                       title_limit: int = 200, bullet_limit: int = 500) -> dict:
    """调用技能自带的确定性扫描脚本"""
    if not SCAN_SCRIPT.exists():
        return {"available": False, "issues": [], "raw": ""}
    lines = [f"[TITLE] {title}", highlights]
    lines += [f"[BULLET] {b}" for b in bullets]
    fd, path = tempfile.mkstemp(suffix=".txt", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        proc = subprocess.run(
            [sys.executable, str(SCAN_SCRIPT), path,
             "--title-limit", str(title_limit), "--bullet-limit", str(bullet_limit)],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        out = (proc.stdout or "") + (proc.stderr or "")
    except Exception as e:
        return {"available": False, "issues": [], "raw": f"扫描失败：{e}"}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return {"available": True, "issues": _parse_scan(out), "raw": out.strip()}


def semantic_review(site: dict, title: str, highlights: str, bullets: list,
                    keywords: list, facts: str = "") -> dict:
    """LLM 语义复核（按技能的内容质量清单）"""
    human = (
        f"站点：{site.get('code')} 语言：{site.get('language')} "
        f"拼写：{site.get('spelling')}\n"
        f"标题（{len(title)} 字符）：{title}\n"
        f"亮点（{len(highlights)} 字符）：{highlights}\n"
        "五点：\n" + "\n".join(f"{i}. {b}" for i, b in enumerate(bullets, 1)) +
        f"\n\n目标关键词（按优先级）：{', '.join(keywords[:15])}" +
        (f"\n\n产品事实（判断产品形态与卖点是否一致）：\n{facts}" if facts else "")
    )
    llm = chains.get_llm(temperature=0)
    return chains.call_structured(llm, REVIEW_SYSTEM, human, {}, SemanticReview, temperature=0)


def adjust_listing(site: dict, title: str, highlights: str, bullets: list,
                   issues: list, facts: str, progress=lambda *_: None) -> dict | None:
    """按评估问题做一轮定向重写"""
    fix_lines = "\n".join(
        f"- [{x['level']}] {x['location']}：{x['problem']} → {x.get('fix') or '按规则修正'}"
        for x in issues if x.get("level") in ("FAIL", "WARN"))
    human = (
        f"产品事实（不得改动、不得新增未提供的参数）：\n{facts}\n\n"
        f"当前标题（上限 {site.get('title_max')} 字符）：{title}\n"
        f"当前亮点（上限 {site.get('highlight_max')} 字符）：{highlights}\n"
        "当前五点：\n" + "\n".join(f"{i}. {b}" for i, b in enumerate(bullets, 1)) +
        f"\n\n需要修正的问题：\n{fix_lines}\n\n"
        f"请在不改变产品事实的前提下逐条修正，使用{site.get('language')}"
        f"（{site.get('spelling')}），五点仍为 5 条，"
        "标题与亮点必须在字符上限内。"
    )
    llm = chains.get_llm(temperature=0.2)
    feedback = ""
    errs = []
    title_max = int(site.get("title_max") or 75)
    hi_max = int(site.get("highlight_max") or 125)
    best = {}
    for attempt in range(3):
        try:
            data = chains.call_structured(llm, REVIEW_SYSTEM, human + feedback, {},
                                          Adjusted, temperature=0.2)
        except Exception:
            break
        name = (data.get("product_name") or "").strip()
        hi = (data.get("highlights") or "").strip()
        bl = [b.strip() for b in (data.get("bullets") or []) if b and b.strip()]

        errs = []
        # 先用技能扫描器做硬规则复核（禁词/禁符号），有 FAIL 直接重写
        scan = deterministic_scan(name or title, hi or highlights, bl or bullets,
                                  title_max, int(site.get("bullet_max") or 500))
        scan_fails = [x for x in scan["issues"] if x["level"] == "FAIL"]
        if scan_fails:
            errs.extend(f"{x['location']}：{x['problem']}" for x in scan_fails[:4])
            progress(f"第 {attempt + 1} 次重写仍有硬规则冲突：{'；'.join(errs[:3])}")
            feedback = ("\n\n上一版仍命中硬规则：\n" + "\n".join(f"- {e}" for e in errs) +
                        "\n请删除违规词/符号后重新输出完整内容。")
            continue
        # 分字段校验，合格的部分先收下，避免整份被一个超长字段拖死
        if name and len(name) <= title_max and not check_product_name(name, site.get("brand") or ""):
            best["product_name"] = name
        else:
            errs.append(f"标题必须在 {title_max} 字符内")
        if hi and len(hi) <= hi_max and not check_highlights(hi):
            best["highlights"] = hi
        else:
            errs.append(f"亮点必须在 {hi_max} 字符内")
        if len(bl) >= 5:
            b_errs = check_bullets([_split_bullet(b) for b in bl])
            if not b_errs:
                best["bullets"] = bl
            else:
                errs.extend(str(e) for e in b_errs[:4])
        else:
            errs.append("五点必须 5 条")

        if len(best) == 3:
            return best
        progress(f"第 {attempt + 1} 次重写部分未通过：{'；'.join(errs[:3])}")
        feedback = ("\n\n上一版未通过校验：\n" + "\n".join(f"- {e}" for e in errs) +
                    "\n标题与亮点必须严格在字符上限内（输出前请自行数字符），五点保持 5 条。")
    return best or None


def translate_bullets(bullets: list) -> list:
    llm = chains.get_llm(temperature=0.2)
    human = "逐条翻译为简洁中文，保持顺序与条数，只翻译不增删内容：\n" + \
            "\n".join(f"{i}. {b}" for i, b in enumerate(bullets, 1))
    try:
        data = chains.call_structured(llm, "你是亚马逊文案翻译。", human, {},
                                      BulletTranslations, temperature=0.2)
        items = [x.strip() for x in data.get("items") or []]
        return items if len(items) == len(bullets) else []
    except Exception:
        return []


def review_and_adjust(site: dict, result: dict, facts: str, keywords: list,
                      progress=lambda *_: None) -> dict:
    """完整评估：确定性扫描 + 语义复核 → 定向重写 → 复扫"""
    title = result["product_name"]
    highlights = result["highlights"]
    bullets = list(result["bullets"])

    det = deterministic_scan(title, highlights, bullets,
                             int(site.get("title_max") or 200),
                             int(site.get("bullet_max") or 500))
    progress(f"技能扫描：{'可用' if det['available'] else '不可用'}，命中 {len(det['issues'])} 项")

    sem = {"verdict": "ready", "issues": [], "summary": ""}
    try:
        sem = semantic_review(site, title, highlights, bullets, keywords, facts)
    except Exception as e:
        progress(f"语义复核失败：{e}")

    issues = det["issues"] + list(sem.get("issues") or [])
    # 扫描器把「五点 > 建议值 200 字符」记为 WARN，但本项目按竞品篇幅基准定向控制长度，
    # 该条属规则建议而非硬约束，避免与长度目标冲突造成噪声
    issues = [x for x in issues if "建议值 200" not in (x.get("problem") or "")]
    fails = [x for x in issues if x["level"] == "FAIL"]
    warns = [x for x in issues if x["level"] == "WARN"]
    verdict = "blocked" if fails else ("fix" if warns else "ready")

    report = {
        "verdict": verdict,
        "summary": sem.get("summary") or "",
        "scan_available": det["available"],
        "issues": issues,
        "adjusted": False,
        "before": {"product_name": title, "highlights": highlights, "bullets": bullets},
    }
    if not issues:
        progress("评估通过，无需调整")
        return report

    progress(f"评估发现 FAIL {len(fails)} 项 / WARN {len(warns)} 项，执行定向重写")
    fixed = adjust_listing(site, title, highlights, bullets, issues, facts, progress)
    if not fixed:
        progress("定向重写未通过校验，保留原稿")
        return report

    report["adjusted"] = True
    report["before"] = {"product_name": title, "highlights": highlights, "bullets": bullets}
    report["after"] = fixed

    new_name = fixed.get("product_name") or title
    new_hi = fixed.get("highlights") or highlights
    new_bullets = fixed.get("bullets") or bullets
    result["product_name"] = new_name
    result["highlights"] = new_hi
    result["bullets"] = new_bullets
    result["compliance"] = {
        "name_issues": check_product_name(new_name, site.get("brand") or ""),
        "highlight_issues": check_highlights(new_hi),
        "bullet_issues": check_bullets([_split_bullet(b) for b in new_bullets]),
    }

    try:
        zh = chains.translate_listing(new_name, new_hi)
        result["product_name_zh"] = zh.get("name_zh", "")
        result["highlights_zh"] = zh.get("highlights_zh", "")
    except Exception as e:
        progress(f"调整后中文翻译失败：{e}")
    zh_bullets = translate_bullets(new_bullets)
    if zh_bullets:
        result["bullets_zh"] = zh_bullets

    after_scan = deterministic_scan(
        new_name, new_hi, new_bullets,
        int(site.get("title_max") or 200), int(site.get("bullet_max") or 500))
    after_issues = [x for x in after_scan["issues"] if "建议值 200" not in (x.get("problem") or "")]
    report["after_issues"] = after_issues
    report["after_verdict"] = ("blocked" if any(x["level"] == "FAIL" for x in after_issues)
                               else ("fix" if after_issues else "ready"))
    progress(f"调整后复扫：{report['after_verdict']}，剩余 {len(after_issues)} 项")
    return report
