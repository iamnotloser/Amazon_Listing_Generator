"""Listing 抓取层：Playwright 真实浏览器，绕过 Amazon 反爬

站点化与稳定性要点：
- locale / 货币 / 语言参数全部跟随站点，不再固定 en_US + USD
- 每个页面用独立浏览器上下文打开：Amazon 对同一会话的连续请求会降级成
  「只有导航栏和页脚」的空壳页，换上下文可稳定拿到完整详情页
- 被拦截（202 / 503 / Service Unavailable）时先访问首页预热再重试
- 自动关闭英欧站的 cookie 同意拦截页
- 缓存按站点域名隔离，且只缓存解析成功的数据
"""
import json
import random
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CACHE_DIR = Path(__file__).parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BLOCK_MARKERS = ("Service Unavailable", "Enter the characters you see below",
                 "not a robot", "api-services-support@amazon.com")


def _clean(s: str) -> str:
    return re.sub(r"[\u200e\u200f\u00a0]", " ", s or "").strip()


def _host(domain: str) -> str:
    host = re.sub(r"^https?://", "", domain or "").strip("/")
    return host.replace("www.", "")


def _cache_dir_for(domain: str) -> Path:
    d = CACHE_DIR / _host(domain)
    d.mkdir(parents=True, exist_ok=True)
    return d


class ListingFetcher:
    def __init__(self, site_domain: str, locale: str = "en-US",
                 timezone_id: str = "America/New_York", currency: str = "USD",
                 headless: bool = True, attempts: int = 3):
        self.site_domain = site_domain.rstrip("/")
        self.locale = locale
        self.lang_tag = locale.replace("-", "_")     # en-GB -> en_GB
        self.lang = locale.split("-")[0]             # en-GB -> en
        self.currency = (currency or "USD").upper()
        self.timezone_id = timezone_id
        self.headless = headless
        self.attempts = max(attempts, 1)
        self.cache_dir = _cache_dir_for(self.site_domain)
        self._pw = None
        self._browser = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"],
        )
        return self

    def __exit__(self, *exc):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _new_context(self):
        ctx = self._browser.new_context(
            user_agent=UA,
            viewport={"width": 1440, "height": 900},
            locale=self.locale,
            timezone_id=self.timezone_id,
            extra_http_headers={
                "Accept-Language": f"{self.locale},{self.lang};q=0.9,en;q=0.8",
                "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
                "sec-ch-ua-platform": '"Windows"',
                "sec-ch-ua-mobile": "?0",
            },
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
            f"Object.defineProperty(navigator,'languages',{{get:()=>['{self.locale}','{self.lang}']}});"
        )
        # 站点级货币与语言，避免出口 IP 导致显示其他货币
        domain = _host(self.site_domain)
        try:
            ctx.add_cookies([
                {"name": "i18n-prefs", "value": self.currency, "domain": f".{domain}", "path": "/"},
                {"name": "lc-main", "value": self.lang_tag, "domain": f".{domain}", "path": "/"},
            ])
        except Exception:
            pass
        return ctx

    # ---------- 反爬处理 ----------
    def _dismiss_consent(self, page) -> bool:
        """关闭「Select your cookie preferences」拦截页。"""
        for sel in ("#sp-cc-accept", "input#sp-cc-accept", "button#sp-cc-accept",
                    "[data-action-type='ACCEPT']", "button:has-text('Accept All')"):
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click(timeout=3000)
                    page.wait_for_timeout(random.randint(800, 1500))
                    return True
            except Exception:
                continue
        return False

    def _warmup(self, page):
        """访问站点首页建立会话，规避直接请求内页时的 503。"""
        try:
            page.goto(self.site_domain + "/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(random.randint(2000, 3200))
            self._dismiss_consent(page)
        except Exception:
            pass

    @staticmethod
    def _blocked(status, title: str, body: str) -> bool:
        if status in (202, 503) or status is None:
            return True
        text = f"{title}\n{body}"
        return any(m in text for m in BLOCK_MARKERS)

    def _goto(self, page, url: str):
        """加载页面；被拦截时预热首页后重试，验证码直接抛错。"""
        last = None
        for i in range(self.attempts):
            if i:
                self._warmup(page)
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(random.randint(1200, 2400))
            status = resp.status if resp else None
            title = page.title() or ""
            body = page.inner_text("body")[:1200]
            if "Enter the characters you see below" in body or "not a robot" in body:
                raise RuntimeError(f"命中验证码：{url}")
            if not self._blocked(status, title, body):
                return resp
            last = f"HTTP {status} / {title[:40]}"
            page.wait_for_timeout(random.randint(1500, 3000))
        raise RuntimeError(f"页面被拦截（{last}）：{url}")

    @staticmethod
    def _wait_title(page, timeout: int = 15000) -> bool:
        """详情页标题区是延迟渲染的，等 #productTitle 出现。"""
        try:
            page.wait_for_selector("#productTitle", timeout=timeout, state="attached")
            return True
        except Exception:
            return False

    def _open(self, url: str):
        """用独立上下文打开页面，返回 (ctx, page, resp)。"""
        ctx = self._new_context()
        page = ctx.new_page()
        try:
            resp = self._goto(page, url)
        except Exception:
            page.close()
            ctx.close()
            raise
        return ctx, page, resp

    # ---------- 单个 ASIN ----------
    def fetch(self, asin: str, use_cache: bool = True) -> dict:
        cache_file = self.cache_dir / f"{asin}.json"
        if use_cache and cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if cached.get("title"):
                    return cached
            except Exception:
                pass

        url = f"{self.site_domain}/dp/{asin}?language={self.lang_tag}"
        last = None
        for attempt in range(self.attempts):
            ctx = page = None
            try:
                ctx, page, resp = self._open(url)
                self._dismiss_consent(page)
                if not self._wait_title(page):
                    # 会话被降级成空壳页时，换下一个上下文重试
                    last = "详情页降级（未出现商品标题区）"
                    raise RuntimeError(last)
                page.wait_for_timeout(random.randint(500, 1200))

                data = {"asin": asin, "url": url, "http_status": resp.status if resp else None}

                def txt(sel):
                    el = page.query_selector(sel)
                    return _clean(el.inner_text()) if el else None

                data["title"] = txt("#productTitle")
                data["byline"] = txt("#bylineInfo")
                data["price"] = (txt("#corePrice_feature_div .a-offscreen")
                                 or txt("#corePriceDisplay_desktop_feature_div .a-offscreen")
                                 or txt("#apex_desktop .a-offscreen")
                                 or txt(".a-price .a-offscreen")
                                 or txt("#price_inside_buybox")
                                 or txt("#newBuyBoxPrice")
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

                if not data["title"]:
                    raise RuntimeError(f"未解析到标题（ASIN 可能无效）：{asin}")

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
            except RuntimeError as e:
                last = str(e)
            finally:
                if page:
                    page.close()
                if ctx:
                    ctx.close()
            time.sleep(random.uniform(1.5, 2.5) * (attempt + 1))
        raise RuntimeError(f"抓取失败：{last or '未知原因'}")

    # ---------- 搜索页发现竞品 ----------
    def search_competitors(self, keyword: str, limit: int = 6, pages: int = 1) -> list:
        out, seen = [], set()
        for p in range(1, max(pages, 1) + 1):
            url = (f"{self.site_domain}/s?k={quote_plus(keyword)}"
                   f"&page={p}&language={self.lang_tag}")
            ctx = page = None
            try:
                ctx, page, _ = self._open(url)
                self._dismiss_consent(page)
                try:
                    page.wait_for_selector(
                        '[data-component-type="s-search-result"], div.s-result-item[data-asin]',
                        timeout=12000, state="attached")
                except Exception:
                    pass
                cards = page.eval_on_selector_all(
                    '[data-component-type="s-search-result"]',
                    """els => els.map(e => ({
                        asin: e.getAttribute('data-asin'),
                        sponsored: !!e.querySelector('.puis-sponsored-label-text, [aria-label="Sponsored"], [aria-label="赞助"]'),
                        title: (e.querySelector('h2')?.innerText || '').trim(),
                        price: e.querySelector('.a-price .a-offscreen')?.innerText || ''
                    }))"""
                )
                if not cards:
                    # 部分站点/布局没有 data-component-type，退回通用卡片选择器
                    cards = page.eval_on_selector_all(
                        "div.s-result-item[data-asin]",
                        """els => els.map(e => ({
                            asin: e.getAttribute('data-asin'),
                            sponsored: !!e.querySelector('.puis-sponsored-label-text'),
                            title: (e.querySelector('h2')?.innerText || '').trim(),
                            price: e.querySelector('.a-price .a-offscreen')?.innerText || ''
                        }))"""
                    )
            except RuntimeError:
                cards = []
            finally:
                if page:
                    page.close()
                if ctx:
                    ctx.close()

            for c in cards:
                if not c["asin"] or c["sponsored"] or not c["title"]:
                    continue
                if c["asin"] in seen:
                    continue
                seen.add(c["asin"])
                out.append(c)
                if len(out) >= limit:
                    return out
            time.sleep(random.uniform(1.0, 2.0))
        return out
