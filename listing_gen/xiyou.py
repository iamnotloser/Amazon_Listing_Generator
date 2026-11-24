"""西柚 MCP（xydc-mcp）客户端：ASIN 反查关键词。

走 MCP Streamable HTTP，直接调用远程工具，不依赖 IDE 是否挂载。
凭证只从环境变量读取：
    XIYOU_MCP_URL   默认 https://mcp.xydc.com/mcp
    XIYOU_MCP_TOKEN 西柚后台生成的 Bearer token
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


class MCPError(RuntimeError):
    pass


class XiyouClient:
    def __init__(self, url=None, token=None, timeout=120):
        self.url = (url or os.getenv("XIYOU_MCP_URL") or "https://mcp.xydc.com/mcp").strip()
        raw = (token if token is not None else os.getenv("XIYOU_MCP_TOKEN") or "").strip()
        if not raw:
            raise MCPError("缺少 XIYOU_MCP_TOKEN，请在 .env 中配置西柚 MCP token")
        self.token = raw if raw.lower().startswith("bearer ") else f"Bearer {raw}"
        self.timeout = timeout
        self.session_id = None
        self._connected = False

    # ---------- 传输层 ----------
    def _post(self, payload):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": self.token,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                return resp.status, resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            raise MCPError(f"HTTP {e.code}: {body[:300]}")
        except urllib.error.URLError as e:
            raise MCPError(f"连接失败: {e.reason}")

    @staticmethod
    def _parse(body):
        body = (body or "").strip()
        if not body:
            return []
        if body.startswith("{"):
            try:
                return [json.loads(body)]
            except json.JSONDecodeError:
                return []
        msgs = []
        for block in body.split("\n\n"):
            data = "\n".join(l[5:].strip() for l in block.splitlines() if l.startswith("data:"))
            if data:
                try:
                    msgs.append(json.loads(data))
                except json.JSONDecodeError:
                    continue
        return msgs

    def _rpc(self, method, params=None, notify=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            payload["id"] = 1
        _, body = self._post(payload)
        if notify:
            return []
        msgs = self._parse(body)
        for m in msgs:
            if m.get("error"):
                raise MCPError(f"{method} 失败: {json.dumps(m['error'], ensure_ascii=False)[:300]}")
        return msgs

    # ---------- MCP 生命周期 ----------
    def connect(self):
        if self._connected:
            return self
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "listing_gen", "version": "1.0"},
        })
        self._rpc("notifications/initialized", {}, notify=True)
        self._connected = True
        return self

    def call_tool(self, name, arguments):
        """调用工具并解出 content[0].text 里的 JSON 业务数据。"""
        self.connect()
        msgs = self._rpc("tools/call", {"name": name, "arguments": arguments})
        for m in msgs:
            for c in (m.get("result") or {}).get("content") or []:
                text = c.get("text") if isinstance(c, dict) else str(c)
                if not text:
                    continue
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    raise MCPError(f"{name} 返回非 JSON: {text[:300]}")
        raise MCPError(f"{name} 无返回内容")

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        return False


# ---------- 业务封装 ----------
def _rank_of(ranks, position):
    for r in ranks or []:
        if r.get("position") == position:
            return r.get("totalRank") or r.get("pageRank")
    return None


def fetch_asin_keywords(asin, country="US", top=50, client=None):
    """ASIN 反查关键词（近 7 天），按总流量降序，返回归一化字典列表。"""
    own = client is None
    cli = client or XiyouClient()
    try:
        data = cli.call_tool("get_asin_keywords", {
            "asin": asin,
            "country": country,
            "page": 1,
            "page_size": min(max(int(top), 1), 10000),
            "sort_field": "traffic",
            "sort_order": "desc",
            "intent_summary": "反查该 ASIN 的关键词与流量结构，用于生成 Listing 关键词池。",
            "user_task": f"反查 ASIN {asin}（{country} 站）的关键词，用于 Listing 标题和五点优化。",
        }).get("data") or {}
        rows = []
        for r in data.get("list") or []:
            traffic = ((r.get("trafficSummary") or {}).get("traffic")) or {}
            ranks = r.get("ranks") or []
            rows.append({
                "keyword": (r.get("searchTerm") or "").strip(),
                "keyword_zh": r.get("searchTermZh"),
                "traffic": traffic.get("total"),
                "organic_traffic": traffic.get("organic"),
                "ad_traffic": traffic.get("advertising"),
                "organic_rank": _rank_of(ranks, "or"),
                "ad_rank": _rank_of(ranks, "sp"),
                "search_volume": None,
                "aba_rank": None,
            })
        return [x for x in rows if x["keyword"]]
    finally:
        if own:
            cli.__exit__(None, None, None)


def enrich_keyword_info(keywords, country="US", batch=100, client=None):
    """用 get_keyword_info 补周搜索量/ABA 排名/竞价，返回 {小写词: {...}}。"""
    own = client is None
    cli = client or XiyouClient()
    out = {}
    try:
        kws = [k for k in dict.fromkeys(keywords) if k]
        for i in range(0, len(kws), batch):
            chunk = kws[i:i + batch]
            data = cli.call_tool("get_keyword_info", {
                "keywords": chunk,
                "country": country,
                "intent_summary": "补充关键词的周搜索量与 ABA 排名，用于关键词池打分排序。",
                "user_task": f"为 {country} 站关键词池补充搜索量、ABA 排名和竞价指标。",
            }).get("data") or {}
            for item in data.get("list") or []:
                kw = (item.get("searchTerm") or "").strip().lower()
                if not kw:
                    continue
                aba = item.get("abaReport") or {}
                cpc = item.get("costPerClick") or {}
                out[kw] = {
                    "search_volume": aba.get("weeklySearchVolume"),
                    "aba_rank": aba.get("searchFrequencyRank"),
                    "cpc": cpc.get("value"),
                    "competitive_difficulty": item.get("competitiveDifficulty"),
                }
        return out
    finally:
        if own:
            cli.__exit__(None, None, None)


def fetch_asin_bsr(asin, country="US", client=None):
    """查单个 ASIN 的 BSR，返回主类目排名（leaf category 优先）及各类目明细。"""
    own = client is None
    cli = client or XiyouClient()
    try:
        data = cli.call_tool("get_asin_bsr", {
            "asin": asin,
            "country": country,
            "intent_summary": "校验候选竞品的 BSR 排名，用于筛选头部竞品。",
            "user_task": f"校验 ASIN {asin}（{country} 站）的类目 BSR 排名。",
        }).get("data") or {}
        tree = data.get("amzCategoryTree") or {}
        ranks = data.get("classificationRanks") or []
        by_id = {r.get("classificationId"): r for r in ranks}
        primary = by_id.get(tree.get("categoryId"))
        if primary is None and ranks:
            primary = min(ranks, key=lambda r: r.get("rank") or 10 ** 9)
        tree_ids = [p.get("categoryId") for p in tree.get("path") or []]
        if tree.get("categoryId"):
            tree_ids.append(tree.get("categoryId"))
        return {
            "asin": asin,
            "bsr_rank": primary.get("rank") if primary else None,
            "bsr_category": (primary or {}).get("displayName"),
            "leaf_id": tree.get("categoryId"),
            "tree_ids": [x for x in tree_ids if x],
            "ranks": [{"id": r.get("classificationId"),
                       "category": r.get("displayName"),
                       "rank": r.get("rank")} for r in ranks],
        }
    finally:
        if own:
            cli.__exit__(None, None, None)


def filter_by_bsr(asins, country="US", max_rank=50, category_ids=None, on_progress=None):
    """逐个查 BSR，保留排名 <= max_rank 的 ASIN，按排名升序返回。

    category_ids 非空时，只认落在这些类目（我的 ASIN 类目树）里的排名。
    为空时（手动模式没有 ASIN），改用每个候选自己的叶子类目排名，
    再按叶子类目聚类，只保留候选最多的那个类目，避免串类目。
    返回 (passed, rejected, errors)。
    """
    cli = XiyouClient()
    infos, errors = [], []
    target = set(category_ids or [])
    try:
        for i, asin in enumerate(asins, 1):
            try:
                info = fetch_asin_bsr(asin, country, client=cli)
            except Exception as e:
                errors.append({"asin": asin, "error": str(e)})
                if on_progress:
                    on_progress(i, asin, None, "error")
                continue
            ranks = info.get("ranks") or []
            if target:
                matched = [r for r in ranks if r.get("id") in target]
            else:
                leaf = [r for r in ranks if r.get("id") == info.get("leaf_id")]
                matched = leaf or ranks
            best = min((r for r in matched if r.get("rank")), key=lambda r: r["rank"], default=None)
            info["matched_rank"] = best["rank"] if best else None
            info["matched_category"] = best["category"] if best else None
            infos.append(info)
            if on_progress:
                on_progress(i, asin, info["matched_rank"], "checked")
    finally:
        cli.__exit__(None, None, None)

    dominant = None
    if not target and infos:
        # 手动模式：按叶子类目聚类，取成员最多的类目作为目标类目
        groups = {}
        for x in infos:
            if x.get("leaf_id") and x.get("matched_rank"):
                groups.setdefault(x["leaf_id"], []).append(x)
        if groups:
            dominant = max(groups, key=lambda k: (len(groups[k]), -min(v["matched_rank"] for v in groups[k])))
            name = groups[dominant][0].get("matched_category")
            if on_progress:
                on_progress(0, f"主类目: {name}", len(groups[dominant]), "cluster")
        else:
            dominant = None

    passed, rejected = [], []
    for x in infos:
        same_cat = (x.get("leaf_id") in target) if target else (x.get("leaf_id") == dominant)
        if x.get("matched_rank") and x["matched_rank"] <= max_rank and same_cat:
            passed.append(x)
        else:
            rejected.append(x)
    passed.sort(key=lambda x: x["matched_rank"])
    return passed, rejected, errors
