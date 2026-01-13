
import asyncio
import base64
import os
import subprocess
from static_ffmpeg import run

print("!!! LOADING STREAM CHECKER MODULE !!!")

class StreamChecker:
    _ffmpeg_path = None

    @classmethod
    def get_ffmpeg_path(cls):
        if not cls._ffmpeg_path:
            # 自动下 FFmpeg
            cls._ffmpeg_path = run.get_or_fetch_platform_executables_else_raise()[0]
        return cls._ffmpeg_path

    @classmethod
    async def check_stream_visual(cls, url: str) -> dict:
        ffmpeg_exe = cls.get_ffmpeg_path()
        
        # 用临时文件解决 Windows 下的管道问题
        import uuid
        import tempfile
        
        # 系统临时目录
        temp_filename = os.path.join(tempfile.gettempdir(), f"capture_{uuid.uuid4()}.jpg")
        
        cmd = [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel", "warning",
            "-headers", "User-Agent: AptvPlayer/1.4.1\r\n",
            "-i", url,
            "-frames:v", "1",
            "-vf", "scale=320:-1",
            "-f", "image2",
            "-c:v", "mjpeg",
            temp_filename 
        ]

        print(f"DEBUG: Running visual check for {url} -> {temp_filename}")

        print(f"DEBUG: Running visual check for {url} -> {temp_filename}")

        try:
            # 运行 FFmpeg 截图
            
            def run_ffmpeg():
                return subprocess.run(
                    cmd, 
                    capture_output=True, 
                    timeout=10
                )

            result = await asyncio.to_thread(run_ffmpeg)
            
            if result.returncode == 0 and os.path.exists(temp_filename) and os.path.getsize(temp_filename) > 0:
                # 成功
                with open(temp_filename, "rb") as f:
                    img_data = f.read()
                
                b64 = base64.b64encode(img_data).decode('utf-8')
                return {"url": url, "status": True, "image": f"data:image/jpeg;base64,{b64}"}
            else:
                err_msg = result.stderr.decode('utf-8', errors='ignore') if result.stderr else "Check Failed (No image produced)"
                print(f"DEBUG: FFmpeg failed [RC={result.returncode}]: {err_msg}")
                return {"url": url, "status": False, "error": err_msg[:200]}

        except subprocess.TimeoutExpired:
            print(f"DEBUG: Timeout for {url}")
            return {"url": url, "status": False, "error": "Timeout"}
        except Exception as e:
            return {"url": url, "status": False, "error": str(e)}
        finally:
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                except:
                    pass
