"""
作业抄袭检测系统 - 大模型客户端（OpenAI 兼容接口）

这一层的默认立场是「省钱」，因为教师不希望为 API 花太多钱：

1. 只有显式调用才发请求。任何检测流程都不会自动联网，界面上是按钮而不是自动开关。
2. 结果按「模型 + 提示词」缓存。同一道题、同一篇作文重复跑不重复花钱。
3. 有每日预算闸门。估算花费超过上限后直接拒绝发请求，并提示教师手动放宽。
4. 用量与估算花费在界面上可见，教师随时知道自己花了多少。

密钥单独存 secrets.json（已在 .gitignore 中），设置接口只回「是否已配置」，
绝不把密钥原文返回给前端。

DeepSeek / 通义千问 / 智谱 / Kimi / 火山方舟等国内服务都提供 OpenAI 兼容的
chat/completions 端点，因此只需填 base_url + model + key 即可切换厂商。
"""

import json
import os
import re
import threading
from datetime import date
from hashlib import sha256

import requests

from config import BASE_DIR

SECRETS_FILE = os.path.join(BASE_DIR, "secrets.json")
USAGE_FILE = os.path.join(BASE_DIR, "llm_usage.json")
CACHE_FILE = os.path.join(BASE_DIR, "llm_cache.json")

# 单进程内的读改写用锁保护：Flask 多线程处理请求时避免写坏 JSON 文件
_LOCK = threading.RLock()
_CACHE_MAX_ENTRIES = 500

# 单价（元 / 百万 token）。国内主流服务普遍在这个量级，取一个偏保守的默认值，
# 宁可高估花费也不要让教师以为免费。实际价格请在设置里按所用厂商改。
DEFAULT_PRICE_PER_MILLION = {"input": 2.0, "output": 8.0}


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def _write_json(path: str, data) -> bool:
    """原子写入；失败返回 False 而不是抛错（用量记录不该打断检测功能）。"""
    tmp_path = path + ".tmp"
    try:
        with _LOCK:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        return True
    except OSError:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


# ==================== 密钥 ====================

def load_api_key() -> str:
    """取密钥。优先环境变量（便于服务器部署），其次本地 secrets.json。"""
    env_key = os.environ.get("ANTICOPY_LLM_API_KEY", "").strip()
    if env_key:
        return env_key
    stored = _read_json(SECRETS_FILE, {})
    return str(stored.get("api_key") or "").strip()


def save_api_key(api_key: str) -> bool:
    """保存密钥到本地文件。空字符串表示清除。"""
    with _LOCK:
        stored = _read_json(SECRETS_FILE, {})
        if api_key:
            stored["api_key"] = api_key
        else:
            stored.pop("api_key", None)
    return _write_json(SECRETS_FILE, stored)


def mask_api_key(api_key: str = None) -> str:
    """给界面看的脱敏形式，例如 sk-a...z9。"""
    key = load_api_key() if api_key is None else api_key
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-2:]}"


# ==================== 用量与预算 ====================

def _today() -> str:
    return date.today().isoformat()


def load_usage() -> dict:
    return _read_json(USAGE_FILE, {"days": {}, "total_calls": 0})


def estimate_cost(usage: dict, prices: dict = None) -> float:
    """按百万 token 单价估算一次请求的花费（元）。"""
    price_table = prices or DEFAULT_PRICE_PER_MILLION
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cost_in = prompt_tokens / 1_000_000 * float(price_table.get("input", 0) or 0)
    cost_out = completion_tokens / 1_000_000 * float(price_table.get("output", 0) or 0)
    return round(cost_in + cost_out, 6)


def record_usage(prompt_tokens: int, completion_tokens: int, cost: float) -> dict:
    """记账并返回当天累计。"""
    data = load_usage()
    days = data.get("days")
    if not isinstance(days, dict):
        days = {}
        data["days"] = days
    day = days.get(_today()) or {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    day["calls"] = int(day.get("calls", 0)) + 1
    day["input_tokens"] = int(day.get("input_tokens", 0)) + int(prompt_tokens)
    day["output_tokens"] = int(day.get("output_tokens", 0)) + int(completion_tokens)
    day["cost"] = round(float(day.get("cost", 0.0)) + float(cost), 6)
    days[_today()] = day
    data["total_calls"] = int(data.get("total_calls", 0)) + 1
    _write_json(USAGE_FILE, data)
    return day


def today_usage() -> dict:
    data = load_usage()
    day = (data.get("days") or {}).get(_today()) or {
        "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    day["date"] = _today()
    day["total_calls"] = int(data.get("total_calls", 0))
    return day


def budget_remaining(daily_budget: float) -> float:
    """今日还能花多少（元）。预算为非正数表示不设限。"""
    if not daily_budget or float(daily_budget) <= 0:
        return float("inf")
    used = float(today_usage().get("cost", 0.0))
    return round(max(0.0, float(daily_budget) - used), 6)


# ==================== 缓存 ====================

def cache_key(model: str, system_prompt: str, user_prompt: str) -> str:
    payload = f"{model}\x1f{system_prompt}\x1f{user_prompt}"
    return sha256(payload.encode("utf-8")).hexdigest()


def cache_get(key: str):
    cache = _read_json(CACHE_FILE, {})
    entry = cache.get(key)
    if not isinstance(entry, dict):
        return None
    return entry.get("value")


def cache_put(key: str, value) -> None:
    with _LOCK:
        cache = _read_json(CACHE_FILE, {})
        cache[key] = {"value": value, "at": _today()}
        # 只按日期裁不掉，超额时删最旧的一条，避免缓存无限膨胀
        if len(cache) > _CACHE_MAX_ENTRIES:
            oldest = sorted(cache.items(), key=lambda item: item[1].get("at", ""))
            for drop_key, _ in oldest[:len(cache) - _CACHE_MAX_ENTRIES]:
                cache.pop(drop_key, None)
    _write_json(CACHE_FILE, cache)


def cache_stats() -> dict:
    cache = _read_json(CACHE_FILE, {})
    return {"entries": len(cache)}


def clear_cache() -> int:
    cache = _read_json(CACHE_FILE, {})
    removed = len(cache)
    _write_json(CACHE_FILE, {})
    return removed


# ==================== 客户端 ====================

class LLMClient:
    """OpenAI 兼容的 chat/completions 客户端（带预算与缓存）。"""

    def __init__(self, config: dict = None):
        self.config = dict(config or {})

    # ---------- 配置读取 ----------

    @property
    def base_url(self) -> str:
        return str(self.config.get("base_url") or "").rstrip("/")

    @property
    def model(self) -> str:
        return str(self.config.get("model") or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @property
    def daily_budget(self) -> float:
        return float(self.config.get("daily_budget") or 0)

    @property
    def prices(self) -> dict:
        prices = self.config.get("prices")
        return prices if isinstance(prices, dict) else DEFAULT_PRICE_PER_MILLION

    def endpoint(self) -> str:
        url = self.base_url
        if not url:
            return ""
        if url.endswith("/chat/completions"):
            return url
        return url + "/chat/completions"

    def readiness(self) -> tuple:
        """返回 (ok, error_code, message)。调用前统一用它把门。"""
        if not self.enabled:
            return False, "disabled", "大模型功能已在设置中关闭"
        if not self.base_url or not self.model:
            return False, "not_configured", "尚未配置接口地址与模型名称"
        if not load_api_key():
            return False, "no_key", "尚未填写 API 密钥"
        if budget_remaining(self.daily_budget) <= 0:
            return False, "budget_exceeded", (
                f"今日估算花费已达预算上限 {self.daily_budget} 元，"
                f"请明天再用或在设置中提高预算")
        return True, "", ""

    def status(self) -> dict:
        """给设置页用的状态，只含脱敏密钥。"""
        day = today_usage()
        return {
            "configured": bool(self.base_url and self.model and load_api_key()),
            "enabled": self.enabled,
            "base_url": self.base_url,
            "model": self.model,
            "has_key": bool(load_api_key()),
            "key_masked": mask_api_key(),
            "daily_budget": self.daily_budget,
            "budget_remaining": None if self.daily_budget <= 0 else budget_remaining(self.daily_budget),
            "prices": self.prices,
            "today": day,
            "cache": cache_stats(),
            "readiness": {
                "ok": self.readiness()[0],
                "error_code": self.readiness()[1],
                "message": self.readiness()[2],
            },
        }

    # ---------- 调用 ----------

    def chat(self, user_prompt: str, system_prompt: str = "",
             use_cache: bool = True, max_tokens: int = None) -> dict:
        """
        发一次对话请求。

        :return: {"success", "content", "from_cache", "usage", "cost", "error", "error_code"}
        """
        ok, error_code, message = self.readiness()
        if not ok:
            return {"success": False, "content": "", "error": message,
                    "error_code": error_code, "from_cache": False}

        key = cache_key(self.model, system_prompt, user_prompt)
        if use_cache:
            cached = cache_get(key)
            if cached:
                return {"success": True, "content": cached.get("content", ""),
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                        "cost": 0.0, "from_cache": True, "error": "", "error_code": ""}

        payload = {
            "model": self.model,
            "messages": ([{"role": "system", "content": system_prompt}]
                         if system_prompt else [])
                        + [{"role": "user", "content": user_prompt}],
            # 参考答案与文本判别都要求稳定输出，温度固定 0
            "temperature": float(self.config.get("temperature") or 0),
            "max_tokens": int(max_tokens or self.config.get("max_tokens") or 900),
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {load_api_key()}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(self.endpoint(), json=payload, headers=headers,
                                 timeout=float(self.config.get("timeout") or 30))
        except requests.exceptions.Timeout:
            return {"success": False, "content": "", "error": "接口请求超时，本次未产生费用",
                    "error_code": "timeout", "from_cache": False}
        except requests.exceptions.RequestException as e:
            return {"success": False, "content": "", "error": f"无法连接接口：{e}",
                    "error_code": "unreachable", "from_cache": False}

        if resp.status_code >= 400:
            return {"success": False, "content": "",
                    "error": _explain_http_error(resp),
                    "error_code": f"http_{resp.status_code}", "from_cache": False}

        try:
            data = resp.json()
        except ValueError:
            return {"success": False, "content": "", "error": "接口返回的不是 JSON",
                    "error_code": "bad_response", "from_cache": False}

        content = _extract_content(data)
        if not content:
            return {"success": False, "content": "",
                    "error": "接口没有返回正文内容", "error_code": "empty_content",
                    "from_cache": False}

        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        cost = estimate_cost(usage, self.prices)

        record_usage(prompt_tokens, completion_tokens, cost)
        if use_cache:
            cache_put(key, {"content": content})

        return {"success": True, "content": content,
                "usage": {"prompt_tokens": prompt_tokens,
                          "completion_tokens": completion_tokens},
                "cost": cost, "from_cache": False, "error": "", "error_code": ""}

    def chat_json(self, user_prompt: str, system_prompt: str = "",
                  use_cache: bool = True, max_tokens: int = None) -> dict:
        """要求接口输出 JSON，并容错解析（模型常会裹代码块或加解释）。"""
        result = self.chat(user_prompt, system_prompt, use_cache, max_tokens)
        if not result.get("success"):
            result["data"] = None
            return result

        parsed = extract_json(result["content"])
        if parsed is None:
            result["success"] = False
            result["error"] = "接口返回内容无法解析为结构化结果，请重试或换模型"
            result["error_code"] = "parse_failed"
            result["data"] = None
        else:
            result["data"] = parsed
        return result


def _explain_http_error(resp) -> str:
    code = resp.status_code
    if code == 401:
        return "密钥无效或已过期（接口返回 401）"
    if code == 403:
        return "该密钥无权访问此模型（接口返回 403）"
    if code == 404:
        return "接口地址或模型名不存在，请检查设置（接口返回 404）"
    if code == 429:
        return "触发接口限流或余额不足（接口返回 429），稍后再试"
    if code >= 500:
        return f"服务端异常（接口返回 {code}），稍后再试"
    return f"接口返回错误 {code}"


def _extract_content(data: dict) -> str:
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        # 部分服务把结构化结果放在 reasoning_content 或直接字符串
        return str(content or "").strip()
    return ""


def extract_json(text: str):
    """
    从模型回复里取出 JSON 对象。

    容忍三种常见形态：裸 JSON、```json 代码块、JSON 前后带解释文字。
    """
    if not text:
        return None

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    candidates.append(text)

    for candidate in candidates:
        candidate = candidate.strip()
        try:
            return json.loads(candidate)
        except ValueError:
            pass
        # 退一步：取第一个 { 到最后一个 } 的片段
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except ValueError:
                continue
    return None
