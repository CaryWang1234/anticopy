"""
作业抄袭检测系统 - 核心检测引擎
统一调度检测流程：API 获取答案 → 相似度计算 → 报警输出
"""

import os
import logging
from similarity import SimilarityCalculator
from api_client import APIClient
from alerter import Alerter
from config import DETECTION_CONFIG

logger = logging.getLogger(__name__)


class DetectionEngine:
    """抄袭检测核心引擎"""

    def __init__(self):
        self.calculator = SimilarityCalculator()
        self.api_client = APIClient()
        self.alerter = Alerter()
        self.config = DETECTION_CONFIG

    # ==================== 文本检测 ====================

    def detect_text(self, standard_answer: str, student_answer: str, student_name: str = "学生") -> dict:
        """
        单份文本检测
        :return: 检测结果 dict
        """
        result = self.calculator.calculate(standard_answer, student_answer)
        result["student_name"] = student_name
        result["student_answer"] = student_answer
        result["standard_answer"] = standard_answer

        # 触发警报
        self.alerter.alert(
            student_name=student_name,
            score=result["weighted_score"],
            level=result["level"],
            details=result
        )
        return result

    def detect_text_batch(self, standard_answer: str, students: list) -> dict:
        """
        批量文本检测
        :param standard_answer: 标准答案（公共）
        :param students: [{"name": "张三", "text": "答案..."}, ...]
        :return: {"results": [...], "statistics": {...}}
        """
        results = self.calculator.batch_calculate(standard_answer, students)
        statistics = self.calculator.get_statistics(results)

        # 批量警报
        self.alerter.batch_alert(results, statistics)

        return {"results": results, "statistics": statistics}

    def detect_text_with_api(self, question: str, student_answers: list) -> dict:
        """
        通过 API 自动获取标准答案并批量检测
        :param question: 题目文本
        :param student_answers: [{"name": "张三", "text": "答案..."}, ...]
        """
        # 调用 API 获取标准答案
        api_result = self.api_client.search_answer(question)

        if not api_result["success"]:
            return {
                "success": False,
                "error": api_result["error"],
                "results": [],
                "statistics": {}
            }

        standard = api_result["answer"]
        logger.info(f"API 获取标准答案成功 (来源: {api_result['source']})")

        # 批量检测
        batch_result = self.detect_text_batch(standard, student_answers)
        batch_result["success"] = True
        batch_result["standard_answer"] = standard
        batch_result["api_source"] = api_result["source"]
        return batch_result

    # ==================== 图片检测 ====================

    def detect_image(self, image_path: str, student_answer: str, student_name: str = "学生") -> dict:
        """
        单份图片检测：从题目图片获取标准答案后与学生答案比较
        """
        api_result = self.api_client.search_by_image(image_path)

        if not api_result["success"]:
            return {
                "success": False,
                "error": api_result["error"],
                "student_name": student_name
            }

        standard = api_result["answer"]
        result = self.calculator.calculate(standard, student_answer)
        result["student_name"] = student_name
        result["standard_answer"] = standard
        result["api_source"] = api_result["source"]

        self.alerter.alert(
            student_name=student_name,
            score=result["weighted_score"],
            level=result["level"],
            details=result
        )
        return result

    def detect_image_batch(self, image_path: str, students: list) -> dict:
        """
        批量图片检测：同一张题目图片，多份学生答案
        """
        api_result = self.api_client.search_by_image(image_path)

        if not api_result["success"]:
            return {
                "success": False,
                "error": api_result["error"],
                "results": [],
                "statistics": {}
            }

        standard = api_result["answer"]
        batch_result = self.detect_text_batch(standard, students)
        batch_result["success"] = True
        batch_result["standard_answer"] = standard
        batch_result["api_source"] = api_result["source"]
        return batch_result

    # ==================== 交互式检测 ====================

    def detect_interactive(self) -> None:
        """交互式命令行检测"""
        print("\n" + "=" * 50)
        print("📝 作业抄袭检测系统 - 交互模式")
        print("=" * 50)

        mode = input("\n选择检测模式 [1=文本 / 2=图片 / 3=API搜题]: ").strip()

        if mode == "1":
            self._interactive_text()
        elif mode == "2":
            self._interactive_image()
        elif mode == "3":
            self._interactive_api()
        else:
            print("❌ 无效选项")

    def _interactive_text(self):
        """交互式文本检测"""
        print("\n── 文本检测模式 ──")
        standard = input("请输入标准答案: ").strip()

        count = input("请输入检测份数: ").strip()
        try:
            count = int(count)
        except ValueError:
            count = 1

        students = []
        for i in range(count):
            name = input(f"  学生{i+1}姓名 (回车默认学生{i+1}): ").strip()
            name = name or f"学生{i+1}"
            text = input(f"  {name}的答案: ").strip()
            students.append({"name": name, "text": text})

        result = self.detect_text_batch(standard, students)
        self._print_result_summary(result)

    def _interactive_image(self):
        """交互式图片检测"""
        print("\n── 图片检测模式 ──")
        image_path = input("请输入题目图片路径: ").strip()

        count = input("请输入检测份数: ").strip()
        try:
            count = int(count)
        except ValueError:
            count = 1

        students = []
        for i in range(count):
            name = input(f"  学生{i+1}姓名 (回车默认学生{i+1}): ").strip()
            name = name or f"学生{i+1}"
            text = input(f"  {name}的答案: ").strip()
            students.append({"name": name, "text": text})

        result = self.detect_image_batch(image_path, students)
        if result.get("success"):
            self._print_result_summary(result)
        else:
            print(f"❌ 检测失败: {result.get('error')}")

    def _interactive_api(self):
        """交互式 API 搜题检测"""
        print("\n── API 搜题检测模式 ──")
        question = input("请输入题目: ").strip()

        count = input("请输入检测份数: ").strip()
        try:
            count = int(count)
        except ValueError:
            count = 1

        students = []
        for i in range(count):
            name = input(f"  学生{i+1}姓名 (回车默认学生{i+1}): ").strip()
            name = name or f"学生{i+1}"
            text = input(f"  {name}的答案: ").strip()
            students.append({"name": name, "text": text})

        result = self.detect_text_with_api(question, students)
        if result.get("success"):
            print(f"\n📖 标准答案 (来源: {result.get('api_source')}):")
            print(f"  {result['standard_answer'][:200]}...")
            self._print_result_summary(result)
        else:
            print(f"❌ 检测失败: {result.get('error')}")

    def _print_result_summary(self, result: dict):
        """打印结果摘要"""
        stats = result.get("statistics", {})
        if stats:
            print(f"\n📊 检测完成: 共{stats['count']}份")
            print(f"   平均相似度: {stats['avg_score']*100:.1f}%")
            print(f"   抄袭率: {stats['plagiarism_rate']*100:.1f}%")
