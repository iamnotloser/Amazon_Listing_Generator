"""CLI 入口：参数解析后交给 listing_gen.pipeline。

用法：
  python -m listing_gen.main --asin B0FCF8K9CG --brand HOKEMOM --auto
"""
import argparse
import os
import sys

from dotenv import load_dotenv

from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

from listing_gen import pipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asin", default="")
    ap.add_argument("--site", default=os.getenv("SITE", "US"))
    ap.add_argument("--brand", default="")
    ap.add_argument("--extra", default="", help="补充产品信息")
    ap.add_argument("--drop-spec", default="", help="剔除错误的规格字段，逗号分隔")
    ap.add_argument("--xiyou-top", type=int, default=50, help="西柚反查取前 N 个词")
    ap.add_argument("--no-xiyou", action="store_true", help="跳过西柚 MCP")
    ap.add_argument("--no-xiyou-enrich", action="store_true", help="不补搜索量/ABA 排名")
    ap.add_argument("--competitors", default="", help="竞品 ASIN，逗号分隔")
    ap.add_argument("--search-keyword", default="sink caddy with drying stone")
    ap.add_argument("--search-pages", type=int, default=5)
    ap.add_argument("--bsr-max", type=int, default=100)
    ap.add_argument("--auto", action="store_true", help="非交互，自动选第一个方案")
    args = ap.parse_args()

    if not args.asin:
        sys.exit("❌ CLI 模式必须提供 --asin；手动产品信息请用 Web 表单")

    params = {
        "asin": args.asin,
        "site": args.site,
        "brand": args.brand,
        "extra": args.extra,
        "drop_spec": args.drop_spec,
        "xiyou_top": args.xiyou_top,
        "no_xiyou": args.no_xiyou,
        "no_xiyou_enrich": args.no_xiyou_enrich,
        "competitors": [a.strip() for a in args.competitors.split(",") if a.strip()],
        "auto_competitors": not args.competitors,
        "search_keyword": args.search_keyword,
        "search_pages": args.search_pages,
        "bsr_max": args.bsr_max,
    }
    result = pipeline.generate(params, progress=print)
    print(f"\n✅ 商品名称：{result['product_name']}")
    print(f"   商品亮点：{result['highlights']}")
    print(f"   五行要点：{len(result['bullets'])} 条")
    print(f"   输出：{result.get('output_stem')}_listing_*.txt / .json")


if __name__ == "__main__":
    main()
