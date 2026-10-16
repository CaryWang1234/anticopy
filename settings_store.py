"""
作业抄袭检测系统 - 设置持久化

集中管理权重预设、风险阈值、校准与归一化等可调项，落盘到 BASE_DIR/settings.json。

关键点：路径必须走 config.BASE_DIR（打包后是可执行文件所在目录）。
PyInstaller 的 sys._MEIPASS 是只读临时解包目录，写进去重启即丢。

写入采用「先写临时文件再 os.replace」的原子替换，避免中途断电留下半个文件。
"""

import json
import os

from config import (
    BASE_DIR, WEIGHT_PROFILES, DEFAULT_WEIGHT_PROFILE, WEIGHTS,
    SIMILARITY_THRESHOLDS, CALIBRATION_CONFIG, DETECTION_CONFIG,
    LLM_CONFIG, AIGC_CONFIG,
)
from similarity import METRIC_KEYS

SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
SETTINGS_VERSION = 2

# 允许的取值区间：防止手改 JSON 或异常请求把参数改到荒谬的值
_LIMITS = {
    "calibration.floor": (0.0, 0.95),
    "calibration.low_confidence_len": (1, 200),
    "calibration.low_confidence_cap": (0.3, 1.0),
    "fragments.min_len": (4, 50),
    "fragments.max_per_result": (1, 100),
    "cluster_margin": (0.0, 0.5),
    "llm.daily_budget": (0.0, 1000.0),
    "llm.timeout": (5.0, 120.0),
    "llm.max_tokens": (100.0, 4000.0),
    "aigc.min_chars": (10.0, 2000.0),
    "aigc.reliable_chars": (50.0, 3000.0),
    "aigc.short_text_cap": (0.3, 1.0),
    "aigc.evidence_max": (1.0, 30.0),
    "aigc.external_weight": (0.0, 0.6),
    "aigc.review_sentences_max": (1.0, 20.0),
}


def _nested_copy(source: dict) -> dict:
    """浅拷贝一层，内层字典另做拷贝（本项目的设置最多嵌两层）。"""
    return {key: (dict(value) if isinstance(value, dict) else value)
            for key, value in source.items()}


def defaults() -> dict:
    """出厂设置：与 config.py 中的常量保持一致。"""
    return {
        "version": SETTINGS_VERSION,
        "profile": DEFAULT_WEIGHT_PROFILE,
        "weights": dict(WEIGHTS),
        "thresholds": dict(SIMILARITY_THRESHOLDS),
        "calibration": dict(CALIBRATION_CONFIG),
        "fragments": {
            "min_len": DETECTION_CONFIG["fragment_min_len"],
            "max_per_result": DETECTION_CONFIG["fragment_max_per_result"],
        },
        "normalize": {
            "traditional": False,
        },
        "cluster_margin": DETECTION_CONFIG["cluster_margin"],
        "llm": _nested_copy(LLM_CONFIG),
        "aigc": _nested_copy(AIGC_CONFIG),
    }


def profiles() -> dict:
    """供界面展示的预设档位（含标签）。"""
    return {
        name: {"label": item["label"], "weights": dict(item["weights"])}
        for name, item in WEIGHT_PROFILES.items()
    }


def _number(value, fallback, low=None, high=None):
    """把输入转成数字并夹到区间内；无法转换则退回默认值。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:        # NaN
        return fallback
    if low is not None:
        number = max(low, number)
    if high is not None:
        number = min(high, number)
    return number


def _apply_limits(settings: dict) -> dict:
    """按 _LIMITS 逐项夹取区间。"""
    base = defaults()
    for path, (low, high) in _LIMITS.items():
        section, dot, key = path.partition(".")
        if dot and isinstance(settings.get(section), dict):
            fallback = base.get(section, {}).get(key, 0.0)
            settings[section][key] = _number(settings[section].get(key), fallback, low, high)
        else:
            settings[path] = _number(settings.get(path), base.get(path, 0.0), low, high)
    return settings


def _same_weights(left: dict, right: dict) -> bool:
    """判断两份权重是否等价（用于判断是否已偏离预设档位）。"""
    return all(abs(left.get(key, 0.0) - right.get(key, 0.0)) < 1e-9
               for key in METRIC_KEYS)


def sanitize(raw: dict) -> dict:
    """
    把任意输入（磁盘上的文件或请求体）整理成合法设置。

    原则：任何非法输入都不抛错，只退回默认值——设置面板不该因为一个坏值就 500。
    """
    settings = defaults()
    if not isinstance(raw, dict):
        return settings

    # 档位：选预设就整体套用该预设权重
    profile = raw.get("profile")
    if profile in WEIGHT_PROFILES:
        settings["profile"] = profile
        settings["weights"] = dict(WEIGHT_PROFILES[profile]["weights"])

    # 权重：在（预设或当前）基础上覆盖，缺项保留；与预设不一致就算自定义
    incoming_weights = raw.get("weights")
    if isinstance(incoming_weights, dict):
        merged = dict(settings["weights"])
        for key in METRIC_KEYS:
            if key in incoming_weights:
                merged[key] = _number(incoming_weights[key], merged.get(key, 0.0), 0.0, 1.0)
        settings["weights"] = merged
        preset = WEIGHT_PROFILES.get(settings["profile"], {}).get("weights")
        if not preset or not _same_weights(merged, preset):
            settings["profile"] = "custom"
    elif profile and profile not in WEIGHT_PROFILES:
        settings["profile"] = "custom"

    # 阈值：必须严格递减，否则整体退回默认（避免出现「高风险阈值低于中风险」）
    incoming_thresholds = raw.get("thresholds")
    if isinstance(incoming_thresholds, dict):
        thresholds = settings["thresholds"]
        for key in ("high", "medium", "low"):
            if key in incoming_thresholds:
                thresholds[key] = _number(incoming_thresholds[key], thresholds[key], 0.1, 1.0)
        if not (thresholds["high"] > thresholds["medium"] > thresholds["low"]):
            settings["thresholds"] = dict(SIMILARITY_THRESHOLDS)

    # 校准
    incoming_calibration = raw.get("calibration")
    if isinstance(incoming_calibration, dict):
        for key, value in incoming_calibration.items():
            if key == "enabled":
                settings["calibration"]["enabled"] = bool(value)
            elif key in settings["calibration"]:
                settings["calibration"][key] = value

    # 片段
    incoming_fragments = raw.get("fragments")
    if isinstance(incoming_fragments, dict):
        for key, value in incoming_fragments.items():
            if key in settings["fragments"]:
                settings["fragments"][key] = value

    # 归一化
    incoming_normalize = raw.get("normalize")
    if isinstance(incoming_normalize, dict):
        if "traditional" in incoming_normalize:
            settings["normalize"]["traditional"] = bool(incoming_normalize["traditional"])

    if "cluster_margin" in raw:
        settings["cluster_margin"] = _number(raw["cluster_margin"],
                                            settings["cluster_margin"])

    # 大模型接口：只收白名单字段。api_key 一律丢弃——密钥走 llm_client
    # 单独存进 secrets.json，绝不能落进 settings.json（它会被接口回显）
    incoming_llm = raw.get("llm")
    if isinstance(incoming_llm, dict):
        section = settings["llm"]
        if "enabled" in incoming_llm:
            section["enabled"] = bool(incoming_llm["enabled"])
        for key in ("base_url", "model"):
            if key in incoming_llm:
                section[key] = str(incoming_llm[key] or "").strip()[:200]
        for key in ("daily_budget", "timeout", "max_tokens"):
            if key in incoming_llm:
                low, high = _LIMITS[f"llm.{key}"]
                section[key] = _number(incoming_llm[key], section[key], low, high)
        prices = incoming_llm.get("prices")
        if isinstance(prices, dict):
            for key in ("input", "output"):
                if key in prices:
                    section["prices"][key] = _number(prices[key], section["prices"][key],
                                                     0.0, 1000.0)

    # AIGC 线索：权重与档位
    incoming_aigc = raw.get("aigc")
    if isinstance(incoming_aigc, dict):
        section = settings["aigc"]
        weights = incoming_aigc.get("weights")
        if isinstance(weights, dict):
            merged = dict(section["weights"])
            for key in AIGC_CONFIG["weights"]:
                if key in weights:
                    merged[key] = _number(weights[key], merged[key], 0.0, 1.0)
            section["weights"] = merged
        levels = incoming_aigc.get("levels")
        if isinstance(levels, dict):
            for key in ("strong", "medium"):
                if key in levels:
                    section["levels"][key] = _number(levels[key], section["levels"][key],
                                                     0.05, 1.0)
        for key in ("min_chars", "reliable_chars", "short_text_cap", "evidence_max",
                    "external_weight", "review_sentences_max"):
            if key in incoming_aigc:
                low, high = _LIMITS[f"aigc.{key}"]
                section[key] = _number(incoming_aigc[key], section[key], low, high)

    settings = _apply_limits(settings)
    settings["version"] = SETTINGS_VERSION

    # 这几个是「字/个数」，整数更直观（夹取过程会带出浮点）
    settings["calibration"]["low_confidence_len"] = int(settings["calibration"]["low_confidence_len"])
    settings["fragments"]["min_len"] = int(settings["fragments"]["min_len"])
    settings["fragments"]["max_per_result"] = int(settings["fragments"]["max_per_result"])
    settings["llm"]["max_tokens"] = int(settings["llm"]["max_tokens"])
    for key in ("min_chars", "reliable_chars", "evidence_max", "review_sentences_max"):
        settings["aigc"][key] = int(settings["aigc"][key])

    # 权重全为 0 会让所有分数变成 0，退回当前预设
    if sum(settings["weights"].values()) <= 0.0:
        preset = WEIGHT_PROFILES.get(DEFAULT_WEIGHT_PROFILE, {}).get("weights", WEIGHTS)
        settings["weights"] = dict(preset)
        settings["profile"] = DEFAULT_WEIGHT_PROFILE

    # 档位倒挂（强线索低于中线索）没有意义，整体退回出厂
    if settings["aigc"]["levels"]["strong"] <= settings["aigc"]["levels"]["medium"]:
        settings["aigc"]["levels"] = dict(AIGC_CONFIG["levels"])

    # AIGC 权重全为 0 同样会让线索强度恒为 0
    if sum(settings["aigc"]["weights"].values()) <= 0.0:
        settings["aigc"]["weights"] = dict(AIGC_CONFIG["weights"])

    return settings


def load_settings() -> dict:
    """读取设置；文件不存在或损坏时返回出厂设置（不写盘）。"""
    if not os.path.exists(SETTINGS_FILE):
        return defaults()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return defaults()
    return sanitize(raw)


def save_settings(settings: dict) -> dict:
    """校验后原子落盘，返回实际生效的设置。"""
    clean = sanitize(settings)
    tmp_path = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, SETTINGS_FILE)
    except OSError:
        # 磁盘只读等情况：不阻断检测功能，仅本次不持久化
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return clean


def apply_to_detector(detector, settings: dict) -> None:
    """把 AIGC 参数应用到风格线索检测器。"""
    detector.update_config(settings.get("aigc"))


def apply_to_calculator(calculator, settings: dict) -> None:
    """把设置应用到相似度计算器（引擎在启动与保存设置时调用）。"""
    calculator.weights = dict(settings["weights"])
    calculator.thresholds = dict(settings["thresholds"])
    calculator.calibration = dict(settings["calibration"])
    calculator.cluster_margin = settings["cluster_margin"]
    calculator.profile = settings["profile"]
    calculator.fragment_min_len = int(settings["fragments"]["min_len"])
    calculator.fragment_max = int(settings["fragments"]["max_per_result"])
    calculator.traditional = bool(settings["normalize"]["traditional"])
    calculator.clear_cache()        # 归一化开关变了，缓存的特征要作废
