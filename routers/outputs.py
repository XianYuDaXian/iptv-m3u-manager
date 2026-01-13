from fastapi import APIRouter, HTTPException, Depends, Response
from sqlmodel import Session, select
from typing import List, Dict, Any
import json
from datetime import datetime
import re

from models import OutputSource, Subscription, Channel
from database import get_session
from services.generator import M3UGenerator
from services.epg import fetch_epg_cached
from routers.subscriptions import process_subscription_refresh

router = APIRouter(tags=["outputs"])

@router.post("/outputs/", response_model=OutputSource)
async def create_output(out: OutputSource, session: Session = Depends(get_session)):
    """新建聚合源"""
    if out.epg_url:
        await fetch_epg_cached(out.epg_url, refresh=True)
        
    session.add(out)
    session.commit()
    session.refresh(out)
    return out

@router.get("/outputs/", response_model=List[OutputSource])
def list_outputs(session: Session = Depends(get_session)):
    """聚合源列表"""
    return session.exec(select(OutputSource)).all()

@router.delete("/outputs/{output_id}")
def delete_output(output_id: int, session: Session = Depends(get_session)):
    """删除聚合源"""
    out = session.get(OutputSource, output_id)
    if not out:
        raise HTTPException(status_code=404, detail="输出源不存在")
    session.delete(out)
    session.commit()
    return {"message": "删除成功"}

@router.put("/outputs/{output_id}", response_model=OutputSource)
def update_output(output_id: int, output_data: OutputSource, session: Session = Depends(get_session)):
    """更新聚合配置"""
    output = session.get(OutputSource, output_id)
    if not output:
        raise HTTPException(status_code=404, detail="输出源不存在")
    
    # Slug 变了得检查重名
    if output_data.slug != output.slug:
        existing = session.exec(select(OutputSource).where(OutputSource.slug == output_data.slug)).first()
        if existing:
            raise HTTPException(status_code=400, detail="Slug 已被占用")

    output.name = output_data.name
    output.slug = output_data.slug
    output.filter_regex = output_data.filter_regex
    output.keywords = output_data.keywords
    output.subscription_ids = output_data.subscription_ids
    output.epg_url = output_data.epg_url
    output.include_source_suffix = output_data.include_source_suffix
    
    session.add(output)
    session.commit()
    session.refresh(output)
    return output

@router.post("/outputs/preview")
def preview_output(data: dict, session: Session = Depends(get_session)):
    """预览结果"""
    sub_ids = data.get("subscription_ids", [])
    raw_keywords = data.get("keywords", [])
    regex = data.get("filter_regex", ".*")
    
    # 整理关键字列表
    keywords = []
    for k in raw_keywords:
        if isinstance(k, str):
            keywords.append({"value": k, "group": ""})
        elif isinstance(k, dict):
            keywords.append(k)

    # 只要启用了的预览
    enabled_subs = session.exec(select(Subscription.id).where(Subscription.is_enabled == True)).all()
    active_sub_ids = [sid for sid in sub_ids if sid in enabled_subs] if sub_ids else enabled_subs

    if active_sub_ids:
        channels = session.exec(select(Channel).where(Channel.subscription_id.in_(active_sub_ids))).all()
    else:
        channels = []
        
    # 获取订阅名，方便看来源
    subs = session.exec(select(Subscription)).all()
    sub_map = {s.id: s.name or s.url for s in subs}

    # 应用正则过滤
    if regex and regex != ".*":
        try:
            pattern = re.compile(regex, re.IGNORECASE)
            channels = [c for c in channels if pattern.search(c.name)]
        except:
            pass

    results = {}
    if not keywords:
        # 没搜到关键字就全给它
        channels = M3UGenerator.propagate_logos(channels)
        results["All"] = [
            {**c.model_dump(), "source": sub_map.get(c.subscription_id, "Unknown")} 
            for c in channels 
        ]
    else:
        # 逐个关键字匹配看看
        channels = M3UGenerator.propagate_logos(channels)
        for k_obj in keywords:
            k_val = k_obj.get("value", "")
            k_group = k_obj.get("group", "")
            if not k_val: continue
            
            # 关键字筛选逻辑
            matches = M3UGenerator.filter_channels(channels, None, [k_obj])
            
            display_key = f"{k_val} → {k_group}" if k_group else k_val
            results[display_key] = [
                {**c.model_dump(), "source": sub_map.get(c.subscription_id, "Unknown")} 
                for c in matches 
            ]
            
    return results

@router.post("/outputs/{output_id}/refresh")
async def refresh_output(output_id: int, session: Session = Depends(get_session)):
    """手动刷新关联订阅和 EPG"""
    out = session.get(OutputSource, output_id)
    if not out:
        raise HTTPException(status_code=404, detail="输出源不存在")
    
    try:
        sub_ids = json.loads(out.subscription_ids)
    except:
        sub_ids = []
        
    results = []
    # 逐个刷新订阅
    for sub_id in sub_ids:
        try:
            sub = session.get(Subscription, sub_id)
            if sub:
               await process_subscription_refresh(session, sub)
               results.append(f"Sub {sub_id}: Success")
        except Exception as e:
            sub = session.get(Subscription, sub_id)
            if sub:
                sub.last_update_status = f"Error: {str(e)}"
                session.add(sub)
            results.append(f"Sub {sub_id}: Failed")

    # 刷新聚合 EPG
    if out.epg_url:
        try:
            await fetch_epg_cached(out.epg_url, refresh=True)
            results.append("Aggregate EPG: Success")
        except:
            results.append("Aggregate EPG: Failed")
            
    out.last_updated = datetime.utcnow()
    out.last_update_status = "Checked linked subs"
    session.add(out)
    session.commit()
    return {"message": "刷新完成", "details": results}

@router.get("/m3u/{slug}")
async def get_m3u_output(slug: str, session: Session = Depends(get_session)):
    """下载 M3U"""
    out = session.exec(select(OutputSource).where(OutputSource.slug == slug)).first()
    if not out:
        raise HTTPException(status_code=404, detail="输出源不存在")
    

    out.last_request_time = datetime.utcnow()
    session.add(out)
    session.commit()
    
    try:
        sub_ids = json.loads(out.subscription_ids)
    except:
        sub_ids = []

    # 顺便刷一下关联订阅
    for sub_id in sub_ids:
         try:
            sub = session.get(Subscription, sub_id)
            if sub:
               await process_subscription_refresh(session, sub)
               sub.last_update_status = "Success (Client Trigger)"
               session.add(sub)
               session.commit()
         except:
             pass
    
    # 取出刷新的最新频道
    enabled_subs = session.exec(select(Subscription.id).where(Subscription.is_enabled == True)).all()
    active_sub_ids = [sid for sid in sub_ids if sid in enabled_subs] if sub_ids else enabled_subs

    if active_sub_ids:
        # 只要启用了的
        channels = session.exec(select(Channel).where(
            Channel.subscription_id.in_(active_sub_ids),
            Channel.is_enabled == True
        )).all()
    else:
        channels = []

    subs = session.exec(select(Subscription)).all()
    sub_map = {s.id: s.name or s.url for s in subs}

    try:
        raw_keywords = json.loads(out.keywords)
        keywords = []
        for k in raw_keywords:
            if isinstance(k, str):
                keywords.append({"value": k, "group": ""})
            elif isinstance(k, dict):
                keywords.append(k)
    except:
        keywords = []
        
    # 过滤、生成 M3U 
    filtered = M3UGenerator.filter_channels(channels, out.filter_regex, keywords)
    m3u_content = M3UGenerator.generate_m3u(filtered, sub_map, out.epg_url, out.include_source_suffix)
    return Response(content=m3u_content, media_type="application/x-mpegurl; charset=utf-8")
