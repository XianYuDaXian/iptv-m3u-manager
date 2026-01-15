# 使用 Ubuntu 22.04 以完全复现生产环境 (FFmpeg 4.4.2)
FROM ubuntu:22.04

# 设置环境变量，防止交互式安装卡住
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 更新源并安装 Python 3.10、FFmpeg、Git 等依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    git \
    ffmpeg \
    curl \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 建立 python 命令的软链接 (ubuntu 默认只有 python3)
RUN ln -s /usr/bin/python3 /usr/bin/python

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装
COPY requirements.txt .
# --break-system-packages 允许在非 venv 环境下安装 (适用较新 pip)
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages || pip3 install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 复制并设置入口脚本权限
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 暴露端口
EXPOSE 8000

# 定义数据卷挂载点
VOLUME ["/data"]

# 设置入口点
ENTRYPOINT ["/entrypoint.sh"]

# 默认启动命令
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
