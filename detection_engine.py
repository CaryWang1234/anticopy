"""
作业抄袭检测系统 - 核心检测引擎
统一调度：参数装配 → 相似度计算 / AIGC 风格线索 → 报警输出
"""

from similarity import SimilarityCalculator, METRIC_KEYS
from aigc_detector import AIGCDetector
from alerter import Alerter
from answer_source import generate_reference
from llm_client import LLMClient
from config import DETECTION_CONFIG
from settings_store import load_settings, apply_to_calculator, apply_to_detector


class DetectionEngine:
    """抄袭检测核心引擎"""

    def __init__(self):
        self.calculator = SimilarityCalculator()
        self.aigc = AIGCDetector()
        self.llm = LLMClient()
        # 报警文案里的阈值来自同一个字典，设置面板改动后两者保持一致
        self.alerter = Alerter(thresholds=self.calculator.thresholds)
        self.config = DETECTION_CONFIG

        # 启动即加载磁盘上的设置，保证重启后参数不丢
        self.settings = load_settings()
        self.apply_settings(self.settings)

    def apply_settings(self, settings: dict) -> None:
        """把设置应用到计算器、报警器与 AIGC 检测器（设置面板保存后同样走这里）。"""
        self.settings = settings
        apply_to_calculator(self.calculator, settings)
        apply_to_detector(self.aigc, settings)
        self.llm.config = dict(settings.get("llm") or {})
        self.alerter.set_thresholds(self.calculator.thresholds)

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

    def detect_text_batch(self, standard_answer: str, students: list,
                          with_fragments: bool = True, max_frags: int = None,
                          profile: str = None) -> dict:
        """
        批量文本检测
        :param standard_answer: 标准答案（公共）
        :param students: [{"name": "张三", "text": "答案..."}, ...]
        :param with_fragments: 是否定位雷同片段（教师最需要的证据）
        :return: {"results": [...], "statistics": {...}}
        """
        results = self.calculator.batch_calculate(
            standard_answer, students,
            with_fragments=with_fragments, max_frags=max_frags, profile=profile,
        )
        statistics = self.calculator.get_statistics(results)

        # 批量警报
        self.alerter.batch_alert(results, statistics)

        return {"results": results, "statistics": statistics}

    # ==================== AI 生成参考答案 ====================

    def generate_answer(self, question: str, subject: str = "", grade: str = "",
                        answer_style: str = "standard") -> dict:
        """
        用大模型生成参考答案与评分要点（替代不可用的作业帮/小猿搜题搜题接口）。

        结果一律带「AI 生成，请先核对」的提示：参考答案错了，整批比对就全错。
        """
        return generate_reference(question, subject=subject, grade=grade,
                                  answer_style=answer_style, client=self.llm)

    # ==================== AIGC 风格线索 ====================

    def detect_aigc(self, text: str, use_external: bool = False,
                    student_name: str = "") -> dict:
        """
        一篇作文的 AI 风格线索。

        刻意不写进 plagiarism_alerts.log：那个文件是「抄袭报警」，
        而这里给的是需要老师自己判断的线索，混在一起等于替老师下了结论。
        """
        result = self.aigc.analyze(text, use_external=use_external,
                                   client=self.llm, student_name=student_name)
        result["mode"] = "aigc"
        return result

    def detect_aigc_batch(self, essays: list, use_external: bool = False) -> dict:
        """
        多篇作文的风格线索。教师需要的是「全班 40 篇里先看哪 3 篇」，
        所以给出按线索强度排序的列表与计数，而不是一堆独立卡片。

        :param essays: [{"name": "张三", "text": "作文..."}, ...]
        """
        if not essays:
            return {"success": False, "mode": "aigc", "error": "请至少粘贴一篇作文",
                    "results": [], "statistics": {}}
        if len(essays) > self.config["max_batch_size"]:
            return {
                "success": False, "mode": "aigc",
                "error": f"一次最多检测 {self.config['max_batch_size']} 篇",
                "results": [], "statistics": {},
            }

        results = []
        for i, essay in enumerate(essays):
            name = (essay.get("name") or f"作文{i + 1}").strip()
            item = self.aigc.analyze(essay.get("text") or "", use_external=use_external,
                                     client=self.llm, student_name=name)
            item["mode"] = "aigc"
            item["index"] = i
            item["name"] = name
            results.append(item)

        results.sort(key=lambda one: (one.get("ai_score") or 0.0), reverse=True)
        return {"success": True, "mode": "aigc", "results": results,
                "statistics": self._aigc_statistics(results)}

    def _aigc_statistics(self, results: list) -> dict:
        ok = [one for one in results if one.get("success")]
        externals = [one.get("external") or {} for one in ok]
        return {
            "count": len(results),
            "analyzed": len(ok),
            "rejected": len(results) - len(ok),
            "strong_count": sum(1 for one in ok if one["level"] == "strong"),
            "medium_count": sum(1 for one in ok if one["level"] == "medium"),
            "weak_count": sum(1 for one in ok if one["level"] == "weak"),
            "avg_score": round(sum(one["ai_score"] for one in ok) / len(ok), 4) if ok else 0.0,
            "external_calls": sum(1 for one in externals if one.get("success")),
            "external_cached": sum(1 for one in externals if one.get("from_cache")),
            "estimated_cost": round(sum(float(one.get("cost") or 0.0)
                                        for one in externals), 6),
        }

    # ==================== 学生互查 ====================

    def detect_pairwise(self, students: list) -> dict:
        """
        学生之间两两比对（不需要标准答案）。

        :param students: [{"name": "张三", "text": "答案..."}, ...]
        """
        data = self.calculator.pairwise_calculate(students)

        # 控制台按「对」报警：只挑最可疑的前若干对，避免刷屏
        suspicious = [p for p in data["pairs"]
                      if p["score"] >= self.calculator.thresholds.get("medium", 0.5)]
        pseudo_results = [{
            "student_name": f"{p['a_name']} ↔ {p['b_name']}",
            "weighted_score": p["score"],
            "level": p["level"],
            "metrics_version": p["metrics_version"],
            **p["metrics"],
        } for p in suspicious[:10]]

        self.alerter.batch_alert(pseudo_results, data["statistics"],
                                 unit="人", title="学生互查统计报告")
        return data

    # ==================== 整卷多题 ====================

    def detect_paper(self, assignment: dict, students: list) -> dict:
        """
        整卷多题检测：逐题与标准答案比对，再按题目分值汇总成整卷抄袭指数。

        :param assignment: {"name": "第3单元测验",
                            "questions": [{"id","stem","standard_answer","full_score"}, ...]}
        :param students: [{"name": "张三", "answers": {"Q1": "..."}, "photos": []}, ...]
        :return: {"success","mode","per_question","rollup","statistics"}
        """
        questions = assignment.get("questions") or []
        if not questions:
            return {"success": False, "error": "请至少添加一道题", "rollup": [],
                    "per_question": [], "statistics": {}}
        if not students:
            return {"success": False, "error": "请至少添加一份学生答案", "rollup": [],
                    "per_question": [], "statistics": {}}

        cells = len(questions) * len(students)
        max_cells = self.config["max_paper_cells"]
        if cells > max_cells:
            return {
                "success": False,
                "error": f"一次最多比对 {max_cells} 个「学生×题目」格子"
                         f"（当前 {len(students)} 人 × {len(questions)} 题 = {cells}）",
                "rollup": [], "per_question": [], "statistics": {},
            }

        normalized_questions = self._normalize_questions(questions)
        score_matrix = {}          # 学生原下标 -> {题号: 分数}
        weighted_sum = [0.0] * len(students)
        answered_full = [0.0] * len(students)
        low_confidence = [False] * len(students)
        metric_sums = [dict.fromkeys(METRIC_KEYS, 0.0) for _ in students]
        per_question = []

        for question in normalized_questions:
            qid = question["id"]
            full_score = question["full_score"]
            standard = question["standard_answer"]

            # 只把有作答的学生送进比对，缺答的从分子分母同时剔除
            answered = []
            for i, student in enumerate(students):
                text = (student.get("answers") or {}).get(qid) or ""
                if text.strip():
                    answered.append({"name": student.get("name") or f"学生{i + 1}",
                                     "text": text, "origin": i})

            results = self.calculator.batch_calculate(
                standard, answered, with_fragments=True,
                max_frags=DETECTION_CONFIG["fragment_max_per_result"] // 3 or 5,
            )

            for score in results:
                origin = answered[score["index"]]["origin"]
                score["student_index"] = origin        # 供界面按「人」汇总
                score_matrix.setdefault(origin, {})[qid] = score["weighted_score"]
                weighted_sum[origin] += score["weighted_score"] * full_score
                answered_full[origin] += full_score
                if score.get("low_confidence"):
                    low_confidence[origin] = True
                for key in METRIC_KEYS:
                    metric_sums[origin][key] += score.get(key, 0.0)

            per_question.append({
                "qid": qid,
                "stem": question["stem"],
                "standard_answer": standard,
                "full_score": full_score,
                "answered_count": len(answered),
                "results": results,
                "statistics": self.calculator.get_statistics(results),
            })

        total_full = sum(q["full_score"] for q in normalized_questions)
        high_threshold = self.calculator.thresholds.get("high", 0.8)
        rollup = []
        for i, student in enumerate(students):
            per_q = score_matrix.get(i, {})
            if answered_full[i] > 0:
                paper_score = weighted_sum[i] / answered_full[i]
            else:
                paper_score = 0.0

            if per_q:
                weakest_qid = max(per_q, key=lambda key: per_q[key])
                weakest_score = per_q[weakest_qid]
            else:
                weakest_qid, weakest_score = "", 0.0

            # 「有几道题高度雷同、占多少分」比单一平均分更能说明问题：
            # 抄了两道大题但第三题空着，平均分会被拉低到中风险，教师需要
            # 一眼看出「2 题雷同、占 30/40 分」
            full_score_map = {q["id"]: q["full_score"] for q in normalized_questions}
            high_risk_qids = [qid for qid, value in per_q.items()
                              if value >= high_threshold]
            high_risk_score = sum(full_score_map.get(qid, 0.0) for qid in high_risk_qids)

            rollup.append({
                "index": i,
                "name": student.get("name") or f"学生{i + 1}",
                "photos": student.get("photos") or [],
                "paper_score": round(paper_score, 4),
                "level": self.calculator.classify_level(paper_score),
                "per_question": per_q,
                "answered_count": len(per_q),
                "missing_count": len(normalized_questions) - len(per_q),
                "answered_full_score": round(answered_full[i], 2),
                "total_full_score": round(total_full, 2),
                "weakest_question": weakest_qid,
                "weakest_score": round(weakest_score, 4),
                "high_risk_questions": sorted(high_risk_qids),
                "high_risk_score": round(high_risk_score, 2),
                "high_risk_score_share": round(
                    high_risk_score / answered_full[i], 4) if answered_full[i] > 0 else 0.0,
                "avg_metrics": {
                    key: round(metric_sums[i][key] / len(per_q), 4)
                    for key in METRIC_KEYS
                } if per_q else dict.fromkeys(METRIC_KEYS, 0.0),
                "low_confidence": low_confidence[i],
            })

        rollup.sort(key=lambda item: (item["high_risk_score"],
                                      item["paper_score"]), reverse=True)

        return {
            "success": True,
            "mode": "paper",
            "assignment_name": assignment.get("name") or "",
            "questions": [{"id": q["id"], "stem": q["stem"],
                           "full_score": q["full_score"]} for q in normalized_questions],
            "per_question": per_question,
            "rollup": rollup,
            "statistics": self._paper_statistics(rollup, per_question, len(students)),
        }

    def _normalize_questions(self, questions: list) -> list:
        """补全题号与分值，并保证题号唯一（重复题号会让「按题汇总」串行）。"""
        normalized = []
        used = set()
        for i, question in enumerate(questions):
            qid = str(question.get("id") or f"Q{i + 1}").strip() or f"Q{i + 1}"
            while qid in used:
                qid = f"{qid}_"
            used.add(qid)
            try:
                full_score = float(question.get("full_score") or 10)
            except (TypeError, ValueError):
                full_score = 10.0
            normalized.append({
                "id": qid,
                "stem": (question.get("stem") or "").strip(),
                "standard_answer": (question.get("standard_answer") or "").strip(),
                "full_score": max(0.1, full_score),
            })
        return normalized

    def _paper_statistics(self, rollup: list, per_question: list,
                          student_count: int) -> dict:
        """整卷统计：学生维度 + 题目维度。"""
        scores = [r["paper_score"] for r in rollup]
        hi = self.calculator.thresholds.get("high", 0.8)
        mid = self.calculator.thresholds.get("medium", 0.5)
        low = self.calculator.thresholds.get("low", 0.3)

        # 全班平均相似度最高的那一题，往往就是这次最「有故事」的题
        suspicious = [q for q in per_question if q["results"]]
        weakest_question = ""
        if suspicious:
            weakest_question = max(
                suspicious, key=lambda q: q["statistics"].get("avg_score", 0))["qid"]

        statistics = {
            "count": student_count,
            "question_count": len(per_question),
            "weakest_question": weakest_question,
            "missing_total": sum(r["missing_count"] for r in rollup),
            "low_confidence_count": sum(1 for r in rollup if r["low_confidence"]),
            "min_len_avg": 0,
        }

        if not scores:
            statistics.update({"avg_score": 0, "max_score": 0, "min_score": 0,
                               "high_count": 0, "medium_count": 0, "low_count": 0,
                               "safe_count": 0, "plagiarism_rate": 0})
            return statistics

        statistics.update({
            "avg_score": round(sum(scores) / len(scores), 4),
            "max_score": round(max(scores), 4),
            "min_score": round(min(scores), 4),
            "high_count": sum(1 for s in scores if s >= hi),
            "medium_count": sum(1 for s in scores if mid <= s < hi),
            "low_count": sum(1 for s in scores if low <= s < mid),
            "safe_count": sum(1 for s in scores if s < low),
            "plagiarism_rate": round(sum(1 for s in scores if s >= mid) / len(scores), 4),
        })
        return statistics

    # ==================== 交互式检测 ====================

    def detect_interactive(self) -> None:
        """交互式命令行检测"""
        print("\n" + "=" * 50)
        print("📝 作业抄袭检测系统 - 交互模式")
        print("=" * 50)

        mode = input("\n选择检测模式 [1=文本 / 2=学生互查 / 3=AIGC风格线索]: ").strip()

        if mode == "1":
            self._interactive_text()
        elif mode == "2":
            self._interactive_pairwise()
        elif mode == "3":
            self._interactive_aigc()
        else:
            print("❌ 无效选项")

    def _collect_students(self) -> list:
        """命令行里逐个收集学生姓名与答案。"""
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
        return students

    def _interactive_text(self):
        """交互式文本检测"""
        print("\n── 文本检测模式 ──")
        standard = input("请输入标准答案: ").strip()
        students = self._collect_students()

        result = self.detect_text_batch(standard, students)
        self._print_result_summary(result)

    def _interactive_pairwise(self):
        """交互式学生互查（不需要标准答案）"""
        print("\n── 学生互查模式 ──")
        students = self._collect_students()
        if len([s for s in students if s["text"]]) < 2:
            print("❌ 互查至少需要两份有文字的答案")
            return

        data = self.detect_pairwise(students)
        stats = data["statistics"]
        print(f"\n📊 共 {data['pair_count']} 组两两比对")
        print(f"   平均相似度: {stats['avg_score']*100:.1f}%")
        if data["groups"]:
            for group in data["groups"]:
                names = "、".join(member["name"] for member in group["members"])
                print(f"   ⚠️ 疑似团伙（{group['size']} 人）: {names}")
        else:
            print("   未发现成团的雷同")

    def _interactive_aigc(self):
        """交互式 AIGC 风格线索"""
        print("\n── AIGC 风格线索模式 ──")
        print("粘贴作文全文，输入完成后单独一行回车结束（Ctrl+Z 回车也可）：")
        lines = []
        while True:
            try:
                line = input("")
            except EOFError:
                break
            if not line.strip():
                break
            lines.append(line)

        result = self.detect_aigc("\n".join(lines))
        if not result.get("success"):
            print(f"❌ {result.get('error')}")
            return

        print(f"\n🔍 线索强度: {result['level_label']} ({result['ai_score']*100:.1f}%)")
        for signal in result["signals"]:
            if not signal["applicable"]:
                continue
            print(f"   · {signal['label']:<8} {signal['metric']}  —  {signal['reading']}")
        print("\n最可疑的句子：")
        for item in result["evidence"][:3]:
            print(f"   [{item['score']:.2f}] {item['text'][:40]}")
        print(f"\n⚠️ {result['notice']}")

    def _print_result_summary(self, result: dict):
        """打印结果摘要"""
        stats = result.get("statistics", {})
        if stats:
            print(f"\n📊 检测完成: 共{stats['count']}份")
            print(f"   平均相似度: {stats['avg_score']*100:.1f}%")
            print(f"   抄袭率: {stats['plagiarism_rate']*100:.1f}%")
            if stats.get("low_confidence_count"):
                print(f"   注: {stats['low_confidence_count']} 份答案过短，"
                      f"分数已按小样本收缩")
