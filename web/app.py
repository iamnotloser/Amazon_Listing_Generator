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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from listing_gen import pipeline

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


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")), log_level="info")
