"""
作业抄袭检测系统 - 报警模块
支持控制台彩色输出和日志文件记录
"""

import sys
import logging
from datetime import datetime
from config import ALERT_CONFIG, SIMILARITY_THRESHOLDS
from similarity import METRIC_KEYS, METRIC_LABELS


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

    def __init__(self, config=None, thresholds=None):
        self.config = config or ALERT_CONFIG
        # 阈值可能被设置面板改动，这里保持引用而非拷贝，便于引擎统一切换
        self.thresholds = thresholds if thresholds is not None else SIMILARITY_THRESHOLDS
        self._setup_logger()

    def set_thresholds(self, thresholds: dict):
        """设置面板保存后由引擎调用，保证文案与实际阈值一致。"""
        self.thresholds = thresholds or SIMILARITY_THRESHOLDS

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
                # 六项指标分两行，避免一行太长在窄终端里折行难读
                parts = [f"{METRIC_LABELS[key]} {details.get(key, 0) * 100:.1f}%"
                         for key in METRIC_KEYS]
                safe_print("  " + "  ".join(parts[:3]))
                safe_print("  " + "  ".join(parts[3:]))
                if details.get("low_confidence"):
                    safe_print(f"  {COLORS['warning']}答案过短，分数已按小样本收缩"
                               f"（原始 {details.get('raw_score', 0) * 100:.1f}%）{reset}")
                elif details.get("metrics_version") == 0:
                    safe_print(f"  {COLORS['warning']}旧版算法计算的历史结果{reset}")

        # 日志文件记录
        log_level = LEVEL_MAP.get(level, "INFO")
        msg = f"{student_name} | 相似度={score_pct} | 等级={level}"
        if details:
            detail_parts = " ".join(f"{METRIC_LABELS[key]}={details.get(key, 0):.3f}"
                                    for key in METRIC_KEYS)
            msg += f" | {detail_parts}"
            if details.get("low_confidence"):
                msg += f" | 低置信(原始={details.get('raw_score', 0):.3f})"

        self.logger.log(getattr(logging, log_level), msg)

    def batch_alert(self, results: list, statistics: dict,
                    unit: str = "份", title: str = "检测统计报告"):
        """批量输出检测结果。unit 用于互查模式（统计口径是「人」和「对」）。"""
        hi = self.thresholds.get("high", 0.8)
        mid = self.thresholds.get("medium", 0.5)
        low = self.thresholds.get("low", 0.3)

        console = self.config.get("console", True)
        if console:
            safe_print(f"\n{'='*60}")
            safe_print(f"{COLORS['bold']}📊 {title}{COLORS['reset']}")
            safe_print(f"{'='*60}")
            safe_print(f"  检测{unit}数: {statistics.get('count', 0)}")
            if statistics.get("pair_count") is not None and unit != "份":
                safe_print(f"  比对对数: {statistics.get('pair_count', 0)}")
            safe_print(f"  平均相似度: {statistics.get('avg_score', 0)*100:.1f}%")
            safe_print(f"  最高相似度: {statistics.get('max_score', 0)*100:.1f}%")
            safe_print(f"  最低相似度: {statistics.get('min_score', 0)*100:.1f}%")
            safe_print(f"  抄袭率: {statistics.get('plagiarism_rate', 0)*100:.1f}%")
            safe_print(f"  ─────────────────────────")
            safe_print(f"  🚨 高相似度 (≥{hi*100:.0f}%): {statistics.get('high_count', 0)} {unit}")
            safe_print(f"  ⚠️  中相似度 ({mid*100:.0f}-{hi*100:.0f}%): {statistics.get('medium_count', 0)} {unit}")
            safe_print(f"  ℹ️  低相似度 ({low*100:.0f}-{mid*100:.0f}%): {statistics.get('low_count', 0)} {unit}")
            safe_print(f"  ✅ 安全 (<{low*100:.0f}%): {statistics.get('safe_count', 0)} {unit}")
            if statistics.get("group_count"):
                safe_print(f"  👥 疑似团伙(≥3人): {statistics['group_count']} 组，"
                           f"最大 {statistics.get('largest_group', 0)} 人")
            if statistics.get("low_confidence_count"):
                safe_print(f"  注: {statistics['low_confidence_count']} {unit}答案过短，"
                           f"分数已按小样本收缩")
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
            metric_parts = "  ".join(f"{METRIC_LABELS[key]} {r.get(key, 0)*100:.1f}%"
                                     for key in METRIC_KEYS)
            lines.append(f"    {metric_parts}")

        lines.append(f"\n{'=' * 50}")
        return "\n".join(lines)
