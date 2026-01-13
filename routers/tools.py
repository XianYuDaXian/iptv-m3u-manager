from fastapi import APIRouter
import asyncio
import aiohttp
from sqlmodel import SQLModel

from services.connectivity import check_url
from services.epg import EPGManager, fetch_epg_cached, md5

router = APIRouter(tags=["tools"])

from datetime import datetime
from sqlmodel import Session, select
from database import get_session
from fastapi import Depends
from models import Channel

class CheckRequest(SQLModel):
    """检测请求数据"""
    urls: list[str] = []
    items: list[dict] = [] # 包含 {id: int, url: str} 的列表
    auto_disable: bool = False

# 并发控制锁（防 FFmpeg 跑太猛爆 CPU）
visual_check_semaphore = asyncio.Semaphore(4)
from services.stream_checker import StreamChecker

@router.get("/api/epg/current")
async def get_epg_current(epg_url: str, tvg_id: str = None, tvg_name: str = None, current_logo: str = None, refresh: bool = False):
    """看现在播啥节目"""
    prog_data = await EPGManager.get_program(epg_url, tvg_id, tvg_name, current_logo, refresh=refresh)
    return {"program": prog_data.get("title", ""), "logo": prog_data.get("logo")}

@router.post("/check-connectivity")
async def check_connectivity(req: CheckRequest):
    """快速连通性检测（通不通）"""
    async with aiohttp.ClientSession() as session:
        target_urls = req.urls if req.urls else [i['url'] for i in req.items]
        tasks = [check_url(u, session) for u in target_urls]
        results = await asyncio.gather(*tasks)
        return results

@router.post("/check-stream-visual")
async def check_stream_visual(req: CheckRequest, session: Session = Depends(get_session)):
    """深度检测 (用 FFmpeg 截图)"""
    async def bounded_check(item):
        url = item['url']
        channel_id = item.get('id')
        
        # 限下并发
        async with visual_check_semaphore:
            res = await StreamChecker.check_stream_visual(url)
            
        return {**res, "id": channel_id}

    # 批量截图
    tasks = [bounded_check(item) for item in req.items]
    results = await asyncio.gather(*tasks)
    
    # 检测结果入库
    for res in results:
        if res.get('id'):
            ch = session.get(Channel, res['id'])
            if ch:
                ch.check_status = res['status']
                ch.check_date = datetime.utcnow()
                ch.check_image = res.get('image') # Base64 格式字符串或 None
                
                # 不行就禁用（如果开启了自动处理）
                if req.auto_disable and not res['status']:
                    ch.is_enabled = False
                    res['auto_disabled'] = True # 返回给前端的标识
                
                res['is_enabled'] = ch.is_enabled 
                session.add(ch)
    session.commit()
    
    return results
