from fastapi import APIRouter, HTTPException, Depends, Response, BackgroundTasks
from sqlmodel import Session, select
from typing import List, Dict, Any
import json
from datetime import datetime, timedelta
import re

from models import OutputSource, Subscription, Channel
from database import get_session
from services.generator import M3UGenerator
from services.epg import fetch_epg_cached
from services.stream_checker import StreamChecker
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

@router.get("/outputs/")
def list_outputs(session: Session = Depends(get_session)):
    """聚合源列表，包含详细统计"""
    outputs = session.exec(select(OutputSource)).all()
    results = []
    
    for out in outputs:
        # 获取关联的所有订阅 ID
        try:
            sub_ids = json.loads(out.subscription_ids)
        except:
            sub_ids = []
            
        # 获取这些订阅下的所有频道
        channels = session.exec(select(Channel).where(Channel.subscription_id.in_(sub_ids))).all()
        
        # 使用生成的逻辑进行过滤
        try:
            keywords = json.loads(out.keywords)
        except:
            keywords = []
            
        filtered = M3UGenerator.filter_channels(channels, out.filter_regex, keywords)
        
        total = len(filtered)
        enabled = len([c for c in filtered if c.is_enabled])
        disabled = total - enabled
        
        # 转为字典并添加统计
        out_dict = out.model_dump()
        out_dict.update({
            "total_count": total,
            "enabled_count": enabled,
            "disabled_count": disabled
        })
        results.append(out_dict)
        
    return results

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
    output.is_enabled = output_data.is_enabled
    output.auto_update_minutes = output_data.auto_update_minutes
    output.auto_visual_check = output_data.auto_visual_check
    
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
async def refresh_output(output_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
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
    out.last_update_status = "手动更新成功"
    session.add(out)
    session.commit()

    # 如果开启了自动深度检测，手动刷新时也异步触发
    trigger_visual = False
    if out.auto_visual_check:
        print(f"DEBUG: [手动刷新] 聚合源 {output_id} 开启了深度检测，加入后台任务 (强制探测模式)...")
        background_tasks.add_task(run_output_visual_check, output_id, force_check=True)
        trigger_visual = True
    else:
        print(f"DEBUG: [手动刷新] 聚合源 {output_id} 未开启深度检测 (auto_visual_check={out.auto_visual_check})")

    return {"message": "刷新完成", "details": results, "trigger_visual": trigger_visual}

async def run_output_visual_check(output_id: int, force_check: bool = False):
    """后台运行深度检测"""
    from database import engine
    from sqlmodel import Session
    
    with Session(engine) as session:
        out = session.get(OutputSource, output_id)
        if not out: return
        
        try:
            sub_ids = json.loads(out.subscription_ids)
            raw_channels = []
            for sid in sub_ids:
                chs = session.exec(select(Channel).where(Channel.subscription_id == sid)).all()
                raw_channels.extend(chs)
            
            try:
                keywords = json.loads(out.keywords)
            except:
                keywords = []
            
            from services.generator import M3UGenerator
            matched_channels = M3UGenerator.filter_channels(raw_channels, out.filter_regex, keywords)
            
            # 手动刷新时默认强制执行（force_check=True）
            if force_check:
                pending_channels = matched_channels
                print(f"DEBUG: [后台检测] 强制模式启动，将重新探测全部 {len(pending_channels)} 个匹配频道")
            else:
                # 自动同步场景下复用 24 小时冷却逻辑
                check_limit = datetime.utcnow() - timedelta(hours=24)
                pending_channels = [
                    c for c in matched_channels 
                    if not c.check_date or c.check_date < check_limit
                ]
                print(f"DEBUG: [后台检测] 自动模式，匹配 {len(matched_channels)} 个频道，其中 {len(pending_channels)} 个需要重新探测")
            
            if pending_channels:
                print(f"[后台检测] 聚合源 {out.id} 触发同步深度检测，待测: {len(pending_channels)}")
                from services.stream_checker import StreamChecker
                check_source = 'manual' if force_check else 'auto'
                await StreamChecker.run_batch_check(session, pending_channels, source=check_source)
                
                # 重新获取对象以防 session 冲突
                out = session.get(OutputSource, output_id)
                out.last_update_status = "手动更新+深度检测完成"
                session.add(out)
                session.commit()
                print(f"[后台检测] 聚合源 {out.id} 检测完成。")
        except Exception as e:
            print(f"[后台检测] 聚合源 {out.id} 执行失败: {e}")

@router.get("/m3u/{slug}")
async def get_m3u_output(slug: str, session: Session = Depends(get_session)):
    """下载 M3U"""
    out = session.exec(select(OutputSource).where(OutputSource.slug == slug)).first()
    if not out:
        raise HTTPException(status_code=404, detail="输出源不存在")
    

    out.last_request_time = datetime.utcnow()
    session.add(out)
    session.commit()
    session.refresh(out) # 确保状态同步
    
    # 检查是否启用
    if not out.is_enabled:
        return Response(content="#EXTM3U\n# 频道已暂时下线，请在后台启用该聚合源后重试。", media_type="text/plain; charset=utf-8")

    try:
        sub_ids = json.loads(out.subscription_ids)
    except:
        sub_ids = []
    
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
