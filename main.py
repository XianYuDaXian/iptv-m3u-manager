from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
import asyncio
import os
from datetime import datetime

from database import engine, create_engine, sqlite_url
from models import SQLModel, Subscription, Channel, OutputSource
from routers import subscriptions, outputs, tools, channels

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

async def auto_update_task():
    """后台自动同步订阅"""
    while True:
        try:
            with Session(engine) as session:
                subs = session.exec(select(Subscription)).all()
                for sub in subs:
                    if sub.auto_update_minutes > 0:
                        now = datetime.utcnow()
                        last = sub.last_updated or datetime.min
                        elapsed_mins = (now - last).total_seconds() / 60
                        
                        if elapsed_mins >= sub.auto_update_minutes:
                            print(f"[自动更新] 正在刷新订阅 {sub.id} ({sub.name})。已耗时: {elapsed_mins:.1f}分钟")
                            try:
                                from routers.subscriptions import process_subscription_refresh
                                count = await process_subscription_refresh(session, sub)
                                print(f"[自动更新] 订阅 {sub.id} 已同步。共提取到 {count} 个频道。")
                            except Exception as e:
                                print(f"[自动更新] 订阅 {sub.id} 刷新失败: {e}")
                                sub.last_update_status = f"AutoUpdate Error: {str(e)}"
                                session.add(sub)
                                session.commit()
        except Exception as outer_e:
            print(f"[自动更新] 循环发生错误: {outer_e}")
            
        await asyncio.sleep(60) # 每隔 1 分钟检查一次

@app.on_event("startup")
def on_startup():
    """启动时初始化"""
    create_db_and_tables()
    migrate_db()
    asyncio.create_task(auto_update_task())

@app.get("/")
def read_index():
    """返回主页文件"""
    with open("./static/index.html", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="text/html")
