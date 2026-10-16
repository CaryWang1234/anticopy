"""
作业抄袭检测系统 - 相似度计算模块

六个维度 + 自归一化加权 + 小样本校准 + 雷同片段定位

维度说明（键名与旧版保持一致，但公式已升级，见 METRICS_VERSION）：
- exact_match           连续雷同块（≥4 字）的 Dice 系数，修掉旧版按最长文本做分母
                        导致「长答案天然虚高」的问题
- sequence_similarity   整体序列比率
- keyword_overlap       分词词频余弦（旧版是去重集合 Jaccard，丢失词频）
- char_gram_jaccard     字符 2/3-gram 集合 Jaccard，抓近似逐字照抄
- char_gram_cosine      同一 n-gram 词频向量的余弦，容忍语序调整
- coverage              3-gram 短边包含度，抓「只抄了一半」

加权分自归一化：Σ(w·m) / Σw，因此权重之和不必等于 1。
"""

import logging
from collections import OrderedDict, namedtuple
from difflib import SequenceMatcher
from hashlib import md5

from text_utils import (
    normalize, char_ngram_counter, term_counter, cosine, jaccard, containment,
    find_fragments,
)
from config import (
    WEIGHTS, WEIGHT_PROFILES, DEFAULT_WEIGHT_PROFILE,
    SIMILARITY_THRESHOLDS, CALIBRATION_CONFIG, DETECTION_CONFIG,
)

logger = logging.getLogger(__name__)

# 指标口径版本：历史记录里没有这个字段的一律视为旧版算法（值为 0）
METRICS_VERSION = 2

# 只有长度达到这个值的公共块才计入精确匹配，避免「同用两三个字」被当成照抄
MIN_BLOCK = 4

# 指标的规范顺序，权重表、界面展示都按这个顺序走
METRIC_KEYS = (
    "exact_match",
    "sequence_similarity",
    "keyword_overlap",
    "char_gram_jaccard",
    "char_gram_cosine",
    "coverage",
)

METRIC_LABELS = {
    "exact_match": "精确匹配",
    "sequence_similarity": "序列相似",
    "keyword_overlap": "关键词",
    "char_gram_jaccard": "字形重合",
    "char_gram_cosine": "字形分布",
    "coverage": "内容覆盖",
}

# 特征缓存容量：一批 50 份答案会构建 50 个特征，留够余量
_PROFILE_CACHE_SIZE = 256


class AnswerProfile(namedtuple("AnswerProfile", (
        "norm", "index_map", "grams", "gram_set", "tri_set", "terms"))):
    """一份答案的可比对特征。构建一次、多次比较复用。"""
    __slots__ = ()

    @property
    def length(self):
        return len(self.norm)


def _empty_metrics():
    """文本为空时的零分结果。"""
    result = {key: 0.0 for key in METRIC_KEYS}
    result.update({
        "weighted_score": 0.0,
        "raw_score": 0.0,
        "confidence": 1.0,
        "low_confidence": False,
        "min_len": 0,
        "metrics_version": METRICS_VERSION,
        "level": "safe",
    })
    return result


class SimilarityCalculator:
    """相似度计算器"""

    def __init__(self, weights=None, thresholds=None, calibration=None,
                 cluster_margin=None, profile=None,
                 fragment_min_len=None, fragment_max=None):
        self.weights = dict(weights or WEIGHTS)
        self.thresholds = dict(thresholds or SIMILARITY_THRESHOLDS)
        self.calibration = dict(calibration or CALIBRATION_CONFIG)
        self.cluster_margin = (
            DETECTION_CONFIG["cluster_margin"] if cluster_margin is None
            else cluster_margin
        )
        self.profile = profile or DEFAULT_WEIGHT_PROFILE
        self.fragment_min_len = (
            DETECTION_CONFIG["fragment_min_len"] if fragment_min_len is None
            else fragment_min_len
        )
        self.fragment_max = (
            DETECTION_CONFIG["fragment_max_per_result"] if fragment_max is None
            else fragment_max
        )
        self.traditional = False
        self._cache = OrderedDict()

    # ==================== 特征构建与缓存 ====================

    def _profile(self, text: str) -> AnswerProfile:
        """构建（或从缓存取）答案特征。缓存键含繁简开关，且不落盘。"""
        if not text:
            return AnswerProfile("", [], char_ngram_counter(""), set(), set(), term_counter(""))

        key = md5(f"{int(self.traditional)}\x00{text}".encode("utf-8")).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        norm, index_map = normalize(text, traditional=self.traditional)
        grams = char_ngram_counter(norm)
        profile = AnswerProfile(
            norm=norm,
            index_map=index_map,
            grams=grams,
            gram_set=set(grams),
            tri_set={gram for (size, gram) in grams if size == 3},
            terms=term_counter(norm),
        )

        self._cache[key] = profile
        if len(self._cache) > _PROFILE_CACHE_SIZE:
            self._cache.popitem(last=False)
        return profile

    def clear_cache(self):
        self._cache.clear()

    # ==================== 单维度计算 ====================

    def _exact_match(self, norm1: str, norm2: str) -> float:
        """Dice 系数：连续雷同块（≥MIN_BLOCK）覆盖了两侧总长的多少。"""
        if not norm1 or not norm2:
            return 0.0
        matcher = SequenceMatcher(None, norm1, norm2, autojunk=False)
        matched = sum(block.size for block in matcher.get_matching_blocks()
                      if block.size >= MIN_BLOCK)
        if matched == 0:
            return 0.0
        return 2.0 * matched / (len(norm1) + len(norm2))

    def _sequence_similarity(self, norm1: str, norm2: str) -> float:
        """整体序列比率。autojunk=False 避免长文本里的重复片段被忽略。"""
        if not norm1 or not norm2:
            return 0.0
        return SequenceMatcher(None, norm1, norm2, autojunk=False).ratio()

    # ==================== 校准 ====================

    def _calibrate(self, raw_score: float, min_len: int):
        """
        小样本校准：答案越短，字面相同越可能只是巧合（「不会」两个字完全相同
        不能算抄袭），因此只在答案过短时对分数做收缩。

        阈值就是 low_confidence_len：达到该长度即不再收缩。这样「逐字照抄一份
        27 字的答案」仍然是满分，不会出现「明明一模一样却只有 95%」的怪象。

        :return: (score, confidence, low_confidence)
        """
        config = self.calibration
        low_len = max(1, int(config.get("low_confidence_len", 12)))
        low_confidence = min_len < low_len

        if not config.get("enabled", True):
            return raw_score, 1.0, low_confidence

        confidence = min(1.0, min_len / low_len)
        floor = float(config.get("floor", 0.55))
        score = raw_score * (floor + (1.0 - floor) * confidence)

        if low_confidence:
            score = min(score, float(config.get("low_confidence_cap", 0.75)))

        return score, confidence, low_confidence

    # ==================== 加权 ====================

    def weights_for(self, profile: str = None) -> dict:
        """
        取生效的权重表。返回六项齐全的字典——旧设置里只有三项时，
        缺失项从对应预设补齐，保证行为可预期。
        """
        base = WEIGHT_PROFILES.get(profile or DEFAULT_WEIGHT_PROFILE, {}).get("weights", {})
        merged = {}
        for key in METRIC_KEYS:
            value = self.weights.get(key)
            if value is None:
                value = base.get(key, 0.0)
            merged[key] = max(0.0, float(value))
        if sum(merged.values()) <= 0.0:
            merged = {key: base.get(key, 0.0) for key in METRIC_KEYS}
        return merged

    def _weighted(self, metrics: dict, weights: dict) -> float:
        total = sum(weights.values())
        if total <= 0.0:
            return 0.0
        return sum(weights[key] * metrics[key] for key in METRIC_KEYS) / total

    # ==================== 主入口 ====================

    def calculate(self, text1: str, text2: str,
                  with_fragments: bool = True,
                  max_frags: int = None,
                  profile: str = None) -> dict:
        """
        计算两段文本的相似度。

        :param with_fragments: 是否定位雷同片段（证据），关闭可省一点开销
        :param profile: 权重预设名（short_answer / long_text / pairwise）
        :return: 含六个维度、加权分、校准信息与片段的字典
        """
        return self._compare(self._profile(text1), self._profile(text2),
                             with_fragments=with_fragments,
                             max_frags=max_frags, profile=profile)

    def _compare(self, p1: AnswerProfile, p2: AnswerProfile,
                 with_fragments: bool = True, max_frags: int = None,
                 profile: str = None) -> dict:
        if not p1.norm or not p2.norm:
            result = _empty_metrics()
            result["fragments"] = []
            return result

        metrics = {
            "exact_match": self._exact_match(p1.norm, p2.norm),
            "sequence_similarity": self._sequence_similarity(p1.norm, p2.norm),
            "keyword_overlap": cosine(p1.terms, p2.terms, sublinear=True),
            "char_gram_jaccard": jaccard(p1.gram_set, p2.gram_set),
            "char_gram_cosine": cosine(p1.grams, p2.grams),
            "coverage": containment(p1.tri_set, p2.tri_set),
        }

        weights = self.weights_for(profile)
        raw_score = self._weighted(metrics, weights)
        min_len = min(p1.length, p2.length)
        score, confidence, low_confidence = self._calibrate(raw_score, min_len)

        result = {key: round(metrics[key], 4) for key in METRIC_KEYS}
        result.update({
            "weighted_score": round(score, 4),      # 校准后，用于分级与展示
            "raw_score": round(raw_score, 4),        # 校准前，供教师对照
            "confidence": round(confidence, 4),
            "low_confidence": low_confidence,
            "min_len": min_len,
            "metrics_version": METRICS_VERSION,
            "weight_profile": profile or self.profile,
            "level": self._classify_level(score),
        })

        if with_fragments:
            limit = max_frags or self.fragment_max
            result["fragments"] = find_fragments(
                p1.norm, p1.index_map, p2.norm, p2.index_map,
                min_len=self.fragment_min_len,
                max_frags=limit,
            )
        else:
            result["fragments"] = []

        return result

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

    def classify_level(self, score: float) -> str:
        """对外暴露的分级入口（整卷汇总等场景需要按配置阈值分级）。"""
        return self._classify_level(score)

    # ==================== 批量 ====================

    def batch_calculate(self, standard: str, answers: list,
                        with_fragments: bool = True,
                        max_frags: int = None,
                        profile: str = None) -> list:
        """
        批量计算：将标准答案与多份学生答案逐一比较
        :param answers: [{"name": "张三", "text": "答案..."}, ...]
        :return: 按相似度降序排列的结果列表
        """
        standard_profile = self._profile(standard)
        results = []

        for i, answer in enumerate(answers):
            text = answer.get("text", "")
            score = self._compare(
                standard_profile, self._profile(text),
                with_fragments=with_fragments, max_frags=max_frags, profile=profile,
            )
            score["index"] = i
            score["student_name"] = answer.get("name", f"学生{i + 1}")
            score["student_answer"] = text
            results.append(score)

        results.sort(key=lambda x: x["weighted_score"], reverse=True)
        return results

    # ==================== 学生互查（两两比对） ====================

    def pairwise_calculate(self, students: list,
                           with_fragments: bool = True,
                           max_frag_pairs: int = 30) -> dict:
        """
        学生之间两两比对：不需要标准答案，用于判断「谁抄谁」。

        :param students: [{"name": "张三", "text": "答案..."}, ...]
        :return: {"students","matrix","pairs","pair_count","groups",
                  "per_student","statistics"}
        """
        names = [s.get("name") or f"学生{i + 1}" for i, s in enumerate(students)]
        profiles = [self._profile(s.get("text", "")) for s in students]
        size = len(students)

        matrix = [[0.0] * size for _ in range(size)]
        pairs = []

        for i in range(size):
            matrix[i][i] = 1.0
            for j in range(i + 1, size):
                result = self._compare(profiles[i], profiles[j], with_fragments=False)
                score = round(result["weighted_score"], 4)
                matrix[i][j] = score
                matrix[j][i] = score
                pairs.append({
                    "a_index": i, "a_name": names[i],
                    "b_index": j, "b_name": names[j],
                    "score": score,
                    "level": result["level"],
                    "low_confidence": result["low_confidence"],
                    "metrics_version": result["metrics_version"],
                    "metrics": {key: result[key] for key in METRIC_KEYS},
                    "fragments": [],
                })

        pairs.sort(key=lambda item: item["score"], reverse=True)

        # 片段只在前若干对里定位：50 人是 1225 次序列比对，而教师真正
        # 会翻看的只有排在最前面的那些，全量算纯属浪费
        if with_fragments and pairs:
            for pair in pairs[:max_frag_pairs]:
                extra = self._compare(profiles[pair["a_index"]], profiles[pair["b_index"]])
                pair["fragments"] = extra["fragments"]

        groups = self._cluster_groups(pairs, names)
        per_student = self._per_student_scores(matrix, names)
        statistics = self._pairwise_statistics(pairs, per_student, groups, size)

        return {
            "students": [{"index": i, "name": names[i],
                          "text": students[i].get("text", "")}
                         for i in range(size)],
            "matrix": matrix,
            "pairs": pairs[:50],
            "pair_count": len(pairs),
            "pairs_truncated": len(pairs) > 50,
            "groups": groups,
            "per_student": per_student,
            "statistics": statistics,
        }

    def _cluster_groups(self, pairs: list, names: list) -> list:
        """
        用并查集把「互相高度相似」的学生连成团伙。

        连线阈值取 max(中风险阈值, 全体对数中位数 + cluster_margin)。
        只看绝对阈值的话，遇到「全班普遍雷同」会把整个班并成一组，
        加上中位数这一项是为了让聚类跟着这次作业的整体水平走。
        """
        if not pairs:
            return []

        mid = self.thresholds.get("medium", 0.5)
        high = self.thresholds.get("high", 0.8)
        # 下界是「至少要有中风险那么像」，上界是高风险阈值：
        # - 只有一对可比时中位数就是那一对的分数，不设上界会让阈值永远够不着
        # - 全班普遍雷同（中位数本身就高）时，上界保证只把最像的那几对连起来，
        #   而不是把整个班并成一组
        edge = min(max(mid, _median([p["score"] for p in pairs]) + self.cluster_margin),
                   high)

        union_find = UnionFind(len(names))
        for pair in pairs:
            if pair["score"] >= edge:
                union_find.union(pair["a_index"], pair["b_index"])

        min_size = DETECTION_CONFIG["cluster_min_size"]
        groups = []
        for members in union_find.buckets().values():
            if len(members) < 2:
                continue
            member_set = set(members)
            internal = [p for p in pairs
                        if p["a_index"] in member_set and p["b_index"] in member_set]
            if not internal:
                continue

            scores = [p["score"] for p in internal]
            strongest = max(internal, key=lambda p: p["score"])
            strongest_fragment = strongest["fragments"][0]["text"] if strongest["fragments"] else None

            groups.append({
                "members": sorted(members),
                "member_names": [names[i] for i in sorted(members)],
                "size": len(members),
                "kind": "gang" if len(members) >= min_size else "pair",
                "min_internal": round(min(scores), 4),
                "avg_internal": round(sum(scores) / len(scores), 4),
                "max_internal": round(max(scores), 4),
                "evidence": {
                    "a_name": strongest["a_name"],
                    "b_name": strongest["b_name"],
                    "score": strongest["score"],
                    "fragment": strongest_fragment,
                },
            })

        # 团伙（≥3 人）排前面；内部最低分越高越可信
        groups.sort(key=lambda g: (g["kind"] == "gang", g["min_internal"]), reverse=True)
        return groups

    def _per_student_scores(self, matrix: list, names: list) -> list:
        """每位学生「和谁最像」，按最像的那一份降序，教师一眼看到最可疑的人。"""
        size = len(names)
        mid = self.thresholds.get("medium", 0.5)
        per_student = []

        for i in range(size):
            others = [matrix[i][j] for j in range(size) if j != i]
            if not others:
                per_student.append({"index": i, "name": names[i], "max_score": 0.0,
                                    "avg_score": 0.0, "partner": "", "involvement": 0})
                continue
            best_j = max((j for j in range(size) if j != i), key=lambda j: matrix[i][j])
            per_student.append({
                "index": i,
                "name": names[i],
                "max_score": round(matrix[i][best_j], 4),
                "avg_score": round(sum(others) / len(others), 4),
                "partner": names[best_j],
                "involvement": sum(1 for value in others if value >= mid),
            })

        per_student.sort(key=lambda item: item["max_score"], reverse=True)
        return per_student

    def _pairwise_statistics(self, pairs: list, per_student: list,
                             groups: list, size: int) -> dict:
        """统计以「对」为单位，另外附人数与团伙数（界面主统计仍按人算）。"""
        hi = self.thresholds.get("high", 0.8)
        mid = self.thresholds.get("medium", 0.5)
        low = self.thresholds.get("low", 0.3)
        scores = [p["score"] for p in pairs]

        statistics = {
            "count": size,                 # 参与人数：与文本模式的「检测份数」对齐
            "student_count": size,
            "pair_count": len(pairs),
            "matrix_size": size,
            "group_count": sum(1 for g in groups if g["kind"] == "gang"),
            "suspicious_pair_count": sum(1 for g in groups if g["kind"] == "pair"),
            "largest_group": max((g["size"] for g in groups), default=0),
            "low_confidence_count": sum(1 for p in pairs if p["low_confidence"]),
            "min_len_avg": 0,
        }

        if not scores:
            statistics.update({"avg_score": 0, "max_score": 0, "min_score": 0,
                               "median_score": 0, "high_count": 0, "medium_count": 0,
                               "low_count": 0, "safe_count": 0, "plagiarism_rate": 0})
            return statistics

        statistics.update({
            "avg_score": round(sum(scores) / len(scores), 4),
            "max_score": round(max(scores), 4),
            "min_score": round(min(scores), 4),
            "median_score": round(_median(scores), 4),
            "high_count": sum(1 for s in scores if s >= hi),
            "medium_count": sum(1 for s in scores if mid <= s < hi),
            "low_count": sum(1 for s in scores if low <= s < mid),
            "safe_count": sum(1 for s in scores if s < low),
            "plagiarism_rate": round(sum(1 for s in scores if s >= mid) / len(scores), 4),
        })
        return statistics

    def get_statistics(self, results: list) -> dict:
        """计算批量检测的统计信息（键名保持与旧版兼容）"""
        if not results:
            return {
                "count": 0, "avg_score": 0, "max_score": 0, "min_score": 0,
                "high_count": 0, "medium_count": 0, "low_count": 0,
                "safe_count": 0, "plagiarism_rate": 0,
                "min_len_avg": 0, "low_confidence_count": 0,
            }

        scores = [r["weighted_score"] for r in results]
        hi = self.thresholds.get("high", 0.8)
        mid = self.thresholds.get("medium", 0.5)
        low = self.thresholds.get("low", 0.3)
        lengths = [r.get("min_len", 0) for r in results]

        return {
            "count": len(results),
            "avg_score": round(sum(scores) / len(scores), 4),
            "max_score": round(max(scores), 4),
            "min_score": round(min(scores), 4),
            "high_count": sum(1 for s in scores if s >= hi),
            "medium_count": sum(1 for s in scores if mid <= s < hi),
            "low_count": sum(1 for s in scores if low <= s < mid),
            "safe_count": sum(1 for s in scores if s < low),
            "plagiarism_rate": round(sum(1 for s in scores if s >= mid) / len(scores), 4),
            "min_len_avg": round(sum(lengths) / len(lengths), 1),
            "low_confidence_count": sum(1 for r in results if r.get("low_confidence")),
        }


# ==================== 通用工具 ====================

class UnionFind:
    """并查集：把互相高度相似的学生连成团伙。"""

    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:        # 路径压缩
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int):
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self.parent[root_b] = root_a

    def buckets(self) -> dict:
        groups = {}
        for index in range(len(self.parent)):
            groups.setdefault(self.find(index), []).append(index)
        return groups


def _median(values: list) -> float:
    """中位数。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


# ==================== 历史数据兼容 ====================

def normalize_legacy_result(result: dict, thresholds: dict = None) -> dict:
    """
    把历史记录里旧口径的结果补成当前形状。

    旧记录只有三项指标（exact_match / sequence_similarity / keyword_overlap），
    这里保留它们的原始数值（那时确实是这么算的），只补上缺失的键并把
    metrics_version 标为 0；界面据此走旧渲染路径，不会把补出来的 0%
    当成「字形相似度 0%」展示给教师。
    """
    if not isinstance(result, dict):
        return result

    thresholds = thresholds or SIMILARITY_THRESHOLDS
    normalized = dict(result)

    for key in METRIC_KEYS:
        if normalized.get(key) is None:
            normalized[key] = 0.0

    normalized.setdefault("weighted_score", 0.0)
    normalized.setdefault("raw_score", normalized["weighted_score"])
    normalized.setdefault("confidence", 1.0)
    normalized.setdefault("low_confidence", False)
    normalized.setdefault("fragments", [])

    if not normalized.get("metrics_version"):
        normalized["metrics_version"] = 0

    if not normalized.get("level"):
        score = normalized["weighted_score"]
        if score >= thresholds.get("high", 0.8):
            normalized["level"] = "high"
        elif score >= thresholds.get("medium", 0.5):
            normalized["level"] = "medium"
        elif score >= thresholds.get("low", 0.3):
            normalized["level"] = "low"
        else:
            normalized["level"] = "safe"

    return normalized

