"""FastAPI 入口：网页、数据接口、定时任务生命周期。"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, scheduler, service
from .config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("app")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    service.bootstrap()
    log.info("启动：数据库 %s，默认检索词「%s」", settings.DB_PATH, settings.SEARCH_KEYWORD)

    scheduler.start()

    if settings.FETCH_ON_START:
        products = db.list_products(only_subscribed=True)
        empty = [p for p in products if db.rate_count(p["code"]) == 0]

        async def _bootstrap():
            for p in empty:
                try:
                    log.info("无历史数据，启动时抓取 %s …", p["name"])
                    await asyncio.to_thread(service.sync_product, p["code"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("启动抓取 %s 失败：%s", p["code"], exc)

        if empty:
            asyncio.create_task(_bootstrap())

    yield
    scheduler.shutdown()


app = FastAPI(title="建行理财收益监控", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ProductIn(BaseModel):
    code: str = Field(min_length=3, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    principal: float | None = Field(default=None, ge=0, le=1e12)
    start_date: str | None = None


class ProductPatch(BaseModel):
    principal: float | None = Field(default=None, ge=0, le=1e12)
    start_date: str | None = None
    subscribed: int | None = Field(default=None, ge=0, le=1)
    name: str | None = None


def _check_date(s: str | None) -> str | None:
    if not s:
        return None
    try:
        datetime.strptime(s.strip(), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
    return s.strip()


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/overview")
def api_overview():
    data = service.overview()
    data["next_run"] = scheduler.next_run()
    data["fetch_time"] = settings.FETCH_TIME
    data["search_keyword"] = settings.SEARCH_KEYWORD
    data["logs"] = db.recent_logs(12)
    data["server_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return JSONResponse(data)


@app.get("/api/search")
def api_search(q: str = ""):
    try:
        return JSONResponse({"keyword": q or settings.SEARCH_KEYWORD,
                             "items": service.search_products(q)})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


@app.post("/api/products")
def api_add_product(body: ProductIn):
    start = _check_date(body.start_date)
    rec = db.upsert_product(body.code.strip(), body.name.strip(),
                            body.principal, start, subscribed=1)
    return JSONResponse({"ok": True, "product": rec})


@app.patch("/api/products/{code}")
def api_patch_product(code: str, body: ProductPatch):
    if not db.get_product(code):
        raise HTTPException(404, "产品不存在")
    start = _check_date(body.start_date)
    rec = db.update_product(code, principal=body.principal, start_date=start,
                            subscribed=body.subscribed, name=body.name)
    return JSONResponse({"ok": True, "product": rec})


@app.delete("/api/products/{code}")
def api_delete_product(code: str):
    if not db.get_product(code):
        raise HTTPException(404, "产品不存在")
    db.remove_product(code)
    return {"ok": True}


@app.post("/api/products/{code}/sync")
def api_sync_product(code: str):
    try:
        return JSONResponse(service.sync_product(code))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


@app.post("/api/sync")
def api_sync_all():
    results = service.sync_all()
    return JSONResponse({"results": results, "ok": sum(1 for r in results if "error" not in r)})


@app.get("/api/products/{code}/calc")
def api_calc(code: str, principal: float | None = None, start_date: str | None = None):
    start = _check_date(start_date)
    try:
        return JSONResponse(service.build_calc(code, principal, start))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc))


@app.post("/api/test-mail")
def api_test_mail():
    try:
        return JSONResponse(service.send_daily_mail(test=True))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"发送失败：{exc}")


@app.get("/api/health")
def api_health():
    return {"ok": True, "products": len(db.list_products()), "time": datetime.now().isoformat()}


@app.get("/api/diag")
def api_diag(q: str = ""):
    """诊断接口：服务器能否访问建行公告检索、关键词命中情况。"""
    return JSONResponse(service.diagnose(q))
