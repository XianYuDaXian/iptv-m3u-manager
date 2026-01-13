import asyncio
import aiohttp
from datetime import datetime
from sqlmodel import SQLModel

async def check_url(url: str, session: aiohttp.ClientSession):
    try:
        start = datetime.now()
        # 使用自定义 UA
        headers = {"User-Agent": "AptvPlayer/1.4.1"}
        async with session.head(url, headers=headers, timeout=5, allow_redirects=True) as response:
            latency = int((datetime.now() - start).total_seconds() * 1000)
            return {
                "url": url,
                "status": response.status < 400,
                "latency": latency,
                "error": None
            }
    except Exception as e:
         return {
            "url": url,
            "status": False,
            "latency": 0,
            "error": str(e)
        }
