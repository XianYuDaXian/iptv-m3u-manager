import os
import gzip
import io
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from hashlib import md5
from datetime import datetime
from typing import Dict, Any

# EPG 缓存目录
EPG_CACHE_DIR = "./epg_cache"
if not os.path.exists(EPG_CACHE_DIR):
    os.makedirs(EPG_CACHE_DIR, exist_ok=True)

# 并发控制锁，防止重复下载/解析
_url_locks: Dict[str, asyncio.Lock] = {}
_locks_lock = asyncio.Lock()

async def fetch_epg_cached(url: str, refresh: bool = False) -> str:
    """下载并缓存 EPG"""
    if not url:
        return None
        
    url_hash = md5(url.encode()).hexdigest()
    cache_path = os.path.join(EPG_CACHE_DIR, f"{url_hash}.xml")
    
    # 1. 检查是否可以跳过下载（非强制执行且本地缓存存在）
    if not refresh and os.path.exists(cache_path):
        return cache_path

    # 2. 获取针对该 URL 的锁，防止冗余的并发下载
    async with _locks_lock:
        if url_hash not in _url_locks:
            _url_locks[url_hash] = asyncio.Lock()
        lock = _url_locks[url_hash]
    
    async with lock:
        # 双重检查：可能在我们等待锁的过程中，另一个请求已经下载完成了
        if not refresh and os.path.exists(cache_path):
            return cache_path
            
        print(f"正在下载 EPG: {url}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status != 200:
                        return None
                    content = await response.read()
                    
                    # 解压 gzip
                    if url.endswith(".gz") or content[:2] == b'\x1f\x8b':
                        try:
                            with gzip.GzipFile(fileobj=io.BytesIO(content)) as gz:
                                xml_content = gz.read()
                        except:
                            xml_content = content
                    else:
                        xml_content = content
                        
                    with open(cache_path, "wb") as f:
                        f.write(xml_content)
            return cache_path
        except Exception as e:
            print(f"下载 EPG 失败 {url}: {e}")
            return None

class EPGManager:
    """EPG 管理器"""
    _cache: Dict[str, Dict[str, Any]] = {}
    
    @classmethod
    async def get_program(cls, epg_url: str, channel_id: str, channel_name: str, current_logo: str = None, refresh: bool = False) -> dict:
        """获取频道节目"""
        if not epg_url: return {"title": "无 EPG 链接", "logo": None}
        
        url_hash = md5(epg_url.encode()).hexdigest()
        
        # 1. 内存缓存查找
        if not refresh and url_hash in cls._cache:
            return cls._lookup_in_memory(cls._cache[url_hash], channel_id, channel_name, current_logo)
            
        # 2. 获取针对该 URL 的解析锁
        async with _locks_lock:
            if url_hash not in _url_locks:
                _url_locks[url_hash] = asyncio.Lock()
            lock = _url_locks[url_hash]
            
        async with lock:
            # 3. 再次检查缓存（双重锁）
            if not refresh and url_hash in cls._cache:
                return cls._lookup_in_memory(cls._cache[url_hash], channel_id, channel_name, current_logo)
                
            # 4. 获取本地文件（必要时下载）
            xml_path = await fetch_epg_cached(epg_url, refresh=refresh)
            if not xml_path or not os.path.exists(xml_path):
                return {"title": "抓取失败", "logo": None}
                
            # 5. 解析并建立内存索引
            try:
                parsed_data = cls._parse_epg_file(xml_path)
                cls._cache[url_hash] = {
                    "timestamp": datetime.now().timestamp(),
                    "programs": parsed_data["programs"],
                    "name_map": parsed_data["name_map"],
                    "logos": parsed_data["logos"],
                    "reverse_logos": parsed_data["reverse_logos"]
                }
                return cls._lookup_in_memory(cls._cache[url_hash], channel_id, channel_name, current_logo)
            except Exception as e:
                print(f"EPG 索引错误: {e}")
                return {"title": "解析错误", "logo": None}

    @staticmethod
    def _clean_name(name: str) -> str:
        """清洗名称用于模糊匹配"""
        if not name: return ""
        import re
        # 移除 ()、[]、【】 及其内部内容
        name = re.sub(r'[\(\[【].*?[\)\]】]', '', name)
        # 移除常见港台干扰词
        name = name.replace("TVB", "").replace("HD", "").replace("高清", "").replace("频道", "")
        return name.strip()

    @staticmethod
    def _lookup_in_memory(cache_entry, channel_id, channel_name, current_logo=None):
        """多重查找策略"""
        programs = cache_entry["programs"]
        name_map = cache_entry["name_map"]
        logos = cache_entry.get("logos", {})
        reverse_logos = cache_entry.get("reverse_logos", {})
        
        target_ids = set()
        
        # 策略 A：精确 ID
        if channel_id: target_ids.add(channel_id)
        
        # 策略 B：全名映射
        if channel_name:
            target_ids.add(channel_name)
            if channel_name in name_map: target_ids.add(name_map[channel_name])
            
            # 策略 C：模糊匹配
            cleaned = EPGManager._clean_name(channel_name)
            if cleaned and cleaned != channel_name:
                if cleaned in name_map: 
                    target_ids.add(name_map[cleaned])
        
        # 策略 D：台标匹配
        if current_logo:
             if current_logo in reverse_logos:
                 target_ids.add(reverse_logos[current_logo])

        if channel_id and channel_id in name_map: target_ids.add(name_map[channel_id])
            
        now_str = datetime.now().strftime("%Y%m%d%H%M%S")
        found_title = "无节目信息"
        found_logo = None
        
        # 遍历所有可能的 ID 进行匹配
        for tid in target_ids:
            # 查找匹配当前时间的节目片段
            if tid in programs and found_title == "无节目信息":
                for start, stop, title in programs[tid]:
                    if start <= now_str <= stop: 
                        found_title = title
                        break
            
            # 查找台标（如果有）
            if tid in logos and not found_logo:
                found_logo = logos[tid]
                
            # 如果两个都找到了就提前退出
            if found_title != "无节目信息" and found_logo:
                break
                
        return {"title": found_title, "logo": found_logo}

    @staticmethod
    def _parse_epg_file(xml_path):
        """流式解析 XML 以节省内存"""
        programs = {}
        name_map = {}
        logos = {}
        reverse_logos = {}
        context = ET.iterparse(xml_path, events=("start", "end"))
        _, root = next(context)
        for event, elem in context:
            if event == "end":
                if elem.tag == "channel":
                    # 解析频道定义
                    cid = elem.get("id")
                    dn = elem.find("display-name")
                    if cid and dn is not None and dn.text:
                        name_map[dn.text.strip()] = cid
                    
                    # 提取台标
                    icon = elem.find("icon")
                    if cid and icon is not None:
                        src = icon.get("src")
                        if src:
                            logos[cid] = src
                            reverse_logos[src] = cid
                        
                elif elem.tag == "programme":
                    # 解析节目预告
                    chan = elem.get("channel")
                    start = elem.get("start", "")[:14] # 取 YYYYMMDDHHMMSS 格式
                    stop = elem.get("stop", "")[:14]
                    title_elem = elem.find("title")
                    title = title_elem.text if title_elem is not None else "未知节目"
                    if chan and start and stop:
                        if chan not in programs: programs[chan] = []
                        programs[chan].append((start, stop, title))
                    # 解析完清理一下，防止爆内存
                    root.clear()
        return {"programs": programs, "name_map": name_map, "logos": logos, "reverse_logos": reverse_logos}
