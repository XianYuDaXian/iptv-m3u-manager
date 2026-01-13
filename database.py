from sqlmodel import create_engine, Session
from sqlalchemy.orm import sessionmaker

sqlite_url = "sqlite:///./database.db"
# 数据库引擎
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

def get_session():
    # 获取数据库会话
    with Session(engine) as session:
        yield session
