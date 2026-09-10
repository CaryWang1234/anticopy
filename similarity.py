"""
作业抄袭检测系统 - 相似度计算模块
实现三维度相似度计算：精确匹配、序列相似度、关键词重叠
"""

import re
import jieba
from difflib import SequenceMatcher
from collections import Counter
from config import WEIGHTS, SIMILARITY_THRESHOLDS


class SimilarityCalculator:
    """相似度计算器"""

    def __init__(self, weights=None, thresholds=None):
        self.weights = weights or WEIGHTS
        self.thresholds = thresholds or SIMILARITY_THRESHOLDS

    def calculate(self, text1: str, text2: str) -> dict:
        """
        计算两段文本的综合相似度
        返回各维度分数及加权总分
        """
        text1_clean = self._clean_text(text1)
        text2_clean = self._clean_text(text2)

        if not text1_clean or not text2_clean:
            return {
                "exact_match": 0.0,
                "sequence_similarity": 0.0,
                "keyword_overlap": 0.0,
                "weighted_score": 0.0,
                "level": "safe"
            }

        exact = self._exact_match(text1_clean, text2_clean)
        sequence = self._sequence_similarity(text1_clean, text2_clean)
        keyword = self._keyword_overlap(text1_clean, text2_clean)

        weighted = (
            exact * self.weights["exact_match"]
            + sequence * self.weights["sequence_similarity"]
            + keyword * self.weights["keyword_overlap"]
        )

        level = self._classify_level(weighted)

        return {
            "exact_match": round(exact, 4),
            "sequence_similarity": round(sequence, 4),
            "keyword_overlap": round(keyword, 4),
            "weighted_score": round(weighted, 4),
            "level": level
        }

    def _clean_text(self, text: str) -> str:
        """清洗文本：去除多余空白、统一标点"""
        if not text:
            return ""
        text = text.strip()
        text = re.sub(r'\s+', ' ', text)
        # 统一中英文标点
        text = text.replace('，', ',').replace('。', '.').replace('；', ';')
        text = text.replace('：', ':').replace('！', '!').replace('？', '?')
        return text.lower()

    def _exact_match(self, text1: str, text2: str) -> float:
        """精确匹配度：字符级别的完全一致比例"""
        if not text1 or not text2:
            return 0.0
        if text1 == text2:
            return 1.0

        # 基于最长公共子串计算
        matcher = SequenceMatcher(None, text1, text2)
        matching_chars = sum(block.size for block in matcher.get_matching_blocks())
        max_len = max(len(text1), len(text2))
        return matching_chars / max_len if max_len > 0 else 0.0

    def _sequence_similarity(self, text1: str, text2: str) -> float:
        """序列相似度：基于 SequenceMatcher 的整体比率"""
        if not text1 or not text2:
            return 0.0
        return SequenceMatcher(None, text1, text2).ratio()

    def _keyword_overlap(self, text1: str, text2: str) -> float:
        """关键词重叠度：基于分词的 Jaccard 相似系数"""
        if not text1 or not text2:
            return 0.0

        words1 = set(jieba.cut(text1))
        words2 = set(jieba.cut(text2))

        # 去除停用词和单字
        stop_words = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人',
                      '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去',
                      '你', '会', '着', '没有', '看', '好', '自己', '这', '他', '她'}
        words1 = {w for w in words1 if len(w) > 1 and w not in stop_words}
        words2 = {w for w in words2 if len(w) > 1 and w not in stop_words}

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union) if union else 0.0

    def _classify_level(self, score: float) -> str:
        """根据分数划分相似度等级"""
        if score >= self.thresholds.get("high", 0.8):
            return "high"
        elif score >= self.thresholds.get("medium", 0.5):
            return "medium"
        elif score >= self.thresholds.get("low", 0.3):
            return "low"
        else:
            return "safe"

    def batch_calculate(self, standard: str, answers: list) -> list:
        """
        批量计算：将标准答案与多份学生答案逐一比较
        返回按相似度降序排列的结果列表
        """
        results = []
        for i, answer in enumerate(answers):
            score = self.calculate(standard, answer.get("text", ""))
            score["index"] = i
            score["student_name"] = answer.get("name", f"学生{i+1}")
            score["student_answer"] = answer.get("text", "")
            results.append(score)

        # 按相似度降序排列
        results.sort(key=lambda x: x["weighted_score"], reverse=True)
        return results

    def get_statistics(self, results: list) -> dict:
        """计算批量检测的统计信息"""
        if not results:
            return {"count": 0, "avg_score": 0, "high_count": 0, "medium_count": 0, "low_count": 0}

        scores = [r["weighted_score"] for r in results]
        hi = self.thresholds.get("high", 0.8)
        mid = self.thresholds.get("medium", 0.5)
        low = self.thresholds.get("low", 0.3)
        return {
            "count": len(results),
            "avg_score": round(sum(scores) / len(scores), 4),
            "max_score": round(max(scores), 4),
            "min_score": round(min(scores), 4),
            "high_count": sum(1 for s in scores if s >= hi),
            "medium_count": sum(1 for s in scores if mid <= s < hi),
            "low_count": sum(1 for s in scores if low <= s < mid),
            "safe_count": sum(1 for s in scores if s < low),
            "plagiarism_rate": round(sum(1 for s in scores if s >= mid) / len(scores), 4)
        }
