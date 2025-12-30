# 使用官方的 Python 基础镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

ENV TZ=Asia/Shanghai

# 2. 全面替换 APT 源为阿里云镜像
# 首先检查并备份所有可能的源文件
RUN set -eux; \
    # 备份主源文件（如果存在）
    if [ -f /etc/apt/sources.list ]; then \
        cp /etc/apt/sources.list /etc/apt/sources.list.bak; \
    fi; \
    # 备份源目录中的所有文件（如果存在）
    if [ -d /etc/apt/sources.list.d ]; then \
        cp -r /etc/apt/sources.list.d /etc/apt/sources.list.d.bak; \
    fi; \
    # 创建新的主源文件，使用阿里云 trixie 源
    echo "deb https://mirrors.aliyun.com/debian/ trixie main contrib non-free non-free-firmware" > /etc/apt/sources.list; \
    echo "deb https://mirrors.aliyun.com/debian/ trixie-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    echo "deb https://mirrors.aliyun.com/debian/ trixie-backports main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    echo "deb https://mirrors.aliyun.com/debian-security trixie-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    # 清理 sources.list.d 目录中可能存在的其他源
    rm -rf /etc/apt/sources.list.d/*; \
    # 更新证书
    update-ca-certificates;

# 3. 为 Python Pip 换源（阿里云 PyPI 源）
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip config set global.trusted-host mirrors.aliyun.com

# 安装 supervisord 作为进程管理工具
RUN apt-get update && apt-get install -y --no-install-recommends supervisor && rm -rf /var/lib/apt/lists/*

# 复制项目文件&创建必要的文件夹
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p log data conf
COPY biz ./biz
COPY fonts ./fonts
COPY api.py ./api.py
COPY ui.py ./ui.py
COPY conf/prompt_templates.yml ./conf/prompt_templates.yml
COPY conf/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# 暴露 Flask 和 Streamlit 的端口
EXPOSE 5001 5002

# 使用 supervisord 作为启动命令
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]