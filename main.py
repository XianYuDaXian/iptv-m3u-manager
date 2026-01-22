from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
import asyncio
import os
import json
from datetime import datetime, timedelta

from database import engine, create_engine, sqlite_url
from models import SQLModel, Subscription, Channel, OutputSource, TaskRecord
from routers import subscriptions, outputs, tools, channels, tasks
from task_broker import broker, update_task_status
import uuid

app = FastAPI(title="IPTV M3U Manager")

# 静态文件路径
if not os.path.exists("./static"):
    os.makedirs("./static", exist_ok=True)
app.mount("/static", StaticFiles(directory="./static"), name="static")

# 加载功能路由
app.include_router(subscriptions.router)
app.include_router(outputs.router)
app.include_router(tools.router)
app.include_router(channels.router)
app.include_router(tasks.router)

from sqlalchemy import text

def create_db_and_tables():
    """初始化数据库"""
    SQLModel.metadata.create_all(engine)

def migrate_db():
    """数据库迁移（加新字段）"""
    with Session(engine) as session:
        # 订阅表结构迁移
        try:
            session.exec(text("SELECT last_update_status FROM subscription LIMIT 1"))
        except:
            print("正在迁移 Subscription 表: 添加 last_update_status 字段")
            session.exec(text("ALTER TABLE subscription ADD COLUMN last_update_status VARCHAR"))
            session.commit()
            
        try:
            session.exec(text("SELECT auto_update_minutes FROM subscription LIMIT 1"))
        except:
            print("正在迁移 Subscription 表: 添加 auto_update_minutes 字段")
            session.exec(text("ALTER TABLE subscription ADD COLUMN auto_update_minutes INTEGER DEFAULT 0"))
            session.commit()

        try:
            session.exec(text("SELECT is_enabled FROM subscription LIMIT 1"))
        except:
            print("正在迁移 Subscription 表: 添加 is_enabled 字段")
            session.exec(text("ALTER TABLE subscription ADD COLUMN is_enabled BOOLEAN DEFAULT 1"))
            session.commit()

        try:
            session.exec(text("SELECT epg_url FROM subscription LIMIT 1"))
        except:
            print("正在迁移 Subscription 表: 添加 epg_url 字段")
            session.exec(text("ALTER TABLE subscription ADD COLUMN epg_url VARCHAR"))
            session.commit()
        
        # 频道表结构迁移
        try:
            session.exec(text("SELECT tvg_id FROM channel LIMIT 1"))
        except:
            print("正在迁移 Channel 表: 添加 tvg_id 字段")
            session.exec(text("ALTER TABLE channel ADD COLUMN tvg_id VARCHAR"))
            session.commit()

        # 聚合输出表结构迁移
        try:
            session.exec(text("SELECT epg_url FROM outputsource LIMIT 1"))
        except:
            print("正在迁移 OutputSource 表: 添加 epg_url 字段")
            session.exec(text("ALTER TABLE outputsource ADD COLUMN epg_url VARCHAR"))
            session.commit()

        try:
            session.exec(text("SELECT include_source_suffix FROM outputsource LIMIT 1"))
        except:
            print("正在迁移 OutputSource 表: 添加 include_source_suffix 字段")
            session.exec(text("ALTER TABLE outputsource ADD COLUMN include_source_suffix BOOLEAN DEFAULT 1"))
            session.commit()

        try:
            session.exec(text("SELECT last_updated FROM outputsource LIMIT 1"))
        except:
            print("正在迁移 OutputSource 表: 添加 last_updated 和 last_update_status 字段")
            session.exec(text("ALTER TABLE outputsource ADD COLUMN last_updated DATETIME"))
            session.exec(text("ALTER TABLE outputsource ADD COLUMN last_update_status VARCHAR"))
            session.commit()
        
        try:
            session.exec(text("SELECT last_request_time FROM outputsource LIMIT 1"))
        except:
             print("正在迁移 OutputSource 表: 添加 last_request_time 字段")
             session.exec(text("ALTER TABLE outputsource ADD COLUMN last_request_time DATETIME"))
             session.commit()

        try:
            session.exec(text("SELECT is_enabled FROM channel LIMIT 1"))
        except:
            print("正在迁移 Channel 表: 添加 is_enabled 字段")
            session.exec(text("ALTER TABLE channel ADD COLUMN is_enabled BOOLEAN DEFAULT 1"))
            session.commit()
            
        try:
            session.exec(text("SELECT check_status FROM channel LIMIT 1"))
        except:
            print("正在迁移 Channel 表: 添加深度检测相关字段 (check_status, check_date, check_image)")
            session.exec(text("ALTER TABLE channel ADD COLUMN check_status BOOLEAN"))
            session.exec(text("ALTER TABLE channel ADD COLUMN check_date DATETIME"))
            session.exec(text("ALTER TABLE channel ADD COLUMN check_image VARCHAR"))
            session.commit()

        try:
            session.exec(text("SELECT check_error FROM channel LIMIT 1"))
        except:
            print("正在迁移 Channel 表: 添加 check_error 字段")
            session.exec(text("ALTER TABLE channel ADD COLUMN check_error VARCHAR"))
            session.commit()

        try:
            session.exec(text("SELECT check_source FROM channel LIMIT 1"))
        except:
             print("正在迁移 Channel 表: 添加 check_source 字段")
             session.exec(text("ALTER TABLE channel ADD COLUMN check_source VARCHAR"))
             session.commit()

        try:
            session.exec(text("SELECT is_enabled FROM outputsource LIMIT 1"))
        except:
            print("正在迁移 OutputSource 表: 添加 is_enabled 字段")
            session.exec(text("ALTER TABLE outputsource ADD COLUMN is_enabled BOOLEAN DEFAULT 1"))
            session.commit()

        try:
            session.exec(text("SELECT auto_update_minutes FROM outputsource LIMIT 1"))
        except:
            print("正在迁移 OutputSource 表: 添加 auto_update_minutes 字段")
            session.exec(text("ALTER TABLE outputsource ADD COLUMN auto_update_minutes INTEGER DEFAULT 0"))
            session.commit()

        try:
            session.exec(text("SELECT auto_visual_check FROM outputsource LIMIT 1"))
        except:
            print("正在迁移 OutputSource 表: 添加 auto_visual_check 字段")
            session.exec(text("ALTER TABLE outputsource ADD COLUMN auto_visual_check BOOLEAN DEFAULT 0"))
            session.commit()

        # 聚合表级别频道排除功能
        try:
            session.exec(text("SELECT excluded_channel_ids FROM outputsource LIMIT 1"))
        except:
            print("正在迁移 OutputSource 表: 添加 excluded_channel_ids 字段")
            session.exec(text("ALTER TABLE outputsource ADD COLUMN excluded_channel_ids VARCHAR DEFAULT '[]'"))
            session.commit()

async def auto_update_task():
    """后台自动同步订阅"""
    while True:
        try:
            with Session(engine) as session:
                # 1. 更新订阅
                subs = session.exec(select(Subscription).where(
                    Subscription.auto_update_minutes > 0,
                    Subscription.is_enabled == True
                )).all()
                for sub in subs:
                    now = datetime.utcnow()
                    last = sub.last_updated or datetime.min
                    elapsed_mins = (now - last).total_seconds() / 60
                    
                    if elapsed_mins >= sub.auto_update_minutes:
                        print(f"[自动更新] 正在刷新订阅 {sub.id} ({sub.name})。已耗时: {elapsed_mins:.1f}分钟")
                        
                        # 派发异步任务
                        from services.fetcher import fetch_subscription_task
                        task_id = f"auto-sub-{sub.id}-{int(now.timestamp())}"
                        task_record = TaskRecord(
                            id=task_id,
                            name=f"自动同步订阅: {sub.name}",
                            status="pending",
                            is_shown=False # 自动任务默认不在前端弹窗，但在任务列表可见
                        )
                        session.add(task_record)
                        session.commit()
                        
                        await fetch_subscription_task.kiq(
                            task_id=task_id,
                            sub_id=sub.id,
                            url_str=sub.url,
                            ua=sub.user_agent,
                            headers_json=sub.headers
                        )
            
                # 2. 更新聚合源
                outputs = session.exec(select(OutputSource).where(
                    OutputSource.auto_update_minutes > 0,
                    OutputSource.is_enabled == True
                )).all()
                for out in outputs:
                        now = datetime.utcnow()
                        last = out.last_updated or datetime.min
                        elapsed_mins = (now - last).total_seconds() / 60

                        if elapsed_mins >= out.auto_update_minutes:
                            print(f"[自动更新] 正在刷新聚合源 {out.id} ({out.name})...")
                            try:
                                sub_ids = json.loads(out.subscription_ids)
                                # 此处不需要 process_subscription_refresh，因为步骤1已经刷过所有订阅
                                # 直接刷新聚合 EPG (如果有)
                                if out.epg_url:
                                    from services.epg import fetch_epg_cached
                                    await fetch_epg_cached(out.epg_url, refresh=True)
                                
                                out.last_updated = now
                                out.last_update_status = "自动更新成功"
                                session.add(out)
                                session.commit()
                                print(f"[自动更新] 聚合源 {out.id} 同步完成。")

                                # 4. 自动化深度检测 (如果开启)
                                if out.auto_visual_check:
                                    print(f"[自动同步] 聚合源 {out.id} 开启了同步后深度检测，正在启动...")
                                    
                                    from services.generator import M3UGenerator
                                    from models import Channel
                                    
                                    # 1. 获取该聚合源关联的所有原始频道对象（带具体属性用于过滤）
                                    raw_channels = []
                                    for sid in sub_ids:
                                        chs = session.exec(select(Channel).where(Channel.subscription_id == sid)).all()
                                        raw_channels.extend(chs)
                                    
                                    # 2. 应用聚合源的过滤逻辑（关键词+正则），这才能保证检测的是正确的频道
                                    try:
                                        keywords = json.loads(out.keywords)
                                    except:
                                        keywords = []
                                    matched_channels = M3UGenerator.filter_channels(raw_channels, out.filter_regex, keywords)
                                    
                                    matched_ids = [c.id for c in matched_channels]

                                    if matched_ids:
                                        from services.stream_checker import check_channels_task
                                        task_id = f"auto-check-{out.id}-{int(now.timestamp())}"
                                        task_record = TaskRecord(
                                            id=task_id,
                                            name=f"自动深度检测: {out.name}",
                                            status="pending",
                                            is_shown=False
                                        )
                                        session.add(task_record)
                                        session.commit()
                                        
                                        await check_channels_task.kiq(
                                            task_id=task_id,
                                            channel_ids=matched_ids,
                                            source='auto'
                                        )
                                        print(f"[自动同步] 聚合源 {out.id} 已派发深度检测任务 ({len(matched_ids)} 个频道)。")
                            except Exception as e:
                                print(f"[自动更新] 聚合源 {out.id} 刷新失败: {e}")
                                out.last_update_status = f"自动更新失败: {str(e)}"
                                session.add(out)
                                session.commit()
        except Exception as outer_e:
            print(f"[自动更新] 循环发生错误: {outer_e}")
            
        await asyncio.sleep(30) # 每隔 30 秒检查一次，提高 2 分钟测试任务的灵敏度

@app.on_event("startup")
async def on_startup():
    """启动时初始化"""
    create_db_and_tables()
    migrate_db()
    
    # 启动 Taskiq Broker
    await broker.startup()
    
    # 纠正“僵尸任务”：将重启前仍处于运行中或等待中的任务重置为已中止
    with Session(engine) as session:
        statement = select(TaskRecord).where(TaskRecord.status.in_(["running", "pending"]))
        zombie_tasks = session.exec(statement).all()
        if zombie_tasks:
            print(f"[System] 正在重置 {len(zombie_tasks)} 个僵尸任务记录...")
            for t in zombie_tasks:
                t.status = "canceled"
                t.message = "系统重启或非正常终止"
                session.add(t)
            session.commit()
    
    asyncio.create_task(auto_update_task())

@app.get("/")
def read_index():
    """返回主页文件"""
    with open("./static/index.html", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="text/html")
