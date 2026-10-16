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


# ============ 相似度阈值 ============
SIMILARITY_THRESHOLDS = {
    "high": 0.8,      # 高相似度 - 严重警告
    "medium": 0.5,    # 中相似度 - 注意
    "low": 0.3        # 低相似度 - 安全
}

# ============ 权重预设 ============
# 教师的心智模型是「这道题是短答题还是长作文」，不是配平指标权重，
# 因此给预设档位而不是一堆滑块。加权分做自归一化（Σw·m / Σw），
# 所以权重之和不必等于 1，改档位不会因为配平问题算错分。
WEIGHT_PROFILES = {
    "short_answer": {
        "label": "短答题 / 填空",
        "weights": {
            "exact_match": 0.20,
            "sequence_similarity": 0.10,
            "keyword_overlap": 0.15,
            "char_gram_jaccard": 0.25,
            "char_gram_cosine": 0.20,
            "coverage": 0.10,
        },
    },
    "long_text": {
        "label": "长文本 / 作文",
        "weights": {
            "exact_match": 0.15,
            "sequence_similarity": 0.25,
            "keyword_overlap": 0.20,
            "char_gram_jaccard": 0.10,
            "char_gram_cosine": 0.20,
            "coverage": 0.10,
        },
    },
    "pairwise": {
        "label": "学生互查",
        "weights": {
            "exact_match": 0.25,
            "sequence_similarity": 0.15,
            "keyword_overlap": 0.10,
            "char_gram_jaccard": 0.25,
            "char_gram_cosine": 0.15,
            "coverage": 0.10,
        },
    },
}

DEFAULT_WEIGHT_PROFILE = "short_answer"

# 兼容旧引用：WEIGHTS 指向默认预设
WEIGHTS = WEIGHT_PROFILES[DEFAULT_WEIGHT_PROFILE]["weights"]

# ============ 小样本校准 ============
# 极短的答案（如「不会」两字）即使字面完全相同，也不足以判定抄袭。
# 校准按答案长度对分数做收缩，并在高级设置里可整体关闭。
CALIBRATION_CONFIG = {
    "enabled": True,
    "floor": 0.55,              # 置信度最低时保留的分数比例
    "low_confidence_len": 12,   # 短于此长度按小样本收缩，且不给高风险
    "low_confidence_cap": 0.75, # 低置信度时的分数上限
}

# ============ 大模型接口（OpenAI 兼容） ============
# 默认关闭且不填密钥，程序就不会发出任何外部请求；本地功能完全不依赖它。
# base_url 填到 …/v1 即可，DeepSeek / 通义 / 智谱 / Kimi 都提供兼容端点。
LLM_CONFIG = {
    "enabled": False,
    "base_url": "",
    "model": "",
    "daily_budget": 2.0,          # 元/天，估算花费达到即拒绝发请求；0 表示不限
    "timeout": 30,
    "max_tokens": 900,
    "prices": {"input": 2.0, "output": 8.0},   # 元/百万 token，请按所用厂商改
}

# ============ AIGC 作文风格线索 ============
# 中学作文本身就教八股结构，所以阈值按 K12 校准，输出只叫「线索」不叫「结论」。
AIGC_CONFIG = {
    "weights": {
        "sentence_uniformity": 0.22,
        "template_density": 0.20,
        "abstract_density": 0.20,
        "punctuation_monotony": 0.12,
        "concreteness_deficit": 0.16,
        "structure_regularity": 0.10,
    },
    # 档位按「中学记叙文实测」定的： blatant AI 腔约 0.6 以上，
    # 认真套模板的好学生习作实测在 0.3 上下，不会掉进「中等」这一档。
    "levels": {"strong": 0.60, "medium": 0.40},
    "min_chars": 60,          # 低于此长度不足以看风格，直接拒绝
    "reliable_chars": 200,    # 达到此长度才算置信度满
    "short_text_cap": 0.7,    # 短文的线索强度上限，防止两三句话被判成「强」
    "evidence_max": 6,
    "external_weight": 0.4,   # 外部接口结果在融合分中的占比
    "review_sentences_max": 8,
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
    "default_student_prefix": "学生",
    # 互查模式：并查集聚类的连线阈值（取「中位对数 + 该值」与中风险阈值的较大者）
    "cluster_margin": 0.15,
    "cluster_min_size": 3,
    # 整卷模式一次最多比对的「学生 × 题目」格子数
    "max_paper_cells": 500,
    # 雷同片段：至少多少字才算有意义的证据
    "fragment_min_len": 8,
    "fragment_max_per_result": 20,
}
