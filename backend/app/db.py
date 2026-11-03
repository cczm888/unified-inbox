"""数据库与 Redis 连接管理。"""
from __future__ import annotations

from collections.abc import AsyncIterator

from redis.asyncio import Redis, from_url
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


_IS_SQLITE = settings.database_url.startswith("sqlite")

_engine_kwargs: dict = {"future": True}
if not _IS_SQLITE:
    # 连接池保活只对真正的网络数据库有意义
    _engine_kwargs["pool_pre_ping"] = True

engine = create_async_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _IS_SQLITE else {},
    **_engine_kwargs,
)

if _IS_SQLITE:
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - 只有本地 SQLite 走这里
        """SQLite 默认不校验外键、也不开 WAL，本地演示时补上。"""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    return _redis


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用。"""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """启动时建表（比赛场景用 create_all 足够；生产可换 alembic）。"""
    from . import models  # noqa: F401  确保模型已注册到 metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_columns(conn)


async def _ensure_columns(conn) -> None:
    """轻量迁移：给已存在的库补新列。

    create_all 只会建缺失的表，不会给老表加列。比赛现场不可能删库重建
    （里面有演示数据），所以这里做一次幂等的补列，SQLite / PostgreSQL 都覆盖。
    """
    from sqlalchemy import text

    dialect = conn.dialect.name
    if dialect == "sqlite":
        rows = (await conn.execute(text("PRAGMA table_info(message)"))).all()
        cols = {r[1] for r in rows}
        if "read_at" not in cols:
            await conn.execute(text("ALTER TABLE message ADD COLUMN read_at DATETIME"))
        if "account_id" not in cols:
            # SQLite 的 ALTER TABLE 加不了外键约束，列本身加上即可（业务层保证一致性）
            await conn.execute(text("ALTER TABLE message ADD COLUMN account_id INTEGER"))
        acc_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(platform_account)"))).all()}
        if "label" not in acc_cols:
            await conn.execute(text("ALTER TABLE platform_account ADD COLUMN label VARCHAR(128)"))
        if "extra" not in acc_cols:
            await conn.execute(text("ALTER TABLE platform_account ADD COLUMN extra JSON"))
        if "last_error" not in acc_cols:
            await conn.execute(text("ALTER TABLE platform_account ADD COLUMN last_error TEXT"))
    else:
        await conn.execute(text("ALTER TABLE message ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ"))
        await conn.execute(text("ALTER TABLE message ADD COLUMN IF NOT EXISTS account_id BIGINT"))
        await conn.execute(text("ALTER TABLE platform_account ADD COLUMN IF NOT EXISTS label VARCHAR(128)"))
        await conn.execute(text("ALTER TABLE platform_account ADD COLUMN IF NOT EXISTS extra JSONB"))
        await conn.execute(text("ALTER TABLE platform_account ADD COLUMN IF NOT EXISTS last_error TEXT"))


async def close_all() -> None:
    await engine.dispose()
    if _redis is not None:
        await _redis.aclose()
