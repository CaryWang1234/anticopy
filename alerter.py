"""
作业抄袭检测系统 - 报警模块
支持控制台彩色输出和日志文件记录
"""

import sys
import logging
from datetime import datetime
from config import ALERT_CONFIG, SIMILARITY_THRESHOLDS


def safe_print(text: str):
    """控制台安全输出：Windows GBK 终端无法显示 emoji 时降级，不报错"""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


# 日志级别映射
LEVEL_MAP = {
    "high": "ERROR",
    "medium": "WARNING",
    "low": "INFO",
    "safe": "DEBUG"
}

# 控制台颜色码 (ANSI)
COLORS = {
    "high": "\033[91m",      # 红色
    "medium": "\033[93m",    # 黄色
    "low": "\033[94m",       # 蓝色
    "safe": "\033[92m",      # 绿色
    "reset": "\033[0m",
    "bold": "\033[1m"
}


class Alerter:
    """报警输出管理器"""

    def __init__(self, config=None):
        self.config = config or ALERT_CONFIG
        self._setup_logger()

    def _setup_logger(self):
        """配置日志记录器"""
        self.logger = logging.getLogger("anticopy.alerts")
        self.logger.setLevel(getattr(logging, self.config.get("log_level", "INFO")))

        # 避免重复添加 handler
        if not self.logger.handlers:
            # 文件 handler
            log_file = self.config.get("log_file", "plagiarism_alerts.log")
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.INFO)
            formatter = logging.Formatter(
                '%(asctime)s | %(levelname)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            fh.setFormatter(formatter)
            self.logger.addHandler(fh)

    def alert(self, student_name: str, score: float, level: str, details: dict = None):
        """
        发出抄袭警报
        :param student_name: 学生姓名
        :param score: 相似度分数
        :param level: 级别 (high/medium/low/safe)
        :param details: 附加详情
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        score_pct = f"{score * 100:.1f}%"

        # 控制台输出
        if self.config.get("console", True):
            color = COLORS.get(level, "")
            reset = COLORS["reset"]
            bold = COLORS["bold"]

            icon = {"high": "🚨", "medium": "⚠️", "low": "ℹ️", "safe": "✅"}.get(level, "")

            safe_print(f"\n{bold}{icon} [{timestamp}] {student_name}{reset}")
            safe_print(f"  相似度: {color}{bold}{score_pct}{reset}  等级: {color}{level.upper()}{reset}")

            if details:
                safe_print(f"  精确匹配: {details.get('exact_match', 0)*100:.1f}%  "
                      f"序列相似: {details.get('sequence_similarity', 0)*100:.1f}%  "
                      f"关键词重叠: {details.get('keyword_overlap', 0)*100:.1f}%")

        # 日志文件记录
        log_level = LEVEL_MAP.get(level, "INFO")
        msg = f"{student_name} | 相似度={score_pct} | 等级={level}"
        if details:
            msg += f" | 精确={details.get('exact_match', 0):.3f} 序列={details.get('sequence_similarity', 0):.3f} 关键词={details.get('keyword_overlap', 0):.3f}"

        self.logger.log(getattr(logging, log_level), msg)

    def batch_alert(self, results: list, statistics: dict):
        """批量输出检测结果"""
        safe_print(f"\n{'='*60}")
        safe_print(f"{COLORS['bold']}📊 检测统计报告{COLORS['reset']}")
        safe_print(f"{'='*60}")
        safe_print(f"  检测份数: {statistics.get('count', 0)}")
        safe_print(f"  平均相似度: {statistics.get('avg_score', 0)*100:.1f}%")
        safe_print(f"  最高相似度: {statistics.get('max_score', 0)*100:.1f}%")
        safe_print(f"  最低相似度: {statistics.get('min_score', 0)*100:.1f}%")
        safe_print(f"  抄袭率: {statistics.get('plagiarism_rate', 0)*100:.1f}%")
        safe_print(f"  ─────────────────────────")
        safe_print(f"  🚨 高相似度 (≥80%): {statistics.get('high_count', 0)} 份")
        safe_print(f"  ⚠️  中相似度 (50-80%): {statistics.get('medium_count', 0)} 份")
        safe_print(f"  ℹ️  低相似度 (30-50%): {statistics.get('low_count', 0)} 份")
        safe_print(f"  ✅ 安全 (<30%): {statistics.get('safe_count', 0)} 份")
        safe_print(f"{'='*60}\n")

        # 逐条记录
        for r in results:
            self.alert(
                student_name=r.get("student_name", "未知"),
                score=r["weighted_score"],
                level=r["level"],
                details=r
            )

        # 汇总写入日志
        self.logger.info(
            f"批量检测完成 | 共{statistics['count']}份 | "
            f"平均={statistics['avg_score']*100:.1f}% | "
            f"抄袭率={statistics['plagiarism_rate']*100:.1f}%"
        )

    def summary_report(self, results: list) -> str:
        """生成纯文本汇总报告（用于导出）"""
        lines = []
        lines.append("=" * 50)
        lines.append("作业抄袭检测报告")
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 50)

        for i, r in enumerate(results, 1):
            lines.append(f"\n[{i}] {r.get('student_name', '未知')}")
            lines.append(f"    综合相似度: {r['weighted_score']*100:.1f}%  等级: {r['level'].upper()}")
            lines.append(f"    精确匹配: {r['exact_match']*100:.1f}%  "
                        f"序列相似: {r['sequence_similarity']*100:.1f}%  "
                        f"关键词重叠: {r['keyword_overlap']*100:.1f}%")

        lines.append(f"\n{'=' * 50}")
        return "\n".join(lines)
