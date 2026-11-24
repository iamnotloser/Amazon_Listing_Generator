"""西柚 MCP 关键词链路测试：反查 → 补指标 → 与竞品词根合并（不抓 Amazon、不调 LLM）。

用法：python test_xiyou.py [ASIN] [站点]
"""
import sys

from listing_gen import xiyou
from listing_gen.keyword_analyzer import merge_with_xiyou

asin = sys.argv[1] if len(sys.argv) > 1 else "B0FCF8K9CG"
site = sys.argv[2] if len(sys.argv) > 2 else "US"

print(f"▶ 反查 {asin} / {site}")
rows = xiyou.fetch_asin_keywords(asin, site, top=20)
print(f"  拿到 {len(rows)} 个词")

info = xiyou.enrich_keyword_info([r["keyword"] for r in rows], site)
for r in rows:
    r.update(info.get(r["keyword"].lower(), {}))
print(f"  补指标成功 {sum(1 for r in rows if r.get('search_volume'))} 个")

# 用假竞品词根验证合并逻辑（真实流程里来自 analyze()）
fake_analysis = {
    "top_words": [
        {"keyword": "sink caddy", "in_titles": 5, "score": 10.0},
        {"keyword": "organizer", "in_titles": 4, "score": 8.0},
    ]
}
pool = merge_with_xiyou(fake_analysis, rows, top_n=10)

print("\n▶ 合并后 Top 10")
print(f"{'keyword':42} {'搜索量':>8} {'ABA':>8} {'流量':>8} {'自然排名':>8}  source")
for p in pool:
    print(f"{p['keyword'][:42]:42} {str(p['search_volume']):>8} {str(p['aba_rank']):>8} "
          f"{str(p['traffic']):>8} {str(p['organic_rank']):>8}  {p['source']}")

assert any(p["source"] == "西柚反查" for p in pool), "合并结果缺少西柚来源"
assert any(p["search_volume"] for p in pool), "未拿到搜索量"
print("\n✅ 链路通过")
