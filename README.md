# Amazon Listing Generator

面向亚马逊运营的 Listing 智能生成工具：输入 相关产品ASIN 或手填产品信息，自动完成竞品查找分析、关键词挖掘、标题五点分析，并生成**商品名称 / 商品亮点 / 五点描述**的中外文对照版本。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688)
![Playwright](https://img.shields.io/badge/Playwright-1.40%2B-2EAD33)

## 功能

- **两种输入模式**：按 ASIN 抓取真实 Listing，或手填未上架产品信息（无需浏览器）
- **竞品自动选品**：关键词搜索结果 → 逐个小类 BSR 校验 → 同父体去重 → 排名等距抽样，最终取 5 个竞品
- **关键词池**：西柚 MCP 反查词 + 竞品标题短语 + 词根合并，按搜索量/流量排序，自动过滤品牌词与泛词
- **内容生成**：3 个角度生成标题与亮点，五点按竞品篇幅基准生成，超界自动重写
- **合规检查**：字符数、词数、禁用表述逐项校验
- **中英对照**：所有产出附带中文翻译，方便审核
- **Web UI**：表单 + 实时日志 + 结果卡片 + 竞品数据弹窗，跟随系统深浅色
- **多站点**：US / CA / MX / BR / UK / DE / FR / IT / ES / NL / BE / SE / PL / TR / JP / AU / IN / SG / AE / SA

## 目录结构

```
listing_gen/
  config.py            # 站点配置（域名、语言、拼写、字符上限）
  fetcher.py           # Playwright 抓 Amazon Listing（含本地缓存）
  xiyou.py             # 西柚 MCP 客户端（反查关键词 / BSR / 关键词指标）
  keyword_analyzer.py  # 词根与短语统计、关键词池合并排序
  chains.py            # LLM 调用（标题、五点、翻译、长度重写）
  title_builder.py     # Listing 拼装与合规检查
  pipeline.py          # 完整流程编排（CLI 与 Web 共用）
  main.py              # 命令行入口
web/
  app.py               # FastAPI：任务队列 + API
  static/index.html    # 表单与结果页
start_demo.ps1         # 一键启动后端 + Cloudflare 临时隧道
```

## 快速开始

### 1. 环境

- Python 3.10+
- 可访问 Amazon 与西柚 MCP 的网络环境

```bash
pip install -r listing_gen/requirements.txt
python -m playwright install chromium
```

### 2. 配置

复制 `listing_gen/.env.example` 为 `listing_gen/.env`：

| 变量 | 说明 |
|---|---|
| `MODEL_NAME` | 模型名，如 `deepseek-chat` / `gpt-4o-mini` |
| `LLM_BASE_URL` | OpenAI 兼容服务地址，DeepSeek 填 `https://api.deepseek.com/v1` |
| `LLM_API_KEY` | 模型服务密钥 |
| `XIYOU_MCP_URL` | 西柚 MCP 地址，默认 `https://mcp.xydc.com/mcp` |
| `XIYOU_MCP_TOKEN` | 西柚后台生成的 token |

`.env` 已被 `.gitignore` 排除，请勿提交。

### 3. 启动 Web

```bash
python web/app.py
# http://127.0.0.1:8000
```

需要公网演示时（含临时隧道）：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_demo.ps1
```

脚本会自动启动后端、下载 `cloudflared`、打印公网 URL。关闭窗口即停止。

### 4. 命令行

```bash
python -m listing_gen.main --asin B0XXXXXXXX --brand YOURBRAND ^
  --competitors B0...,B0... --auto
```

常用参数：`--search-pages`（搜索页数）、`--bsr-max`（小类排名上限，默认 100）、`--xiyou-top`（反查词数）、`--no-xiyou`。

## 竞品选品逻辑

```
搜索关键词（可多个）
  → 去重候选（≤80，剔除自己）
  → 逐个查小类 BSR（西柚）
  → 只保留目标叶子类目、排名 ≤100
  → 同父体去重（同类目同排名视为同一链接）
  → 排名等距抽样，取 7 个候选
  → 抓页面时再按「同品牌 + 标题重合度 ≥80%」兜底去重
  → 最终保留 5 个排名分散的竞品
```

## 关键词池逻辑

1. 西柚按 ASIN 反查关键词（带搜索量、ABA 排名、流量）
2. 竞品标题抽取 2–3 词短语，批量补搜索量
3. 合并后按 `搜索量 × 0.002 + 流量 × 0.0004` 排序
4. 单词泛词降权 50%，品牌词（自己 + 竞品 byline）直接剔除

## 注意事项

- 抓取依赖 Amazon 页面结构，遇到验证码或改版需要调整选择器
- 西柚接口按次消耗 credits：反查约 3 credits/次，BSR 校验 2 credits/个
- 自动选品耗时 2–4 分钟，Web 端默认关闭
- 请遵守 Amazon 与数据服务商的使用条款，仅用于自有产品运营

## License

Private project, all rights reserved.
