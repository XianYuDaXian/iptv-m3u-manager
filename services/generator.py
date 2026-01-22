import re
from typing import List, Dict
from models import Channel

class M3UGenerator:
    """M3U 生成器"""
    
    @staticmethod
    def filter_channels(channels: List[Channel], regex_pattern: str, keywords: List[dict] = None, excluded_ids: List[int] = None) -> List[Channel]:
        """根据关键字和正则筛选频道，并排除指定 ID 的频道"""
        # 转换排除列表为集合，便于快速查找
        excluded_set = set(excluded_ids) if excluded_ids else set()
        
        filtered = []
        
        # 关键字筛选
        if keywords:
            # 使用 Set 避免同一个频道匹配多个关键字时出现重复
            seen_ids = set()
            seen_urls = set() # 增加 URL 去重，防止不同订阅源中的相同频道
            
            for k_obj in keywords:
                k_val = k_obj.get("value", "").lower()
                target_group = k_obj.get("group", "").strip()
                if not k_val:
                    continue
                    
                for c in channels:
                    if c.id in seen_ids:
                        continue
                    if c.url in seen_urls:
                        continue
                    # 跳过聚合表级别排除的频道
                    if c.id in excluded_set:
                        continue
                        
                    if k_val in c.name.lower():
                        # 命中关键字
                        c_copy = c.model_copy()
                        
                        # 如果指定了新分组，则覆盖原分组
                        if target_group:
                            c_copy.group = target_group
                            
                        filtered.append(c_copy)
                        seen_ids.add(c.id)
                        seen_urls.add(c.url)
        else:
            # 没关键字就按 URL 去重
            seen_urls = set()
            for c in channels:
                # 跳过聚合表级别排除的频道
                if c.id in excluded_set:
                    continue
                if c.url not in seen_urls:
                    filtered.append(c.model_copy())
                    seen_urls.add(c.url)
            
        # 正则筛选
        if regex_pattern and regex_pattern != ".*":
            try:
                pattern = re.compile(regex_pattern, re.IGNORECASE)
                filtered = [c for c in filtered if pattern.search(c.name)]
            except re.error:
                # 如果正则格式不正确，则跳过
                pass
                
        return filtered

    @staticmethod
    def propagate_logos(channels: List[Channel]) -> List[Channel]:
        """台标自动补全"""
        # 构建 ID/名称 -> 有效台标的映射表
        id_logo_map = {}
        
        # 收集有效台标
        for c in channels:
            if c.logo:
                key = c.tvg_id if c.tvg_id else c.name
                if key and key not in id_logo_map:
                    id_logo_map[key] = c.logo
        
        # 2. 补全缺失台标
        for c in channels:
            if not c.logo:
                key = c.tvg_id if c.tvg_id else c.name
                if key and key in id_logo_map:
                    c.logo = id_logo_map[key]
                    
        return channels

    @staticmethod
    def generate_m3u(channels: List[Channel], sub_map: Dict[int, str] = None, epg_url: str = None, include_suffix: bool = True) -> str:
        """生成 M3U 文本"""
        # 顺便补下台标
        channels = M3UGenerator.propagate_logos(channels)

        header = "#EXTM3U"
        if epg_url:
            header += f' x-tvg-url="{epg_url}"'
        lines = [header]
        
        for c in channels:
            # 开启后缀显示，就把来源贴在名后面
            source_tag = f" ({sub_map[c.subscription_id]})" if include_suffix and sub_map and c.subscription_id in sub_map else ""
            display_name = f"{c.name}{source_tag}"
            
            # 构建属性字符串：logo, tvg-id, tvg-name (保留原始名称用于 EPG 匹配)
            logo_attr = f' tvg-logo="{c.logo or ""}"'
            tvg_id_attr = f' tvg-id="{c.tvg_id or ""}"'
            tvg_name_attr = f' tvg-name="{c.name}"'
            group_attr = f' group-title="{c.group or "Default"}"'
            
            inf = f'#EXTINF:-1{tvg_id_attr}{tvg_name_attr}{logo_attr}{group_attr},{display_name}'
            lines.append(inf)
            lines.append(c.url)
        return "\n".join(lines)
