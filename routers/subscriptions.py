from fastapi import APIRouter, HTTPException, Depends
from sqlmodel import Session, select
from typing import List
from models import Subscription, Channel
from database import get_session
from services.fetcher import IPTVFetcher
from services.epg import fetch_epg_cached
from datetime import datetime

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

@router.post("/", response_model=Subscription)
async def create_subscription(sub: Subscription, session: Session = Depends(get_session)):
    """加个新订阅，顺便刷一遍"""
    sub.url = sub.url.strip()
    session.add(sub)
    session.commit()
    session.refresh(sub)
    
    # 第一次加，先刷下 EPG
    try:
        await process_subscription_refresh(session, sub)
    except Exception as e:
        print(f"首次刷新失败 {sub.url}: {e}")
        
    return sub

@router.get("/", response_model=List[Subscription])
def list_subscriptions(session: Session = Depends(get_session)):
    """订阅源列表"""
    return session.exec(select(Subscription)).all()

@router.delete("/{sub_id}")
def delete_subscription(sub_id: int, session: Session = Depends(get_session)):
    """删除订阅源（连带频道一起删）"""
    sub = session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    
    # 频道得跟着一块走
    channels = session.exec(select(Channel).where(Channel.subscription_id == sub_id)).all()
    for c in channels:
        session.delete(c)
        
    session.delete(sub)
    session.commit()
    return {"message": "删除成功"}

@router.put("/{sub_id}", response_model=Subscription)
def update_subscription(sub_id: int, updated: Subscription, session: Session = Depends(get_session)):
    """修改订阅配置"""
    db_sub = session.get(Subscription, sub_id)
    if not db_sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    db_sub.name = updated.name
    db_sub.url = updated.url.strip()
    db_sub.user_agent = updated.user_agent
    db_sub.headers = updated.headers
    db_sub.auto_update_minutes = updated.auto_update_minutes
    db_sub.is_enabled = updated.is_enabled
    session.add(db_sub)
    session.commit()
    session.refresh(db_sub)
    return db_sub

@router.get("/{sub_id}/channels", response_model=List[Channel])
def get_subscription_channels(sub_id: int, session: Session = Depends(get_session)):
    """这个订阅下都有啥台？"""
    sub = session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    channels = session.exec(select(Channel).where(Channel.subscription_id == sub_id)).all()
    return channels

async def process_subscription_refresh(session: Session, sub: Subscription) -> int:
    """同步订阅（在这个事务里干活）"""
    # 刷新后保持之前的禁用状态
    disabled_names = {c.name for c in old_channels if not c.is_enabled}
    
    # 清掉旧台
    for c in old_channels:
        session.delete(c)
    
    # 抓取并解析（M3U/TXT 混合）
    channels_data, metadata = await IPTVFetcher.fetch_subscription(sub.url, sub.user_agent, sub.headers)
    
    for item in channels_data:
        # 恢复禁用状态
        is_enabled = True
        if item.get("name") in disabled_names:
            is_enabled = False
            
        channel = Channel(**item, subscription_id=sub.id, is_enabled=is_enabled)
        session.add(channel)
    
    sub.last_updated = datetime.utcnow()
    sub.last_update_status = "Success"
    session.add(sub)
    session.commit()
    return len(channels_data)

@router.post("/{sub_id}/refresh")
async def refresh_subscription(sub_id: int, session: Session = Depends(get_session)):
    """手动刷新订阅"""
    sub = session.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    
    try:
        count = await process_subscription_refresh(session, sub)
        return {"message": f"成功抓取 {count} 个频道"}
    except Exception as e:
        sub = session.get(Subscription, sub_id)
        if sub:
            sub.last_update_status = f"Error: {str(e)}"
            session.add(sub)
            session.commit()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
