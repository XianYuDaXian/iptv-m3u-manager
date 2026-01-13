import re
import aiohttp
import json
import os
import subprocess
import hashlib
import glob
from models import Channel

class M3UParser:
    """M3U/TXT 解析器"""
    
    @staticmethod
    def parse(content: str):
        """解析播放列表（支持 M3U/TXT 格式）"""
        channels = []
        metadata = {}
        lines = content.splitlines()
        current_channel = None
        current_group = "Default"
        
        # 检查前 20 行的全局 EPG 元数据
    for line in lines[:20]:
            line = line.strip()
            if line.startswith("#EXTM3U"):
                # 提取 x-tvg-url 或 url-tvg 属性
                tvg_match = re.search(r'(?:x-tvg-url|url-tvg|tvg-url)="([^"]*)"', line, re.IGNORECASE)
                if tvg_match:
                    metadata["epg_url"] = tvg_match.group(1)
                break

        for line in lines:
            line = line.strip()
            if not line or line.startswith("//"):
                continue

            # M3U 格式检测 (#EXTINF 标签)
            if line.startswith("#EXTINF"):
                name = "Unknown"
                if "," in line:
                    # 频道名称通常出现在最后的逗号之后
                    name = line.rsplit(",", 1)[1].strip()
                
                # 提取属性 (group-title, tvg-logo 等)
                raw_attrs = re.findall(r'([\w-]+)=(?:"([^"]*)"|([^\s,]*))', line)
                attrs = {}
                for item in raw_attrs:
                    key = item[0].lower()
                    val = item[1] or item[2]
                    attrs[key] = val
                
                current_channel = {
                    "name": name,
                    "group": attrs.get("group-title") or attrs.get("group") or current_group,
                    "logo": attrs.get("tvg-logo") or attrs.get("logo") or "",
                    "tvg_id": attrs.get("tvg-id") or attrs.get("id") or ""
                }
            # TXT 分组标题检测
            elif ",#genre#" in line:
                current_group = line.split(",")[0].strip()
            # 链接行检测
            elif any(line.lower().startswith(p) for p in ["http", "rtmp", "p3p", "rtp", "udp", "mms", "rtsp"]):
                if current_channel:
                    # 如果前一行是 #EXTINF，则填充其 URL
                    current_channel["url"] = line
                    channels.append(current_channel)
                    current_channel = None
                else:
                    # 如果没有对应的 #EXTINF，则视为独立的 URL 行
                    channels.append({
                        "name": line.split("/")[-1],
                        "url": line,
                        "group": current_group,
                        "logo": "",
                        "tvg_id": ""
                    })
            # TXT 行检测 (频道名,链接)
            else:
                for sep in [",", "#"]:
                    if sep in line:
                        parts = line.split(sep, 1)
                        name = parts[0].strip()
                        url = parts[1].strip()
                        # 验证 URL 部分是否符合协议
                        if any(url.lower().startswith(p) for p in ["http", "rtmp", "p3p", "rtp", "udp", "mms", "rtsp"]):
                            channels.append({
                                "name": name,
                                "url": url,
                                "group": current_group,
                                "logo": "",
                                "tvg_id": ""
                            })
                            break

        print(f"解析完成：共 {len(channels)} 个频道。元数据：{metadata}")
        return channels, metadata

class IPTVFetcher:
    """订阅抓取工具"""
    
    @staticmethod
    def process_git_repo(url: str):
        """处理 Git 仓库"""
        repo_cache_base = "repo_cache"
        if not os.path.exists(repo_cache_base):
            os.makedirs(repo_cache_base)
            
        # 生成唯一目录名
        url_hash = hashlib.md5(url.encode()).hexdigest()
        repo_dir = os.path.join(repo_cache_base, url_hash)
        
        print(f"正在处理 Git 仓库: {url} -> {repo_dir}")
        
        try:
            if os.path.exists(os.path.join(repo_dir, ".git")):
                # 已存在，拉取更新
                print("仓库已存在，正在拉取更新...")
                try:
                    subprocess.check_call(["git", "-C", repo_dir, "pull"], timeout=60)
                except Exception as e:
                    # 如果拉取失败 (例如 Windows 上的文件锁定)，尝试删除并重新克隆
                    print(f"Git pull 失败: {e}。正在尝试重新克隆...")
                    import shutil
                    shutil.rmtree(repo_dir, ignore_errors=True)
                    subprocess.check_call(["git", "clone", "--depth", "1", url, repo_dir], timeout=120)
            else:
                # 克隆仓库，使用 --depth 1 以节省空间和时间
                print("正在克隆仓库...")
                subprocess.check_call(["git", "clone", "--depth", "1", url, repo_dir], timeout=120)
        except subprocess.CalledProcessError as e:
            print(f"Git 命令执行失败: {e}")
            if os.path.exists(repo_dir):
                 import shutil
                 shutil.rmtree(repo_dir, ignore_errors=True)
            raise Exception(f"Git 操作失败: {e}")
        except Exception as ex:
             print(f"Git 错误: {ex}")
             raise Exception(f"Git 错误: {ex}")

        # 扫描 M3U 或 TXT 文件
        source_files = []
        for root, dirs, files in os.walk(repo_dir):
            # 跳过隐藏目录 (如 .git)
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for file in files:
                if file.lower().endswith(('.m3u', '.m3u8', '.txt')):
                    # 跳过常见的说明文件和依赖文件
                    if file.lower() in ["readme.txt", "requirements.txt", "license.txt"]:
                        continue
                    source_files.append(os.path.join(root, file))
        
        print(f"在仓库中发现 {len(source_files)} 个源文件。")
        
        all_channels = []
        for fpath in source_files:
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    if not content.strip():
                        continue
                    channels, _ = M3UParser.parse(content)
                    all_channels.extend(channels)
            except Exception as e:
                print(f"读取文件错误 {fpath}: {e}")
        
        print(f"从仓库中提取的总频道数: {len(all_channels)}")
        return all_channels

    @staticmethod
    def is_git_url(url: str) -> bool:
        """简单判断是否为 Git URL"""
        url_lower = url.lower()
        return url_lower.endswith(".git") or (
            "github.com" in url_lower and 
            "/tree/" not in url_lower and 
            "/blob/" not in url_lower and 
            not url_lower.endswith((".m3u", ".m3u8"))
        )

    @staticmethod
    async def fetch_subscription(url_str: str, ua: str, headers_json: str):
        """核心抓取函数"""
        # 支持逗号分隔多个地址
        urls = [u.strip() for u in url_str.split(",") if u.strip()]
        
        all_channels = []
        all_metadata = {}

        # 如果未指定 UA，使用默认的 Aptv UA
        if not ua or ua == "Mozilla/5.0":
            ua = "AptvPlayer/1.4.1"
            
        try:
            headers = json.loads(headers_json)
        except:
            headers = {}
        headers["User-Agent"] = ua

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            for url in urls:
                print(f"--- 正在处理源: [{url}] ---")
                try:
                    # 识别是否为 Git 仓库
                    if IPTVFetcher.is_git_url(url):
                         import asyncio
                         loop = asyncio.get_event_loop()
                         # 在线程池中执行耗时的 Git 操作
                         repo_channels = await loop.run_in_executor(None, IPTVFetcher.process_git_repo, url)
                         all_channels.extend(repo_channels)
                         continue

                    # 普通 HTTP 抓取
                    async with session.get(url, headers=headers, timeout=30) as response:
                        print(f"抓取响应状态: {response.status} (URL: {url})")
                        if response.status == 200:
                            content = await response.text(errors='ignore')
                            # 防御性检查：如果是 HTML 而非播放列表，则跳过
                            if "<html" in content.lower() and "#EXTM3U" not in content:
                                print(f"警告: 链接 {url} 返回了网页而非播放列表，已跳过。")
                                continue
                            
                            channels, metadata = M3UParser.parse(content)
                            all_channels.extend(channels)
                            # 如果发现了 EPG URL 等元数据，进行合并
                            if metadata:
                                all_metadata.update(metadata)
                        else:
                            print(f"跳过 {url}: HTTP {response.status}")
                except Exception as e:
                    print(f"处理 {url} 时发生错误: {e}")
                    # 一个源失败后继续处理下一个源
        
        print(f"订阅汇总完成：从 {len(urls)} 个源中共提取 {len(all_channels)} 个频道。")
        return all_channels, all_metadata
