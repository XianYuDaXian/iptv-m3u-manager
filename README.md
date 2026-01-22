# IPTV M3U 管理器

一个简单的 IPTV M3U 订阅聚合与过滤工具，支持自定义频道提取和分组。

### 主要功能
- **聚合订阅**：支持添加多个 M3U 链接，甚至可以直接同步 Git 仓库中的 M3U 文件。
- **精细筛选**：通过关键字或正则表达式提取你想要的频道。
- **自定义分组**：支持将匹配到的频道重新划分为自定义的分组（如：央视、卫视、纪录片等）。
- **Logo、节目补全**：根据频道名称自动补全缺失的台标和节目。
- **自动更新**：支持设置定时任务自动更新订阅源。

### 演示截图

#### PC 端演示
- **PC端演示**
  ![PC端演示](assets/demo.webp)
#### 移动端演示
![移动端演示](assets/mobile_UI.webp)

- **频道有效性检测**
  ![频道检测](assets/stream_check.png)


### 运行指南

#### 方案一：Docker 镜像一键启动（推荐，无需下载源码）
如果你只需使用功能，这是最快的方式。直接运行以下命令即可拉取我们自动构建的镜像：

```bash
# 启动容器
docker run -d \
  --name iptv-manager \
  --restart unless-stopped \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  -e TZ=Asia/Shanghai \
  ghcr.io/xianyudaxian/iptv-m3u-manager:latest
```
> **注意**：
> - Windows PowerShell 用户请将 `$(pwd)` 替换为 `${PWD}`
> - 数据文件将保存在当前目录下的 `data/` 文件夹中。

#### 方案二：Docker Compose 部署（便于管理）
创建一个 `docker-compose.yml` 文件，写入以下内容：

```yaml
version: '3.8'
services:
  iptv-manager:
    image: ghcr.io/xianyudaxian/iptv-m3u-manager:latest
    container_name: iptv-manager
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      - TZ=Asia/Shanghai
```
然后运行：
```bash
docker-compose up -d
```

#### 方案三：本地源码开发
如果你想修改代码或进行二次开发：
1. **下载源码**：
   ```bash
   git clone https://github.com/XianYuDaXian/iptv-m3u-manager.git
   cd iptv-m3u-manager
   ```
2. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   ```
3. **启动程序**：
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```

### 更新日志
- **2026-01-22**
    -🔍 **频道筛选**：筛选功能优化，增加排除频道、统计信息显示
- **2026-01-20**
    -📱 **移动端适配**：移动端适配，支持日间/夜间模式切换
- **2026-01-15**
    -✅ 接入taskiq worker实现全局通知/任务中心
- **2026-01-14**
    -🔍 **深度检测**：支持断点续传，增加根据结果自动启用/禁用频道逻辑。
    -⚙️ **聚合列表**：新增聚合源自动更新频率设置及同步后自动深度检测。
    -🎨 **UI优化**：优化预览窗弹出及编辑滚动交互，减少页面跳变。
- **2026-01-13**
    -🐳 新增 Docker 支持，提供 `docker-compose` 一键部署方案。(因为要Ubuntu指定版本，所以构建时间稍长请耐心等待)
    -🚀 优化 EPG 获取，缓解预览卡顿。
    -✅ 聚合预览新增批量启用、禁用及全选功能。
    -📺 新增深度检测，支持首帧截图并自动下线失效频道。
    -🔗 支持 M3U/TXT/GitHub 格式混排及多地址同时输入。
- **2026-01-12**
    -🎉 项目发布基础功能