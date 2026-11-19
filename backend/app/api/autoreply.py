"""自动回复规则 API。

规则的存在意义：赛题闭环里写的是「自动或人工回复」。
人工回复已经有了（右栏发送框），这里补的是自动那一半。

设计上刻意保持"可解释"：关键词包含匹配 + 平台过滤 + 优先级，
任何一条自动回复都能回答"为什么回了这句"——现场演示时这一条很重要。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import AutoRule
from ..schemas import AutoRuleIn, AutoRuleOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/autoreply", tags=["autoreply"])


@router.get("/rules", response_model=list[AutoRuleOut])
async def list_rules(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(select(AutoRule).order_by(AutoRule.priority.asc(), AutoRule.id.asc()))
    ).scalars().all()
    return rows


@router.post("/rules", response_model=AutoRuleOut)
async def create_rule(body: AutoRuleIn, session: AsyncSession = Depends(get_session)):
    reply = (body.reply_text or "").strip()
    if not reply:
        raise HTTPException(400, "回复内容不能为空")
    if len(reply) > 2000:
        raise HTTPException(400, "回复内容过长（上限 2000 字）")

    rule = AutoRule(
        platform=(body.platform or None),
        keyword=(body.keyword or "").strip() or None,
        reply_text=reply,
        priority=body.priority,
        enabled=body.enabled,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    log.info("新增自动回复规则 #%s platform=%s keyword=%s", rule.id, rule.platform, rule.keyword)
    return rule


@router.patch("/rules/{rule_id}", response_model=AutoRuleOut)
async def update_rule(rule_id: int, body: AutoRuleIn, session: AsyncSession = Depends(get_session)):
    rule = await session.get(AutoRule, rule_id)
    if rule is None:
        raise HTTPException(404, "规则不存在")
    if body.enabled is not None:
        rule.enabled = body.enabled
    if body.reply_text:
        rule.reply_text = body.reply_text.strip()
    if body.keyword is not None:
        rule.keyword = body.keyword.strip() or None
    if body.platform is not None:
        rule.platform = body.platform or None
    if body.priority is not None:
        rule.priority = body.priority
    await session.commit()
    await session.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(AutoRule).where(AutoRule.id == rule_id))
    await session.commit()
    return {"ok": True}
