"""Listing 生成器 Web 服务（FastAPI）。

启动：
    python web/app.py
然后浏览器打开 http://127.0.0.1:8000
"""
import os
import sys
import threading
import time
import uuid
import queue
import zipfile
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from listing_gen import pipeline
from listing_gen.exporters import export

app = FastAPI(title="Amazon Listing 生成器")
JOBS = {}
LOCK = threading.Lock()
STATIC = Path(__file__).resolve().parent / "static"
JOB_QUEUE = queue.Queue()


class Payload(BaseModel):
    mode: str = "asin"
    asin: str = ""
    brand: str = ""
    site: str = "US"
    competitors: str = ""
    auto_competitors: bool = True
    search_keyword: str = "sink caddy with drying stone"
    search_keywords: str = ""
    search_pages: int = 5
    bsr_max: int = 100
    extra: str = ""
    drop_spec: str = ""
    seed_keywords: str = ""
    no_xiyou: bool = False
    review: bool = True
    manual_title: str = ""
    manual_price: str = ""
    manual_breadcrumbs: str = ""
    manual_bullets: str = ""
    manual_specs: str = ""


def _split_lines(text):
    return [x.strip() for x in (text or "").replace("\r", "").split("\n") if x.strip()]


def _parse_specs(text):
    out = {}
    for line in _split_lines(text):
        for sep in ("：", ":"):
            if sep in line:
                k, v = line.split(sep, 1)
                if k.strip():
                    out[k.strip()] = v.strip()
                break
    return out


def _build_params(p: Payload):
    manual = None
    if p.mode == "manual":
        manual = {
            "asin": p.asin.strip(),
            "brand": p.brand.strip(),
            "title": p.manual_title.strip(),
            "price": p.manual_price.strip(),
            "breadcrumbs": p.manual_breadcrumbs.strip(),
            "bullets": _split_lines(p.manual_bullets),
            "specs": _parse_specs(p.manual_specs),
        }
    competitors = [x.strip() for x in (p.competitors or "").replace("，", ",").split(",") if x.strip()]
    seed = [x.strip() for x in (p.seed_keywords or "").replace("，", ",").split(",") if x.strip()]
    kws = [x.strip() for x in (p.search_keywords or "").replace("，", ",").split(",") if x.strip()]
    if not kws and p.search_keyword.strip():
        kws = [p.search_keyword.strip()]
    return {
        "asin": p.asin.strip(),
        "site": (p.site or "US").upper(),
        "brand": p.brand.strip(),
        "extra": p.extra.strip(),
        "drop_spec": p.drop_spec.strip(),
        "competitors": competitors,
        "auto_competitors": bool(p.auto_competitors) and not competitors,
        "search_keyword": kws[0] if kws else "",
        "search_keywords": kws,
        "search_pages": max(1, min(int(p.search_pages or 5), 10)),
        "bsr_max": max(1, min(int(p.bsr_max or 100), 100000)),
        "seed_keywords": seed,
        "no_xiyou": bool(p.no_xiyou),
        "review": bool(p.review),
        "manual": manual,
    }


def _worker_loop():
    """单消费者队列：同一时间只跑一个生成任务，避免抢资源和额度。"""
    while True:
        job_id, params = JOB_QUEUE.get()
        with LOCK:
            job = JOBS.get(job_id)
            if job is None:
                JOB_QUEUE.task_done()
                continue
            job["status"] = "running"
            job["logs"].append("开始执行")

        def progress(msg):
            with LOCK:
                cur = JOBS.get(job_id)
                if cur is not None:
                    cur["logs"].append(str(msg))

        try:
            result = pipeline.generate(params, progress=progress)
            with LOCK:
                JOBS[job_id]["status"] = "done"
                JOBS[job_id]["result"] = result
        except Exception as e:
            with LOCK:
                JOBS[job_id]["status"] = "error"
                JOBS[job_id]["error"] = f"{type(e).__name__}: {e}"
        finally:
            with LOCK:
                JOBS[job_id]["finished_at"] = time.time()
            JOB_QUEUE.task_done()


threading.Thread(target=_worker_loop, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/favicon.svg")
def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/review")
def review_page():
    return FileResponse(STATIC / "review.html", headers={"Cache-Control": "no-store"})


@app.post("/api/generate")
def create_job(payload: Payload):
    if payload.mode == "asin" and not payload.asin.strip():
        raise HTTPException(400, "ASIN 模式需要填写 ASIN")
    if payload.mode == "manual" and not payload.manual_title.strip():
        raise HTTPException(400, "手动模式至少填写产品名称")
    job_id = uuid.uuid4().hex[:12]
    params = _build_params(payload)
    with LOCK:
        JOBS[job_id] = {"status": "queued", "logs": ["已进入队列，等待执行"],
                        "result": None, "error": None, "params": params,
                        "created_at": time.time()}
    JOB_QUEUE.put((job_id, params))
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return {
            "status": job["status"],
            "logs": job["logs"],
            "result": job["result"],
            "error": job["error"],
        }


@app.get("/api/jobs/{job_id}/files")
def list_files(job_id: str):
    """列出该任务生成的所有输出文件（json / EN.txt / 对照.txt）。"""
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
    stem = (job.get("result") or {}).get("output_stem")
    if not stem:
        raise HTTPException(404, "任务未完成或没有输出文件")
    out_dir = ROOT / "output"
    files = []
    for suf in ("_listing.json", "_listing_EN.txt", "_listing_对照.txt"):
        fp = out_dir / f"{stem}{suf}"
        if fp.exists():
            files.append({
                "name": fp.name,
                "size": fp.stat().st_size,
                "url": f"/api/jobs/{job_id}/download/{fp.name}",
            })
    if not files:
        raise HTTPException(404, "输出文件已不存在")
    return {"stem": stem, "files": files,
            "zip_url": f"/api/jobs/{job_id}/download/listing_bundle.zip"}


@app.get("/api/jobs/{job_id}/download/{filename}")
def download_file(job_id: str, filename: str):
    """下载：支持 md / docx / pdf 格式即时生成，也支持已有的 json / EN.txt / 对照.txt / listing_bundle.zip。"""
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
    result = job.get("result") or {}

    fmt = filename.lower()

    # —— 格式化为 md / docx / pdf 时即时生成 ——
    if fmt in ("listing.md", "listing.docx", "listing.pdf"):
        target = fmt.split(".")[-1]   # md / docx / pdf
        if not result:
            raise HTTPException(404, "任务未完成或 result 缺失")
        try:
            body, ctype, fname = export(result, target)
        except Exception as e:
            raise HTTPException(500, f"导出 {target} 失败: {e}")
        from fastapi.responses import Response
        return Response(content=body, media_type=ctype,
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    # —— 原有 zip / json / txt 文件 ——
    stem = result.get("output_stem")
    if not stem:
        raise HTTPException(404, "任务未完成或没有输出文件")
    out_dir = ROOT / "output"

    if filename == "listing_bundle.zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for suf in ("_listing.json", "_listing_EN.txt", "_listing_对照.txt"):
                fp = out_dir / f"{stem}{suf}"
                if fp.exists():
                    zf.write(fp, fp.name)
            review = result.get("review") or {}
            if review.get("issues") or review.get("verdict"):
                rpt = io.StringIO()
                rpt.write(f"合规评估报告\n{'='*40}\n")
                rpt.write(f"结论：{review.get('verdict', 'N/A')}")
                if review.get("after_verdict"):
                    rpt.write(f"（调整后：{review['after_verdict']}）")
                rpt.write("\n\n")
                for x in review.get("issues", []):
                    rpt.write(f"[{x.get('level')}] {x.get('location')}: {x.get('problem')}\n")
                    if x.get("fix"):
                        rpt.write(f"  建议：{x['fix']}\n")
                    rpt.write("\n")
                zf.writestr(f"{stem}_合规评估.txt", rpt.getvalue())
        buf.seek(0)
        return StreamingResponse(
            buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{stem}_bundle.zip"'})

    # 防止路径穿越
    safe = filename.replace("..", "").replace("/", "").replace("\\", "")
    allowed = {f"{stem}_listing.json",
               f"{stem}_listing_EN.txt",
               f"{stem}_listing_对照.txt"}
    if safe not in allowed:
        raise HTTPException(404, "文件不存在")
    fp = out_dir / safe
    if not fp.exists():
        raise HTTPException(404, "文件已不存在")
    return FileResponse(fp, filename=safe, media_type="text/plain; charset=utf-8")


if __name__ == "__main__":
    uvicorn.run(app,
                host=os.getenv("HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "8000")),
                log_level="info")
