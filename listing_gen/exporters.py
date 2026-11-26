"""把 pipeline.generate() 的 result 导出为 .md / .docx / .pdf。

三个函数都是返回 bytes，让上层决定写文件还是直接流式返回给浏览器。
"""
from __future__ import annotations

import io
import os
import platform
import re
from datetime import datetime


# ================== Markdown ==================

def to_markdown(result: dict) -> bytes:
    """中英对照 + 合规评估摘要的 Markdown。"""
    out = []
    site_code = result.get("site") or ""
    source_site = result.get("source_site")
    asin = result.get("asin") or ""
    brand = result.get("brand") or ""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    out.append(f"# Amazon Listing - {site_code}{f' (源站点 {source_site})' if source_site else ''}")
    out.append(f"**ASIN**: `{asin}`  |  **Brand**: {brand}  |  **生成时间**: {stamp}")
    if result.get("info_insufficient"):
        out.append("> ⚠ 产品信息偏少，当前仅生成了 "
                   f"{result.get('bullet_count')} 条五点；建议补充更多产品特征。")
    out.append("")

    out.append("## 商品名称 Product Title")
    en = result.get("product_name") or ""
    zh = result.get("product_name_zh") or ""
    out.append(f"- EN: {en}")
    out.append(f"- 中文: {zh}")
    out.append(f"- 字符数: {len(en)}")
    out.append("")

    # 候选角度
    variants = result.get("title_variants") or []
    if len(variants) > 1:
        out.append("### 备选角度")
        for i, v in enumerate(variants[1:], 2):
            out.append(f"{i}. *{v.get('angle','').strip()}*")
            out.append(f"   - EN: {v.get('product_name','')}")
            out.append(f"   - 中文: {v.get('product_name_zh','')}")
            out.append(f"   - 亮点 EN: {v.get('highlights','')}")
            out.append(f"   - 亮点中文: {v.get('highlights_zh','')}")
        out.append("")

    out.append("## 商品亮点 Bullet Points 前的 Highlight")
    out.append(f"- EN: {result.get('highlights') or ''}")
    out.append(f"- 中文: {result.get('highlights_zh') or ''}")
    out.append("")

    out.append("## 五点描述 Bullet Points")
    out.append(f"_角度方向：{result.get('bullet_angle') or ''}_")
    bullets = result.get("bullets") or []
    bullets_zh = result.get("bullets_zh") or []
    for i, (b, bzh) in enumerate(zip(bullets, bullets_zh), 1):
        out.append(f"**{i}.** {b}")
        if bzh:
            out.append(f"   *{bzh}*")
        out.append("")

    # 关键词覆盖
    cov = result.get("keyword_coverage")
    if cov:
        out.append("## 关键词覆盖")
        out.append(f"- 覆盖 {cov.get('hit')}/{cov.get('total')}")
        if cov.get("missing"):
            out.append(f"- 未覆盖: {', '.join(cov['missing'][:15])}")
        out.append("")

    # 合规评估
    rv = result.get("review") or {}
    if rv.get("verdict") and rv["verdict"] != "skipped":
        out.append("## 合规评估（amazon-ops-optimizer）")
        labels = {"ready": "通过", "fix": "建议修改", "blocked": "存在阻断项",
                  "skipped": "未执行"}
        label = labels.get(rv["verdict"], rv["verdict"])
        after = rv.get("after_verdict")
        out.append(f"- 结论: **{label}**"
                   + (f"  （调整后: {labels.get(after, after)}）" if after else ""))
        out.append(f"- 是否自动调整: {'是' if rv.get('adjusted') else '否'}")
        out.append("")
        issues = rv.get("issues") or []
        if issues:
            out.append("| 级别 | 位置 | 问题 | 建议 |")
            out.append("|---|---|---|---|")
            for x in issues:
                fix = (x.get("fix") or "").replace("|", "/")
                out.append(f"| {x.get('level')} | {x.get('location')} | "
                           f"{(x.get('problem') or '').replace('|','/')} | {fix} |")
            out.append("")
        if rv.get("adjusted") and rv.get("before"):
            out.append("<details><summary>查看调整前内容</summary>")
            bef = rv["before"]
            out.append("")
            out.append(f"**标题（调整前）**: {bef.get('product_name','')}")
            out.append(f"**亮点（调整前）**: {bef.get('highlights','')}")
            for i, b in enumerate(bef.get("bullets") or [], 1):
                out.append(f"{i}. {b}")
            out.append("")
            out.append("</details>")
            out.append("")

    out.append("---")
    out.append("_由 Amazon Listing Generator 生成_")
    return "\n".join(out).encode("utf-8")


# ================== Word / .docx ==================

def _docx_styles():
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt

    styles = []
    for s in ("Title", "Heading 1", "Heading 2", "Normal"):
        styles.append(s)
    return styles


def to_docx(result: dict) -> bytes:
    from docx import Document
    from docx.shared import Pt, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # ---- 字体配置：英文 Arial，中文 Microsoft YaHei ----
    def _set_cjk_font(paragraph, size_pt=None, bold=False):
        for run in paragraph.runs:
            run.font.name = "Arial"
            if size_pt:
                run.font.size = Pt(size_pt)
            if bold:
                run.bold = True
            rPr = run._element.get_or_add_rPr()
            rFonts = rPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rFonts")
            if rFonts is None:
                from docx.oxml import OxmlElement
                rFonts = OxmlElement("w:rFonts")
                rPr.insert(0, rFonts)
            rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia",
                       "Microsoft YaHei")

    def h1(text):
        p = doc.add_heading(text, level=1)
        _set_cjk_font(p, 16, True)

    def h2(text):
        p = doc.add_heading(text, level=2)
        _set_cjk_font(p, 14, True)

    def p(text, bold=False):
        pp = doc.add_paragraph()
        run = pp.add_run(text)
        run.font.name = "Arial"
        run.font.size = Pt(11)
        if bold:
            run.bold = True
        rPr = run._element.get_or_add_rPr()
        rFonts = rPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rFonts")
        if rFonts is None:
            from docx.oxml import OxmlElement
            rFonts = OxmlElement("w:rFonts")
            rPr.insert(0, rFonts)
        rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia",
                   "Microsoft YaHei")
        return pp

    def bullet(text):
        pp = doc.add_paragraph(text, style="List Bullet")
        _set_cjk_font(pp, 11)
        return pp

    # ---- 文档正文 ----
    site_code = result.get("site") or ""
    asin = result.get("asin") or ""
    brand = result.get("brand") or ""

    h1(f"Amazon Listing - {site_code}")
    info = doc.add_paragraph()
    run = info.add_run(f"ASIN: {asin}   |   Brand: {brand}   |   "
                       f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    run.font.name = "Arial"; run.font.size = Pt(10)

    if result.get("info_insufficient"):
        pp = doc.add_paragraph()
        run = pp.add_run("⚠ 产品信息偏少，当前仅生成了 "
                         f"{result.get('bullet_count')} 条五点；建议补充更多产品特征。")
        run.font.name = "Arial"; run.font.size = Pt(10); run.italic = True

    h2("商品名称 Product Title")
    p(f"EN: {result.get('product_name') or ''}")
    p(f"中文: {result.get('product_name_zh') or ''}")

    h2("商品亮点 Bullet Points 前的 Highlight")
    p(f"EN: {result.get('highlights') or ''}")
    p(f"中文: {result.get('highlights_zh') or ''}")

    h2("五点描述 Bullet Points")
    if result.get("bullet_angle"):
        p(f"方向: {result['bullet_angle']}")
    bullets = result.get("bullets") or []
    bullets_zh = result.get("bullets_zh") or []
    for i, (b, bzh) in enumerate(zip(bullets, bullets_zh), 1):
        pp = doc.add_paragraph()
        run = pp.add_run(f"{i}. {b}")
        run.font.name = "Arial"; run.font.size = Pt(11)
        rPr = run._element.get_or_add_rPr()
        rFonts = rPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rFonts")
        if rFonts is None:
            from docx.oxml import OxmlElement
            rFonts = OxmlElement("w:rFonts")
            rPr.insert(0, rFonts)
        rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia",
                   "Microsoft YaHei")
        if bzh:
            zh_pp = doc.add_paragraph()
            run = zh_pp.add_run(f"    {bzh}")
            run.font.name = "Arial"; run.font.size = Pt(10); run.italic = True

    # 关键词覆盖
    cov = result.get("keyword_coverage")
    if cov:
        h2("关键词覆盖")
        p(f"覆盖 {cov.get('hit')}/{cov.get('total')}")
        if cov.get("missing"):
            p("未覆盖: " + ", ".join(cov["missing"][:15]))

    # 合规评估
    rv = result.get("review") or {}
    if rv.get("verdict") and rv["verdict"] != "skipped":
        h2("合规评估（amazon-ops-optimizer）")
        labels = {"ready": "通过", "fix": "建议修改", "blocked": "存在阻断项"}
        label = labels.get(rv["verdict"], rv["verdict"])
        after = rv.get("after_verdict")
        p(f"结论: {label}"
          + (f"  （调整后: {labels.get(after, after)}）" if after else ""))

        issues = rv.get("issues") or []
        if issues:
            table = doc.add_table(rows=1, cols=4)
            table.style = "Light Grid Accent 1"
            hdr = table.rows[0].cells
            hdr[0].text = "级别"
            hdr[1].text = "位置"
            hdr[2].text = "问题"
            hdr[3].text = "建议"
            for x in issues:
                row = table.add_row().cells
                row[0].text = x.get("level") or ""
                row[1].text = x.get("location") or ""
                row[2].text = x.get("problem") or ""
                row[3].text = x.get("fix") or ""

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ================== PDF ==================

def _register_cjk_font():
    """找一个 CJK 字体（Windows: 微软雅黑；Linux/Mac: Noto Sans CJK/PingFang）"""
    import platform as _p
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    candidates = []
    if _p.system() == "Windows":
        candidates = [
            ("C:/Windows/Fonts/msyh.ttc", 0, "MSYaHei"),
            ("C:/Windows/Fonts/msyh.ttf", 0, "MSYaHei"),
            ("C:/Windows/Fonts/simhei.ttf", 0, "SimHei"),
            ("C:/Windows/Fonts/simsun.ttc", 0, "SimSun"),
        ]
    elif _p.system() == "Darwin":
        candidates = [
            ("/System/Library/Fonts/PingFang.ttc", 0, "PingFang"),
            ("/Library/Fonts/Arial Unicode.ttf", 0, "ArialUni"),
        ]
    else:
        candidates = [
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0, "NotoCJK"),
            ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0, "WenQuanYi"),
            ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0, "NotoCJK"),
        ]
    for path, idx, name in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx))
                return name
            except Exception:
                try:
                    pdfmetrics.registerFont(TTFont(name, path))
                    return name
                except Exception:
                    continue
    return "Helvetica"   # 兜底（中文会成方块，但英文没问题）


def to_pdf(result: dict) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle)

    cjk = _register_cjk_font()
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle("Title", fontName=cjk, fontSize=22, leading=28,
                                  textColor=HexColor("#1a365d"), spaceAfter=16,
                                  alignment=1, wordWrap="CJK")
    h1_style = ParagraphStyle("H1", fontName=cjk, fontSize=14, leading=20,
                               textColor=HexColor("#2b6cb0"), spaceBefore=16,
                               spaceAfter=8, wordWrap="CJK")
    body_style = ParagraphStyle("Body", fontName=cjk, fontSize=11, leading=17,
                                textColor=HexColor("#2d3748"), spaceAfter=8,
                                wordWrap="CJK")
    meta_style = ParagraphStyle("Meta", fontName=cjk, fontSize=9, leading=12,
                                 textColor=HexColor("#718096"), spaceAfter=10,
                                 wordWrap="CJK")
    italic_style = ParagraphStyle("Italic", parent=body_style, fontName=cjk,
                                   fontSize=10, textColor=HexColor("#4a5568"))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.75 * inch,
                            title="Amazon Listing")

    story = []

    site_code = result.get("site") or ""
    asin = result.get("asin") or ""
    brand = result.get("brand") or ""
    story.append(Paragraph(f"Amazon Listing — {site_code}", title_style))
    story.append(Paragraph(
        f"ASIN: {asin}  |  Brand: {brand}  |  "
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        meta_style))

    if result.get("info_insufficient"):
        story.append(Paragraph(
            f"⚠ 产品信息偏少，当前仅生成了 {result.get('bullet_count')} 条五点；建议补充更多产品特征。",
            italic_style))

    def section(title, *items):
        story.append(Paragraph(title, h1_style))
        for it in items:
            if isinstance(it, str):
                story.append(Paragraph(it.replace("\n", "<br/>"), body_style))
            elif it is None:
                pass
        story.append(Spacer(1, 6))

    section("商品名称 Product Title",
            f"EN: {result.get('product_name') or ''}",
            f"中文: {result.get('product_name_zh') or ''}")

    section("商品亮点 Highlight",
            f"EN: {result.get('highlights') or ''}",
            f"中文: {result.get('highlights_zh') or ''}")

    story.append(Paragraph("五点描述 Bullet Points", h1_style))
    if result.get("bullet_angle"):
        story.append(Paragraph(f"方向: {result['bullet_angle']}", italic_style))
    bullets = result.get("bullets") or []
    bullets_zh = result.get("bullets_zh") or []
    for i, (b, bzh) in enumerate(zip(bullets, bullets_zh), 1):
        story.append(Paragraph(f"<b>{i}.</b> {b}", body_style))
        if bzh:
            story.append(Paragraph(f"<i>{bzh}</i>", italic_style))
    story.append(Spacer(1, 6))

    cov = result.get("keyword_coverage")
    if cov:
        story.append(Paragraph("关键词覆盖", h1_style))
        story.append(Paragraph(f"覆盖 {cov.get('hit')}/{cov.get('total')}", body_style))
        if cov.get("missing"):
            story.append(Paragraph(
                "未覆盖: " + ", ".join(cov["missing"][:15]), body_style))
        story.append(Spacer(1, 6))

    rv = result.get("review") or {}
    if rv.get("verdict") and rv["verdict"] != "skipped":
        story.append(Paragraph("合规评估（amazon-ops-optimizer）", h1_style))
        labels = {"ready": "通过", "fix": "建议修改", "blocked": "存在阻断项"}
        label = labels.get(rv["verdict"], rv["verdict"])
        after = rv.get("after_verdict")
        story.append(Paragraph(
            f"结论: <b>{label}</b>"
            + (f"  （调整后: {labels.get(after, after)}）" if after else ""),
            body_style))

        issues = rv.get("issues") or []
        if issues:
            table_data = [["级别", "位置", "问题", "建议"]]
            for x in issues:
                table_data.append([
                    str(x.get("level") or ""),
                    str(x.get("location") or ""),
                    str(x.get("problem") or ""),
                    str(x.get("fix") or ""),
                ])
            tbl = Table(table_data, repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), HexColor("#2b6cb0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), white),
                ("FONTNAME", (0, 0), (-1, -1), cjk),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 1), (-1, -1), HexColor("#f7fafc")),
                ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#e2e8f0")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(Spacer(1, 4))
            story.append(tbl)
            story.append(Spacer(1, 4))

    try:
        doc.build(story)
    except Exception as e:
        # 兜底：如果是字体 CID 问题，用 Helvetica 重建一版（中文会成方块）
        buf.seek(0); buf.truncate(0)
        fallback = SimpleDocTemplate(buf, pagesize=letter,
                                     leftMargin=0.75*inch, rightMargin=0.75*inch,
                                     topMargin=0.75*inch, bottomMargin=0.75*inch)
        fb_story = [Paragraph("<b>PDF 生成时出现字体错误，以下是纯英文版本：</b>",
                              body_style)]
        for el in story[1:]:
            if isinstance(el, Paragraph):
                fb_story.append(Paragraph(el.text.replace("<[^>]*>", ""), body_style))
            elif isinstance(el, Spacer):
                fb_story.append(el)
        fallback.build(fb_story)
    return buf.getvalue()


# ================== 统一入口 ==================

def export(result: dict, fmt: str) -> tuple[bytes, str, str]:
    """返回 (bytes, content_type, suggested_filename)。fmt 大小写不敏感。"""
    fmt = fmt.lower()
    stem = (result.get("asin") or result.get("brand") or "listing_gen").replace("/", "_")
    if fmt == "md" or fmt == "markdown":
        return to_markdown(result), "text/markdown; charset=utf-8", f"{stem}_listing.md"
    if fmt == "docx" or fmt == "word":
        return to_docx(result), (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"), \
               f"{stem}_listing.docx"
    if fmt == "pdf":
        return to_pdf(result), "application/pdf", f"{stem}_listing.pdf"
    raise ValueError(f"不支持的格式 {fmt}，可选 md / docx / pdf")
