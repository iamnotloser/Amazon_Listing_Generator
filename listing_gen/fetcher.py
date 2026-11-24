"""Listing 抓取层：Playwright 真实浏览器，绕过 Amazon 反爬"""
import json
import random
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CACHE_DIR = Path(__file__).parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _clean(s: str) -> str:
    return re.sub(r"[\u200e\u200f\u00a0]", " ", s or "").strip()


class ListingFetcher:
    def __init__(self, site_domain: str, locale: str = "en-US",
                 timezone_id: str = "America/New_York", headless: bool = True):
        self.site_domain = site_domain.rstrip("/")
        self.locale = locale
        self.timezone_id = timezone_id
        self.headless = headless
        self._pw = None
        self._browser = None
        self._ctx = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._ctx = self._browser.new_context(
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale=self.locale,
            timezone_id=self.timezone_id,
            extra_http_headers={
                "Accept-Language": f"{self.locale},en;q=0.9",
                "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
                "sec-ch-ua-platform": '"Windows"',
                "sec-ch-ua-mobile": "?0",
            },
        )
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
            "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});"
        )
        # 关键：强制货币与语言，避免出口 IP 导致显示 EUR/GBP
        domain = self.site_domain.replace("https://", "")
        try:
            self._ctx.add_cookies([
                {"name": "i18n-prefs", "value": "USD", "domain": f".{domain.replace('www.', '')}", "path": "/"},
                {"name": "lc-main", "value": "en_US", "domain": f".{domain.replace('www.', '')}", "path": "/"},
            ])
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        try:
            if self._ctx:
                self._ctx.close()
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    # ---------- 单个 ASIN ----------
    def fetch(self, asin: str, use_cache: bool = True) -> dict:
        cache_file = CACHE_DIR / f"{asin}.json"
        if use_cache and cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))

        url = f"{self.site_domain}/dp/{asin}?language={self.locale.split('-')[0]}_US"
        page = self._ctx.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(random.randint(1200, 2500))
            body = page.inner_text("body")[:800]
            if "Enter the characters you see below" in body or "not a robot" in body:
                raise RuntimeError(f"命中验证码: {asin}")

            data = {"asin": asin, "url": url, "http_status": resp.status if resp else None}

            def txt(sel):
                el = page.query_selector(sel)
                return _clean(el.inner_text()) if el else None

            data["title"] = txt("#productTitle")
            data["byline"] = txt("#bylineInfo")
            data["price"] = (txt("#corePrice_feature_div .a-offscreen")
                             or txt(".a-price .a-offscreen")
                             or txt("#priceblock_ourprice"))
            data["rating"] = page.get_attribute("#acrPopover", "title") if page.query_selector("#acrPopover") else None
            data["review_count"] = txt("#acrCustomerReviewText")

            data["bullets"] = [
                _clean(t) for t in page.eval_on_selector_all(
                    "#feature-bullets li span.a-list-item",
                    "els => els.map(e => e.innerText)")
                if len(_clean(t)) > 10
            ][:8]

            data["breadcrumbs"] = page.eval_on_selector_all(
                "#wayfinding-breadcrumbs_feature_div ul li a",
                "els => els.map(e => e.innerText.trim())")

            # 规格：商品概览表 + 详情表，按行文本解析（兼容 th/td 与纯文本两种结构）
            specs = {}
            for sel in ["#productOverview_feature_div tr",
                        "#productDetails_techSpec_section_1 tr",
                        "#productDetails_detailBullets_sections1 tr",
                        "table.a-keyvalue.prodDetTable tr"]:
                for row in page.eval_on_selector_all(
                        sel, "els => els.map(e => e.innerText)"):
                    parts = [p.strip() for p in _clean(row).split("\t") if p.strip()]
                    if len(parts) >= 2 and len(parts[0]) < 60:
                        specs[parts[0]] = " ".join(parts[1:])[:200]
            for t in page.eval_on_selector_all(
                    "#detailBullets_feature_div li", "els => els.map(e => e.innerText)"):
                t = _clean(t)
                if ":" in t and len(t) < 200 and "Customer" not in t:
                    k, v = t.split(":", 1)
                    specs[_clean(k)] = _clean(v)
            data["specs"] = specs

            for t in page.eval_on_selector_all(
                    "#detailBullets_feature_div li", "els => els.map(e => e.innerText)"):
                if "Best Sellers Rank" in t:
                    data["bsr_raw"] = _clean(t)
                    break

            data["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return data
        finally:
            page.close()

    # ---------- 搜索页发现竞品 ----------
    def search_competitors(self, keyword: str, limit: int = 6, pages: int = 1) -> list:
        out, seen = [], set()
        page = self._ctx.new_page()
        try:
            for p in range(1, max(pages, 1) + 1):
                url = f"{self.site_domain}/s?k={keyword.replace(' ', '+')}&page={p}"
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(random.randint(1500, 2500))
                cards = page.eval_on_selector_all(
                    '[data-component-type="s-search-result"]',
                    """els => els.map(e => ({
                        asin: e.getAttribute('data-asin'),
                        sponsored: !!e.querySelector('.puis-sponsored-label-text, [aria-label="Sponsored"]'),
                        title: e.querySelector('h2')?.innerText.trim() || '',
                        price: e.querySelector('.a-price .a-offscreen')?.innerText || ''
                    }))"""
                )
                for c in cards:
                    if not c["asin"] or c["sponsored"] or not c["title"]:
                        continue
                    if c["asin"] in seen:
                        continue
                    seen.add(c["asin"])
                    out.append(c)
                    if len(out) >= limit:
                        return out
            return out
        finally:
            page.close()
