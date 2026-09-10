"""
作业抄袭检测系统 - 配置文件
"""

import os
import sys


def _app_base_dir() -> str:
    """可写文件的根目录。

    打包为 exe 后（sys.frozen）返回可执行文件所在目录，避免写入只读的临时解包目录；
    普通运行时返回本文件所在的项目目录。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _app_base_dir()
IS_FROZEN = getattr(sys, "frozen", False)


# ============ API 配置 ============
API_CONFIG = {
    "zuoyebang": {
        "url": "https://api.zuoyebang.com/search/v1",
        "timeout": 10,
        "enabled": True
    },
    "xiaoyuansouti": {
        "url": "https://api.xiaoyuansouti.com/v1/search",
        "timeout": 10,
        "enabled": True
    }
}

# ============ 相似度阈值 ============
SIMILARITY_THRESHOLDS = {
    "high": 0.8,      # 高相似度 - 严重警告
    "medium": 0.5,    # 中相似度 - 注意
    "low": 0.3        # 低相似度 - 安全
}

# ============ 权重配置 ============
WEIGHTS = {
    "exact_match": 0.4,       # 精确匹配权重
    "sequence_similarity": 0.35,  # 序列相似度权重
    "keyword_overlap": 0.25   # 关键词重叠权重
}

# ============ 报警配置 ============
ALERT_CONFIG = {
    "console": True,
    "log_file": os.path.join(BASE_DIR, "plagiarism_alerts.log"),
    "log_level": "INFO"
}

# ============ Web 应用配置 ============
WEB_CONFIG = {
    "host": "0.0.0.0",
    "port": 5000,
    "debug": False if IS_FROZEN else True,
    "secret_key": "anticopy-secret-key-2024",
    "upload_folder": os.path.join(BASE_DIR, "uploads"),
    "max_content_length": 16 * 1024 * 1024  # 16MB
}

# ============ 检测配置 ============
DETECTION_CONFIG = {
    "max_batch_size": 50,
    "supported_image_types": ["jpg", "jpeg", "png", "bmp"],
    "supported_text_types": ["txt"],
    "default_student_prefix": "学生"
}
