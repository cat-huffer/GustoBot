"""
GustoBot 的 FastAPI 入口：注册路由与中间件、挂载上传目录；启动时建表，关闭时回收 Neo4j / LightRAG。
`app` 为 `application` 的兼容别名（便于部分工具与 ASGI 约定）。
"""

import os
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from gustobot.application.services.lightrag_service import get_lightrag_service
from gustobot.config import settings
from gustobot.infrastructure.core import configure_logging
from gustobot.infrastructure.core.database import Base, engine
from gustobot.interfaces.http import knowledge_router, lightrag_router
from gustobot.interfaces.http.knowledge_router import get_neo4j_qa_service
from gustobot.interfaces.http.v1 import api_router as api_v1_router

# 创建应用前先配置日志
configure_logging(debug=settings.DEBUG)

# FastAPI 应用实例
application = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="基于多智能体架构的智能烹饪助手",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 全局中间件（开发/联调阶段放开来源）
application.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTP 路由
application.include_router(knowledge_router.router, prefix=settings.API_V1_PREFIX)
application.include_router(lightrag_router.router, prefix=settings.API_V1_PREFIX)
application.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

# 上传目录：确保存在并挂载为静态资源
uploads_dir = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
os.makedirs(uploads_dir, exist_ok=True)

application.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

@application.get("/")
async def root_redirect():
    """根路径重定向到 OpenAPI 文档（/docs）；Web 前端在 web/ 独立运行。"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs")


@application.get("/favicon.ico")
async def favicon():
    """返回空 favicon，减少开发时浏览器对 /favicon.ico 的 404 噪音。"""
    from fastapi.responses import Response
    return Response(content=b"", media_type="image/x-icon")


@application.get("/api")
async def root() -> dict:
    """服务名称、版本与文档入口（轻量状态页）。"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
    }


@application.get("/health")
async def health_check() -> dict:
    """健康检查：运行状态与版本。"""
    return {"status": "healthy", "version": settings.APP_VERSION}


@application.on_event("startup")
async def startup_event() -> None:
    """应用启动：记录环境信息并创建缺失的数据库表。"""
    logger.info("Starting {} v{}", settings.APP_NAME, settings.APP_VERSION)
    logger.info("Debug mode: {}", settings.DEBUG)
    logger.info("API docs available at http://{}:{}/docs", settings.HOST, settings.PORT)

    # 导入 ORM 模型以注册到 metadata，再按需建表
    import gustobot.infrastructure.persistence.db.models  # noqa: F401
    logger.info("Creating database tables if not exist...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")


@application.on_event("shutdown")
async def shutdown_event() -> None:
    """应用关闭：关闭 Neo4j 连接并清理 LightRAG。"""
    logger.info("Shutting down {}", settings.APP_NAME)

    try:
        service = get_neo4j_qa_service()
        service.close()
        get_neo4j_qa_service.cache_clear()
    except Exception as exc:  # pragma: no cover
        logger.warning(f"Failed to close Neo4j service cleanly: {exc}")

    try:
        lightrag_service = get_lightrag_service()
        await lightrag_service.cleanup()
    except Exception as exc:  # pragma: no cover
        logger.warning(f"Failed to cleanup LightRAG service: {exc}")


@application.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未捕获异常：返回 500；非 DEBUG 时对客户端隐藏异常详情。"""
    logger.error("Unhandled exception on {} {}: {}", request.method, request.url, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": str(exc) if settings.DEBUG else "An unexpected error occurred",
        },
    )


if __name__ == "__main__":
    uvicorn.run(
        "gustobot.main:application",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="info",
    )

# 兼容 ASGI/工具链常用的 `app` 导出名
app = application
