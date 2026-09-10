"""
作业抄袭检测系统 - API 客户端模块
集成作业帮、小猿搜题等搜题 API 自动获取标准答案
"""

import requests
import logging
import base64
import os
from config import API_CONFIG

logger = logging.getLogger(__name__)


class APIClient:
    """搜题 API 客户端"""

    def __init__(self):
        self.config = API_CONFIG
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json"
        })

    def search_answer(self, query: str, source: str = None) -> dict:
        """
        通过搜题 API 获取标准答案
        :param query: 题目文本
        :param source: 指定来源 (zuoyebang / xiaoyuansouti)，None 则全部尝试
        :return: {"success": bool, "answer": str, "source": str, "error": str}
        """
        sources = [source] if source else [
            s for s, cfg in self.config.items() if cfg.get("enabled")
        ]

        for src in sources:
            try:
                result = self._search_from_source(query, src)
                if result["success"] and result.get("answer"):
                    return result
            except Exception as e:
                logger.warning(f"[{src}] 搜题失败: {e}")
                continue

        return {"success": False, "answer": "", "source": "", "error": "所有 API 均未返回有效答案"}

    def search_by_image(self, image_path: str, source: str = None) -> dict:
        """
        通过图片搜题获取标准答案
        :param image_path: 图片路径
        :param source: 指定来源
        """
        if not os.path.exists(image_path):
            return {"success": False, "answer": "", "source": "", "error": f"图片不存在: {image_path}"}

        try:
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            return {"success": False, "answer": "", "source": "", "error": f"读取图片失败: {e}"}

        sources = [source] if source else [
            s for s, cfg in self.config.items() if cfg.get("enabled")
        ]

        for src in sources:
            try:
                result = self._search_image_from_source(image_data, src)
                if result["success"] and result.get("answer"):
                    return result
            except Exception as e:
                logger.warning(f"[{src}] 图片搜题失败: {e}")
                continue

        return {"success": False, "answer": "", "source": "", "error": "图片搜题 API 均未返回结果"}

    def _search_from_source(self, query: str, source: str) -> dict:
        """从指定来源搜索文本答案"""
        cfg = self.config.get(source, {})
        url = cfg.get("url", "")
        timeout = cfg.get("timeout", 10)

        try:
            resp = self.session.get(url, params={"q": query}, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()

            answer = self._extract_answer(data, source)
            return {
                "success": bool(answer),
                "answer": answer,
                "source": source,
                "error": "" if answer else "未找到答案"
            }
        except requests.exceptions.Timeout:
            return {"success": False, "answer": "", "source": source, "error": "请求超时"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "answer": "", "source": source, "error": str(e)}

    def _search_image_from_source(self, image_b64: str, source: str) -> dict:
        """从指定来源搜索图片答案"""
        cfg = self.config.get(source, {})
        url = cfg.get("url", "")
        timeout = cfg.get("timeout", 10)

        try:
            resp = self.session.post(
                url,
                json={"image": image_b64, "type": "image"},
                timeout=timeout
            )
            resp.raise_for_status()
            data = resp.json()

            answer = self._extract_answer(data, source)
            return {
                "success": bool(answer),
                "answer": answer,
                "source": source,
                "error": "" if answer else "未找到答案"
            }
        except requests.exceptions.Timeout:
            return {"success": False, "answer": "", "source": source, "error": "请求超时"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "answer": "", "source": source, "error": str(e)}

    def _extract_answer(self, data: dict, source: str) -> str:
        """从 API 响应中提取答案文本"""
        try:
            if source == "zuoyebang":
                # 作业帮 API 响应格式
                results = data.get("data", {}).get("results", [])
                if results:
                    return results[0].get("answer", "")
            elif source == "xiaoyuansouti":
                # 小猿搜题 API 响应格式
                answers = data.get("data", {}).get("answers", [])
                if answers:
                    return answers[0].get("text", "")

            # 通用提取
            if "answer" in data:
                return data["answer"]
            if "data" in data and isinstance(data["data"], str):
                return data["data"]

        except (KeyError, IndexError, TypeError):
            pass
        return ""

    def health_check(self) -> dict:
        """检查各 API 可用性"""
        status = {}
        for name, cfg in self.config.items():
            if not cfg.get("enabled"):
                status[name] = {"available": False, "reason": "已禁用"}
                continue
            try:
                resp = self.session.get(cfg["url"], timeout=5)
                status[name] = {
                    "available": resp.status_code < 500,
                    "status_code": resp.status_code
                }
            except Exception as e:
                status[name] = {"available": False, "reason": str(e)}
        return status
