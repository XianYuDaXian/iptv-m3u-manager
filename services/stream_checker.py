import asyncio
import base64
import os
import subprocess
import shutil
import uuid
import tempfile
import json
from datetime import datetime, timedelta
from sqlmodel import Session, select
from static_ffmpeg import run

class StreamChecker:
    _ffmpeg_path = None

    @classmethod
    def get_ffmpeg_path(cls):
        """获取并验证 FFmpeg 路径"""
        if cls._ffmpeg_path:
            return cls._ffmpeg_path

        # 1. 优先尝试系统路径中的 ffmpeg
        sys_ffmpeg = shutil.which("ffmpeg")
        if sys_ffmpeg:
            try:
                # 简单验证是否能跑
                subprocess.run([sys_ffmpeg, "-version"], capture_output=True, timeout=2)
                cls._ffmpeg_path = sys_ffmpeg
                print(f"DEBUG: 使用系统 FFmpeg: {sys_ffmpeg}")
                return cls._ffmpeg_path
            except Exception as e:
                print(f"DEBUG: 系统 FFmpeg ({sys_ffmpeg}) 运行失败: {e}")

        # 2. 尝试 static-ffmpeg 下载的二进制
        try:
            static_ffmpeg = run.get_or_fetch_platform_executables_else_raise()[0]
            try:
                subprocess.run([static_ffmpeg, "-version"], capture_output=True, timeout=2)
                cls._ffmpeg_path = static_ffmpeg
                print(f"DEBUG: 使用 static-ffmpeg 二进制: {static_ffmpeg}")
                return cls._ffmpeg_path
            except Exception as e:
                print(f"DEBUG: static-ffmpeg 二进制 ({static_ffmpeg}) 运行失败: {e}")
        except Exception as e:
            print(f"DEBUG: 获取 static-ffmpeg 二进制失败: {e}")

        # 最后兜底
        cls._ffmpeg_path = "ffmpeg"
        print(f"DEBUG: 未找到有效 FFmpeg，兜底使用命令: {cls._ffmpeg_path}")
        return cls._ffmpeg_path

    @classmethod
    async def check_stream_visual(cls, url: str) -> dict:
        ffmpeg_exe = cls.get_ffmpeg_path()
        temp_filename = os.path.join(tempfile.gettempdir(), f"capture_{uuid.uuid4()}.jpg")
        
        # 使用 -user_agent 参数代替 -headers，并在 -i 前增加 -t 限制探测时长
        cmd = [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-t", "5",          # 输入探测阶段限时 5 秒
            "-user_agent", "AptvPlayer/1.4.1",
            "-i", url,
            "-an", "-sn",       # 禁用音频和字幕
            "-frames:v", "1",
            "-vf", "scale=320:-1",
            "-f", "image2",
            "-c:v", "mjpeg",
            temp_filename 
        ]

        print(f"DEBUG: 执行截图命令: {' '.join(cmd)}")

        try:
            def run_ffmpeg():
                # env 使用 os.environ.copy() 确保在 LXC 环境下的变量继承
                return subprocess.run(
                    cmd, 
                    capture_output=True, 
                    timeout=15,
                    env=os.environ.copy()
                )

            result = await asyncio.to_thread(run_ffmpeg)
            
            if result.returncode == 0 and os.path.exists(temp_filename) and os.path.getsize(temp_filename) > 0:
                with open(temp_filename, "rb") as f:
                    img_data = f.read()
                
                b64 = base64.b64encode(img_data).decode('utf-8')
                return {"url": url, "status": True, "image": f"data:image/jpeg;base64,{b64}"}
            else:
                err_msg = result.stderr.decode('utf-8', errors='ignore') if result.stderr else "FFmpeg produced no image."
                
                if result.returncode == -11 or result.returncode == 139:
                    err_msg = f"FFmpeg 进程崩溃 (SIGSEGV, RC={result.returncode})。LXC 容器建议安装系统官方软件包。"
                
                print(f"DEBUG: [{url}] 检测失败 (RC={result.returncode}): {err_msg[:200]}")
                return {"url": url, "status": False, "error": err_msg[:100]}

        except subprocess.TimeoutExpired:
            print(f"DEBUG: [{url}] 检测超时")
            return {"url": url, "status": False, "error": "Detection Timeout"}
        except Exception as e:
            print(f"DEBUG: 运行异常: {e}")
            return {"url": url, "status": False, "error": str(e)}
        finally:
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except:
                    pass

    @classmethod
    async def run_batch_check(cls, session: Session, channels, concurrency: int = 5, source: str = 'manual'):
        """
        分批执行多个频道的深度检测，并更新数据库
        :param session: 数据库会话
        :param channels: Channel 模型对象列表
        :param concurrency: 并发数
        :param source: 检测来源 (manual/auto/other)
        """
        if not channels:
            return

        sem = asyncio.Semaphore(concurrency)

        async def _bounded_check(ch):
            async with sem:
                res = await cls.check_stream_visual(ch.url)
                return {**res, "ch_id": ch.id}

        tasks = [_bounded_check(ch) for ch in channels]
        results = await asyncio.gather(*tasks)

        # 更新数据库
        for res in results:
            if res.get('ch_id'):
                ch = session.get(cls._get_channel_model(), res['ch_id'])
                if ch:
                    ch.check_status = res['status']
                    ch.check_date = datetime.utcnow()
                    ch.check_image = res.get('image')
                    ch.check_error = res.get('error') if not res['status'] else None
                    ch.check_source = source
                    # 根据深度检测结果自动处理
                    ch.is_enabled = res['status']
                    session.add(ch)
        
        session.commit()

    @staticmethod
    def _get_channel_model():
        # 避免循环导入
        from models import Channel
        return Channel
