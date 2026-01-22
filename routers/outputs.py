from fastapi import APIRouter, HTTPException, Depends, Response, BackgroundTasks
from sqlmodel import Session, select
from typing import List, Dict, Any
import json
from datetime import datetime, timedelta
import re

from models import OutputSource, Subscription, Channel, TaskRecord
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
        
        # 解析排除列表
        try:
            excluded_ids = json.loads(out.excluded_channel_ids or "[]")
        except:
            excluded_ids = []
            
        filtered = M3UGenerator.filter_channels(channels, out.filter_regex, keywords, excluded_ids)
        
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
    output.excluded_channel_ids = output_data.excluded_channel_ids
    
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
    excluded_ids = data.get("excluded_channel_ids", [])  # 聚合表级别排除
    # 确保 ID 都是整数，防止前端传字符串导致匹配失败
    try:
        excluded_set = {int(i) for i in excluded_ids} if excluded_ids else set()
    except:
        excluded_set = set()
    
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
    
    # 预览时不在此处过滤，由前端通过 excluded_channel_ids 显示恢复/排除按钮
    # if excluded_set:
    #     channels = [c for c in channels if c.id not in excluded_set]
        
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

    import uuid
    from models import TaskRecord
    from task_broker import update_task_status
    
    task_id = str(uuid.uuid4())
    task_record = TaskRecord(
        id=task_id,
        name=f"刷新聚合: {out.name}",
        status="running",
        progress=10,
        message="正在刷新关联订阅..."
    )
    session.add(task_record)
    session.commit()

    # 初始广播
    background_tasks.add_task(update_task_status, task_id, status="running", progress=10, message="开始刷新关联订阅...")

    async def _do_refresh():
        try:
            sub_ids = json.loads(out.subscription_ids)
        except:
            sub_ids = []
            
        results_info = []
        # 逐个刷新订阅
        for i, sub_id in enumerate(sub_ids):
            # 每处理一个订阅前检查任务状态
            from database import engine
            with Session(engine) as check_session:
                task = check_session.get(TaskRecord, task_id)
                if not task or task.status == "canceled":
                    print(f"[_do_refresh] 任务 {task_id} 已中止，停止处理后续订阅")
                    await update_task_status(task_id, status="canceled", message="刷新作业已由用户中止")
                    return

            try:
                sub = session.get(Subscription, sub_id)
                if sub:
                   await process_subscription_refresh(session, sub)
                   results_info.append(f"Sub {sub_id}: Success")
                   # 更新进度（分摊到前 50%）
                   p = 10 + int((i+1)/len(sub_ids) * 40) if sub_ids else 50
                   await update_task_status(task_id, progress=p, message=f"已同步订阅: {sub.name or sub_id}")
            except Exception as e:
                results_info.append(f"Sub {sub_id}: Failed")

        # 刷新聚合 EPG
        if out.epg_url:
            await update_task_status(task_id, progress=50, message="正在更新 EPG...")
            try:
                await fetch_epg_cached(out.epg_url, refresh=True)
            except: pass
                
        out.last_updated = datetime.utcnow()
        out.last_update_status = "手动更新成功"
        session.add(out)
        session.commit()

        # 如果开启了自动深度检测
        if out.auto_visual_check:
            await update_task_status(task_id, progress=60, message="准备深度检测...")
            # 注意：此处直接复用 run_output_visual_check，但需要让它接管已有的 task_id
            await run_output_visual_check_v2(output_id, task_id=task_id, force_check=True)
        else:
            # 最终出口防御：再次核对数据库，严防状态回跳
            from database import engine
            with Session(engine) as check_session:
                task = check_session.get(TaskRecord, task_id)
                if task and task.status != "canceled":
                    await update_task_status(task_id, status="success", progress=100, message="刷新完成")
                else:
                    print(f"[_do_refresh] 任务 {task_id} 已处于取消状态，跳过最终成功广播")

    # 为了不阻塞 FastAPI 响应，刷新逻辑也在后台跑（或者如果刷新不慢，也可以 await）
    # 这里采用 await 方式以保证 results 正确返回前端 UI 立即更新，而深度检测由其内部异步逻辑接管
    background_tasks.add_task(_do_refresh)

    return {"message": "任务已提交", "task_id": task_id}

async def run_output_visual_check_v2(output_id: int, task_id: str, force_check: bool = False):
    """(优化版) 后台运行深度检测，接管已有 TaskID"""
    from database import engine
    from sqlmodel import Session
    from task_broker import update_task_status
    
    with Session(engine) as session:
        out = session.get(OutputSource, output_id)
        if not out: return
        
        try:
            sub_ids = json.loads(out.subscription_ids)
            raw_channels = []
            for sid in sub_ids:
                chs = session.exec(select(Channel).where(Channel.subscription_id == sid)).all()
                raw_channels.extend(chs)
            
            try: keywords = json.loads(out.keywords)
            except: keywords = []
            
            from services.generator import M3UGenerator
            matched_channels = M3UGenerator.filter_channels(raw_channels, out.filter_regex, keywords)
            
            if matched_channels:
                from services.stream_checker import StreamChecker
                check_source = 'manual' if force_check else 'auto'
                check_result = await StreamChecker.run_batch_check(session, matched_channels, source=check_source, task_id=task_id)
                
                # 如果检测因中止而提前退出，严禁发送成功广播
                if check_result is False:
                    print(f"[run_output_visual_check_v2] 任务 {task_id} 已由用户中止，跳过成功广播")
                    return
                
                # 再次同步聚合源状态
                out = session.get(OutputSource, output_id)
                if out:
                    out.last_update_status = "手动更新+深度检测完成"
                    session.add(out)
                    session.commit()
                
                # 最终出口防御：硬判状态再广播
                with Session(engine) as check_session:
                    task = check_session.get(TaskRecord, task_id)
                    if task and task.status == "canceled":
                        print(f"[run_output_visual_check_v2] 任务 {task_id} 已处于取消状态，跳过最终成功广播")
                        return
                
                await update_task_status(task_id, status="success", progress=100, message="更新与检测全部完成")
            else:
                await update_task_status(task_id, status="success", progress=100, message="刷新完成 (无匹配频道需检测)")
        except Exception as e:
            await update_task_status(task_id, status="failure", message=f"检测执行出错: {e}")

async def run_output_visual_check(output_id: int, force_check: bool = False):
    """后台运行深度检测"""
    from database import engine
    from sqlmodel import Session
    
    with Session(engine) as session:
        out = session.get(OutputSource, output_id)
        if not out: return
        
        # 创建一个异步任务记录，以便“刷新节目表”后触发的检测也能在任务中心看到
        import uuid
        from models import TaskRecord
        from task_broker import update_task_status
        task_id = str(uuid.uuid4())
        task_record = TaskRecord(
            id=task_id,
            name=f"刷新聚合检测: {out.name}",
            status="pending",
            progress=0,
            message="正在准备检测..."
        )
        session.add(task_record)
        session.commit()
        
        # 初始广播
        await update_task_status(task_id, status="pending", progress=0, message="任务排队中")

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
            
            # 彻底移除冷却限制：只要触发此任务，就对所有匹配频道进行探测
            pending_channels = matched_channels
            
            if pending_channels:
                print(f"[后台检测] 聚合源 {out.id} 触发同步深度检测，待测: {len(pending_channels)}")
                from services.stream_checker import StreamChecker
                check_source = 'manual' if force_check else 'auto'
                
                # 传入 task_id 以便更新进度
                await StreamChecker.run_batch_check(session, pending_channels, source=check_source, task_id=task_id)
                
                # 重新获取对象并更新状态
                out = session.get(OutputSource, output_id)
                if out:
                    out.last_update_status = "手动更新+深度检测完成"
                    session.add(out)
                    session.commit()
                
                await update_task_status(task_id, status="success", progress=100, message="检测完成")
                print(f"[后台检测] 聚合源 {out.id} 检测完成。")
            else:
                await update_task_status(task_id, status="success", progress=100, message="无匹配频道需要检测")
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
    
    # 解析排除列表
    try:
        excluded_ids = json.loads(out.excluded_channel_ids or "[]")
    except:
        excluded_ids = []
        
    # 过滤、生成 M3U 
    filtered = M3UGenerator.filter_channels(channels, out.filter_regex, keywords, excluded_ids)
    m3u_content = M3UGenerator.generate_m3u(filtered, sub_map, out.epg_url, out.include_source_suffix)
    return Response(content=m3u_content, media_type="application/x-mpegurl; charset=utf-8")
