"""
作业抄袭检测系统 - AIGC 作文风格线索检测

先说清这个工具能做到什么、做不到什么：

- 它**不能**判定一篇作文是不是 AI 写的。没有任何方法能可靠做到这一点。
- 它做的是「风格线索」：测量七个可解释的指标，把最像机器腔的句子圈出来给老师看。
- 判定权始终在人手上。输出里的每一个数字都能追溯到一句人话解释，
  老师看完可以得出「不像」的结论，工具不越俎代庖。

必须承认的偏差：中学作文本来就教八股结构（总分总、首先其次综上所述），
所以阈值是按 K12 校准的，不是按成人书面语校准的。直接套用面向成人的判别口径
会把认真套用老师模板的学生误判成 AI——这类误判在高校已经出过正式事故。

默认全部本地计算，不发任何网络请求。外部接口只在老师显式勾选时参与，
而且只发送最可疑的若干句，不发全文、不发学生姓名。
"""

import re

from config import AIGC_CONFIG
from text_utils import norm_only
from llm_client import LLMClient

DETECTOR_VERSION = 1

# 句子切分用的终止符（中文为主，兼容半角）
SENTENCE_ENDS = "。！？；…!?;\n"

# ---------- 词表 ----------
# 这些表是「数据」而不是算法：覆盖不全，但每一项都能给老师指出具体词。

# 框架套话：结构模板的直接痕迹
TEMPLATE_PHRASES = (
    "综上所述", "总的来说", "总的来看", "由此观之", "由此可见", "不难发现",
    "不难看出", "值得注意的是", "需要指出的是", "毋庸置疑", "显而易见",
    "众所周知", "无独有偶", "与之相反", "反观", "诚然", "一般来说",
    "首先", "其次", "再次", "最后", "总之", "一方面", "另一方面",
    "具有重要意义", "起到", "至关重要", "扮演", "重要的角色", "离不开",
    "当今社会", "在这个", "随着", "的发展", "的背景下", "与生俱来",
)

# 强 AI 味的抽象大词：中学生日常几乎不会主动用
ABSTRACT_STRONG = (
    "赋能", "彰显", "凸显", "擘画", "赓续", "抓手", "耦合", "范式", "向度",
    "图景", "场域", "底层逻辑", "提质增效", "行稳致远", "久久为功",
    "不可或缺", "息息相关", "密不可分", "与时俱进", "举足轻重", "任重道远",
    "薪火相传", "双向奔赴", "熠熠生辉", "蔚然成风", "应运而生", "方兴未艾",
    "踔厉奋发", "笃行不怠",
)

# 中等抽象度：申论腔，但学生也会被教着写（潜移默化、砥砺这类成语就是课堂词汇），
# 所以权重压低，不能和「赋能/抓手」同等看待。
ABSTRACT_MEDIUM = (
    "体系", "格局", "内涵", "外延", "维度", "路径", "效能", "机制", "素养",
    "理念", "导向", "生态", "视域", "层面", "构建", "塑造", "践行", "弘扬",
    "助力", "意蕴", "旨归", "进路", "潜移默化", "润物无声", "砥砺",
)

# 具体性标记：真实经历会带进来的东西
CONCRETE_NOUNS = (
    "妈妈", "爸爸", "老师", "同学", "奶奶", "爷爷", "同桌", "朋友", "班主任",
    "弟弟", "妹妹", "哥哥", "姐姐", "操场", "教室", "食堂", "公交车", "地铁",
    "手机", "雨伞", "球场", "小区", "巷子", "厨房", "菜市场", "医院",
)

COLLOQUIAL_MARKS = (
    "我觉得", "我想", "我记得", "有一次", "那天", "后来", "其实", "反正",
    "有点", "有点儿", "特别", "真的", "超级", "好几个", "跟我说", "跟他说",
    "你知道吗", "一下子", "咱", "俺", "咋", "啥", "嘛", "呗", "呀", "哦",
    "嗯", "哎", "啦", "挺", "吧", "呢",
)

TIME_MARKS = (
    "去年", "前天", "昨天", "小时候", "上周", "本周", "三年级", "一年级",
    "五年级", "六年级", "初一", "初二", "初三", "寒假", "暑假", "周末",
    "傍晚", "清晨", "中午", "半夜", "放学", "下课",
)

# 对举框架：两个词同时出现在一句里才算
PARALLEL_FRAMES = (
    ("不仅", "而且"), ("不仅", "更"), ("不但", "而且"), ("既", "又"),
    ("是", "也是"), ("一边", "一边"), ("有时", "有时"), ("或是", "或是"),
    ("要么", "要么"), ("不是", "而是"),
)

# 有声音的标点：真人写作会有惊讶、疑问、省略，模型几乎只用逗号句号
VOICE_MARKS = ("！", "？", "…", "～", "——", "!", "?")

# 引用与作品名标记
QUOTE_MARKS = ("“", "”", "「", "」", "《", "》")


# ==================== 小工具 ====================

def _scale(value: float, zero_at: float, one_at: float) -> float:
    """
    把测量值线性映射成 0..1 的「AI 倾向」。

    zero_at / one_at 同时决定方向：one_at < zero_at 时值越小倾向越高。
    线性而不是阶梯，是为了让老师看到一个中间值时知道它离两侧都不远。
    """
    if one_at == zero_at:
        return 0.5
    ratio = (value - zero_at) / (one_at - zero_at)
    return max(0.0, min(1.0, ratio))


def _phrase_count(text: str, phrases) -> tuple:
    """返回 (总次数, 命中的词)。"""
    total = 0
    hits = []
    for phrase in phrases:
        count = text.count(phrase)
        if count:
            total += count
            hits.append(phrase)
    return total, hits


def _per_100(count: float, length: int) -> float:
    return count / length * 100 if length > 0 else 0.0


def _std(values) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return variance ** 0.5


def _cv(values) -> float:
    """变异系数：标准差 / 均值。分布越散越像人写的。"""
    mean = sum(values) / len(values) if values else 0.0
    if mean <= 0:
        return 0.0
    return _std(values) / mean


def split_sentences(text: str) -> list:
    """
    按终止符切句，同时保留原文下标（左闭右开），供界面高亮。

    区间已经去掉首尾空白，所以拿 text[start:end] 一定能取回同一串。
    """
    if not text:
        return []

    sentences = []
    start = 0
    for i, ch in enumerate(text):
        if ch in SENTENCE_ENDS:
            chunk = text[start:i]
            stripped = chunk.strip()
            if stripped:
                offset = start + chunk.index(stripped[0])
                sentences.append({
                    "text": stripped,
                    "start": offset,
                    "end": offset + len(stripped),
                })
            start = i + 1
    tail = text[start:].strip()
    if tail:
        offset = start + text[start:].index(tail[0])
        sentences.append({"text": tail, "start": offset, "end": offset + len(tail)})
    return sentences


# ==================== 检测器 ====================

class AIGCDetector:
    """六个可解释风格信号 + 逐句可疑度证据 + 可选外部接口增强。"""

    def __init__(self, config: dict = None):
        self.config = {key: (dict(value) if isinstance(value, dict) else value)
                       for key, value in AIGC_CONFIG.items()}
        self.levels = dict(self.config["levels"])
        self.weights = dict(self.config["weights"])
        self.update_config(config)

    def update_config(self, config: dict) -> None:
        """设置面板保存后热更新；缺项沿用出厂值。"""
        for key, value in (config or {}).items():
            self.config[key] = dict(value) if isinstance(value, dict) else value
        weights = dict(AIGC_CONFIG["weights"])
        weights.update(self.config.get("weights") or {})
        self.weights = weights
        levels = dict(AIGC_CONFIG["levels"])
        levels.update(self.config.get("levels") or {})
        self.levels = levels

    # ---------- 入口 ----------

    def analyze(self, text: str, use_external: bool = False,
                client: LLMClient = None, student_name: str = "") -> dict:
        """
        检测一篇作文的 AI 风格线索。

        :param text: 作文原文
        :param use_external: 是否调用大模型做二次判别（老师显式勾选才为真）
        :param student_name: 仅用于脱敏，不会发给接口
        :return: 成功时含 ai_score / level / signals / evidence / notice，
                 文本过短或外部接口失败时带 error_code 与下一步提示
        """
        text = (text or "").strip()
        length = len(norm_only(text))
        min_chars = int(self.config.get("min_chars", 60))
        if length < min_chars:
            return {
                "success": False,
                "error_code": "too_short",
                "error": f"作文太短，风格线索不成立（至少 {min_chars} 字）",
                "hint": "短的摘抄、几句话的感想无法判断写法来源，请粘贴完整习作",
            }

        signals, evidence, stats = self._measure(text, length)
        result = self._local_result(length, signals, evidence, stats)

        if use_external:
            active = client or LLMClient(self.config.get("llm") or {})
            result = self._refine(result, evidence, active, student_name)
        return result

    # ---------- 测量 ----------

    def _measure(self, text: str, length: int) -> tuple:
        sentences = split_sentences(text)
        lengths = [max(1, len(norm_only(item["text"]))) for item in sentences]
        paragraphs = [p for p in re.split(r"\n+", text) if p.strip()]
        stats = {
            "sentences": len(sentences),
            "paragraphs": len(paragraphs),
            "avg_sentence_len": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        }
        signals = [
            self._signal_sentence_uniformity(lengths),
            self._signal_template_density(text, length),
            self._signal_abstract_density(text, length),
            self._signal_punctuation(text, length),
            self._signal_concreteness(text, length),
            self._signal_structure(text, sentences, lengths, paragraphs),
        ]
        evidence = self._build_evidence(sentences, lengths)
        return signals, evidence, stats

    def _new_signal(self, key, label, weight):
        return {"key": key, "label": label, "weight": round(float(weight), 3),
                "applicable": True, "metric": "", "tendency": 0.0, "reading": ""}

    def _signal_sentence_uniformity(self, lengths: list) -> dict:
        """句长整齐度：AI 生成的句子长短很平均，真人会有两三字的短促句。"""
        signal = self._new_signal("sentence_uniformity", "句长整齐度",
                                  self.weights["sentence_uniformity"])
        if len(lengths) < 4:
            signal["applicable"] = False
            signal["reading"] = "句子太少，这项无法判断"
            return signal

        cv = _cv(lengths)
        signal["metric"] = f"句长波动系数 {cv:.2f}"
        signal["tendency"] = _scale(cv, 0.62, 0.22)
        if cv >= 0.5:
            signal["reading"] = "句子长短错落，更像随性书写"
        elif cv >= 0.35:
            signal["reading"] = "句长波动中等，看不出明显倾向"
        else:
            signal["reading"] = "句子长度十分接近，模板化倾向偏高"
        return signal

    def _signal_template_density(self, text: str, length: int) -> dict:
        """套话框架密度：首先/其次/综上所述这类结构连接词的出现频率。"""
        signal = self._new_signal("template_density", "套话框架",
                                  self.weights["template_density"])
        count, hits = _phrase_count(text, TEMPLATE_PHRASES)
        density = _per_100(count, length)
        signal["metric"] = f"每百字 {density:.1f} 处"
        signal["tendency"] = _scale(density, 0.5, 3.2)
        if density <= 0.8:
            signal["reading"] = "结构连接词用得克制"
        elif density <= 1.8:
            signal["reading"] = f"连接词偏多（{'、'.join(hits[:6])}），课堂作文常这样写"
        else:
            signal["reading"] = f"框架词密集（{'、'.join(hits[:8])}），像按模板铺出来的"
        signal["hits"] = hits[:10]
        return signal

    def _signal_abstract_density(self, text: str, length: int) -> dict:
        """抽象大词密度：中文大模型偏爱的一类词，学生日常不会主动用。"""
        signal = self._new_signal("abstract_density", "抽象大词",
                                  self.weights["abstract_density"])
        strong, strong_hits = _phrase_count(text, ABSTRACT_STRONG)
        medium, medium_hits = _phrase_count(text, ABSTRACT_MEDIUM)
        score = strong + 0.4 * medium
        density = _per_100(score, length)
        signal["metric"] = f"每百字 {density:.1f} 处"
        signal["tendency"] = _scale(density, 0.1, 1.4)
        shown = strong_hits + medium_hits
        if density <= 0.2:
            signal["reading"] = "没有明显的书面大词"
        elif density <= 0.7:
            signal["reading"] = f"少量大词（{'、'.join(shown[:5])}），需结合全文看"
        else:
            signal["reading"] = f"大词密集（{'、'.join(shown[:8])}），腔调偏成人化"
        signal["hits"] = shown[:12]
        return signal

    def _signal_punctuation(self, text: str, length: int) -> dict:
        """标点声音：真人会不自觉用感叹、疑问、省略，模型几乎只用逗号句号。"""
        signal = self._new_signal("punctuation_monotony", "标点声音",
                                  self.weights["punctuation_monotony"])
        count = sum(text.count(mark) for mark in VOICE_MARKS)
        density = _per_100(count, length)
        signal["metric"] = f"每百字 {density:.1f} 处"
        signal["tendency"] = _scale(density, 0.35, 0.0)
        if density >= 0.4:
            signal["reading"] = "有感叹/疑问/省略，语气有起伏"
        elif density >= 0.1:
            signal["reading"] = "偶有语气标点"
        else:
            signal["reading"] = "全程只有逗号句号，语气十分平直"
        return signal

    def _signal_concreteness(self, text: str, length: int) -> dict:
        """具体信息：数字、时间、人物、口语痕迹。真实经历会自带这些。"""
        signal = self._new_signal("concreteness_deficit", "具体信息",
                                  self.weights["concreteness_deficit"])
        digits = len(re.findall(r"\d", text))
        nouns, noun_hits = _phrase_count(text, CONCRETE_NOUNS)
        colloq, colloq_hits = _phrase_count(text, COLLOQUIAL_MARKS)
        times, time_hits = _phrase_count(text, TIME_MARKS)
        quotes = sum(text.count(mark) for mark in QUOTE_MARKS)
        total = digits + nouns + colloq + times + quotes
        density = _per_100(total, length)
        signal["metric"] = f"每百字 {density:.1f} 处"
        # 真人记叙文的密度本来就高（一段里全是「去年」「妈妈」「三页」），
        # 判别的实际区间在零附近，所以这里把上限压到 4，不让它轻易饱和。
        signal["tendency"] = _scale(density, 4.0, 0.35)
        if density >= 2.5:
            signal["reading"] = "有大量数字、人名、时间和口语痕迹"
        elif density >= 1.0:
            signal["reading"] = "有一定具体信息"
        else:
            signal["reading"] = "几乎全是抽象表述，缺少具体时间/人物/数字"
        signal["hits"] = (noun_hits + time_hits + colloq_hits)[:10]
        return signal

    def _signal_structure(self, text: str, sentences: list, lengths: list,
                          paragraphs: list) -> dict:
        """篇章规整度：段落长度是否齐平、句首是否重复、对举框架有多少。"""
        signal = self._new_signal("structure_regularity", "篇章规整度",
                                  self.weights["structure_regularity"])
        parts = []

        if len(paragraphs) >= 3:
            para_cv = _cv([max(1, len(norm_only(p.strip()))) for p in paragraphs])
            parts.append(_scale(para_cv, 0.5, 0.15))
        if len(sentences) >= 5:
            initials = [norm_only(item["text"])[:2] for item in sentences
                        if len(item["text"]) >= 6]
            repeated = max((initials.count(one) for one in set(initials)), default=0)
            parts.append(_scale(repeated, 1, 4))
        frames = sum(1 for first, second in PARALLEL_FRAMES
                     if first in text and second in text)
        parts.append(_scale(frames, 1, 5))

        tendency = sum(parts) / len(parts)
        signal["tendency"] = tendency
        signal["metric"] = f"对举框架 {frames} 组"
        if tendency <= 0.3:
            signal["reading"] = "段落长短与句首用词不规则"
        elif tendency <= 0.6:
            signal["reading"] = "结构有一定工整度，课堂训练也会这样"
        else:
            signal["reading"] = "段落齐平、句首反复、对举密集，排比感很强"
        return signal

    # ---------- 逐句证据 ----------

    def _build_evidence(self, sentences: list, lengths: list) -> list:
        """给每句打分，圈出最可疑的几句。这是老师能核对的东西。"""
        if not sentences:
            return []

        mean_len = sum(lengths) / len(lengths)
        scored = []
        for item, n in zip(sentences, lengths):
            sentence = item["text"]
            strong_hits = [word for word in ABSTRACT_STRONG if word in sentence]
            medium_hits = [word for word in ABSTRACT_MEDIUM if word in sentence]
            frame_hits = [word for word in TEMPLATE_PHRASES if word in sentence]
            lexical = len(strong_hits) + 0.4 * len(medium_hits) + 0.6 * len(frame_hits)
            lexical_part = min(1.0, lexical / 2.5)

            # 句长恰好卡在全文平均值上：真人写作会一句长一句短
            length_part = 1.0 - min(1.0, abs(n - mean_len) / max(mean_len, 1.0))

            concrete = (len(re.findall(r"\d", sentence))
                        + sum(1 for word in CONCRETE_NOUNS if word in sentence)
                        + sum(1 for word in COLLOQUIAL_MARKS if word in sentence)
                        + sum(1 for word in TIME_MARKS if word in sentence))
            concrete_part = 1.0 - min(1.0, concrete / 3.0)

            has_voice = any(mark in sentence for mark in VOICE_MARKS)
            score = (0.5 * lexical_part + 0.2 * length_part
                     + 0.3 * concrete_part - (0.12 if has_voice else 0.0))
            score = max(0.0, min(1.0, score))

            reasons = []
            if strong_hits or medium_hits:
                reasons.append("抽象大词：" + "、".join(strong_hits + medium_hits[:3]))
            if frame_hits:
                reasons.append("套话框架：" + "、".join(frame_hits[:3]))
            if concrete == 0:
                reasons.append("句内没有可识别的时间、人物或数字")
            if length_part >= 0.85:
                reasons.append("长度与全文平均句长几乎一致")
            if has_voice:
                reasons.append("带感叹/疑问/省略语气")

            scored.append({
                "start": item["start"], "end": item["end"], "text": sentence,
                "score": round(score, 3), "reasons": reasons,
                "concrete": concrete,
            })

        keep = int(self.config.get("evidence_max", 6))
        top = sorted(scored, key=lambda one: one["score"], reverse=True)[:keep]
        return sorted(top, key=lambda one: one["start"])

    # ---------- 汇总 ----------

    def _local_result(self, length: int, signals: list,
                      evidence: list, stats: dict) -> dict:
        used = [one for one in signals if one["applicable"] and one["weight"] > 0]
        weight_sum = sum(one["weight"] for one in used)
        score = (sum(one["weight"] * one["tendency"] for one in used) / weight_sum
                 if weight_sum else 0.0)

        reliable = int(self.config.get("reliable_chars", 200))
        confidence = min(1.0, length / reliable) if reliable > 0 else 1.0
        low_confidence = length < reliable
        if low_confidence:
            cap = float(self.config.get("short_text_cap", 0.7))
            score = min(score, cap)

        score = round(score, 4)
        level = self._level(score)

        result = {
            "success": True,
            "ai_score": score,
            "local_score": score,
            "level": level,
            "level_label": LEVEL_LABELS[level],
            "confidence": round(confidence, 3),
            "low_confidence": low_confidence,
            "char_count": length,
            "signals": sorted(signals, key=lambda one: one["tendency"], reverse=True),
            "evidence": evidence,
            "statistics": stats,
            "detector_version": DETECTOR_VERSION,
            "external": None,
            "notice": NOTICE,
        }
        if low_confidence:
            result["notice"] = NOTICE + f"（全文仅 {length} 字，样本偏短，这一档更容易看错）"
        return result

    def _level(self, score: float) -> str:
        if score >= float(self.levels.get("strong", 0.65)):
            return "strong"
        if score >= float(self.levels.get("medium", 0.45)):
            return "medium"
        return "weak"

    # ---------- 外部接口增强 ----------

    def _refine(self, result: dict, evidence: list,
                client: LLMClient, student_name: str) -> dict:
        """
        只把最可疑的几句送去接口，融合两个分数。

        不发全文（省钱），不发学生姓名（隐私）。这不等于完全匿名：
        作文本身可能写着同学的名字，所以界面上必须让老师自己确认。
        """
        payload = self._review_payload(evidence, student_name)
        if not payload["sentences"]:
            result["external"] = {"success": False,
                                  "error": "没有可用于二次判别的句子",
                                  "error_code": "no_evidence"}
            return result

        reply = client.chat_json(payload["prompt"], _EXTERNAL_SYSTEM, max_tokens=500)
        if not reply.get("success"):
            result["external"] = {
                "success": False,
                "error": reply.get("error", "外部判别失败"),
                "error_code": reply.get("error_code", "llm_failed"),
                "hint": "本地线索仍然有效，可以只看本地结果",
            }
            return result

        data = reply.get("data") or {}
        try:
            external_score = float(data.get("ai_score"))
        except (TypeError, ValueError):
            result["external"] = {"success": False, "error": "接口没给出可用的分值",
                                  "error_code": "bad_score"}
            return result
        external_score = max(0.0, min(1.0, external_score))

        weight = max(0.0, min(0.6, float(self.config.get("external_weight", 0.4))))
        fused = (1.0 - weight) * result["local_score"] + weight * external_score

        result["external"] = {
            "success": True,
            "score": round(external_score, 4),
            "reasons": [str(one).strip() for one in (data.get("reasons") or [])
                        if str(one).strip()][:6],
            "uncertainty": str(data.get("uncertainty") or "").strip(),
            "sentences_sent": payload["count"],
            "from_cache": bool(reply.get("from_cache")),
            "cost": reply.get("cost", 0.0),
            "model": client.model,
            "fused_with": round(weight, 2),
        }
        result["ai_score"] = round(fused, 4)
        result["level"] = self._level(fused)
        result["level_label"] = LEVEL_LABELS[result["level"]]
        result["notice"] = (result["notice"]
                            + "｜外部接口结果按 4 成权重与本地线索融合，两边分值都已列出，可对照查看")
        return result

    def _review_payload(self, evidence: list, student_name: str) -> dict:
        limit = int(self.config.get("review_sentences_max", 8))
        chosen = sorted(evidence, key=lambda one: one["score"], reverse=True)[:limit]
        lines = []
        for index, item in enumerate(chosen, start=1):
            cleaned = anonymize(item["text"], student_name)[:200]
            lines.append(f"{index}. {cleaned}")
        prompt = (
            "下面是一篇中小学作文里最可疑的若干句话，已做轻量脱敏。\n\n"
            + "\n".join(lines)
            + "\n\n请判断这些句子整体更像 AI 生成还是学生自己写的。"
            "按这个结构只输出 JSON：\n"
            '{"ai_score": 0 到 1 之间的小数, "reasons": ["依据1", "依据2"],'
            ' "uncertainty": "看不出或信息不足的地方，没有就留空字符串"}'
        )
        return {"prompt": prompt, "count": len(lines), "sentences": lines}


# 界面上必须原样展示这两段话：一个说明结果不是什么，一个说明结果是什么
NOTICE = (
    "这是风格线索，不是判定。AI 检测对认真套用作文模板的学生存在系统性误判，"
    "任何数字都不能当作「抄袭/AI 写作」的证据，只适合用来决定先找谁聊一聊。"
)

LEVEL_LABELS = {
    "strong": "线索偏强",
    "medium": "线索中等",
    "weak": "线索偏弱",
}

_EXTERNAL_SYSTEM = (
    "你是文本风格分析助手，服务对象是一位中小学老师。任务：判断给出的几句话更像"
    "AI 生成的，还是学生自己写的。\n"
    "硬性要求：\n"
    "1. 结构工整、用词书面本身不构成 AI 证据——中小学作文就是被这样教的；\n"
    "2. 只在看到明确痕迹时才提高分值，例如成套的抽象大词、语气完全平直、"
    "句子长度异常一致、通篇没有任何具体的人/时间/数字；\n"
    "3. 给出的句子是节选，看不到全文，请在 uncertainty 里说明这种局限；\n"
    "4. 不评价作文好坏，不续写，不寒暄，不声明自己是 AI；\n"
    "5. 只输出 JSON。"
)


def anonymize(text: str, student_name: str = "") -> str:
    """
    发给接口前的轻量脱敏：长数字串（手机号/学号/QQ）与老师填写的学生姓名。

    只能做到这一步——作文正文里写明的同学名字、学校名无法可靠识别，
    所以是否要用外部接口，决定权留给老师。
    """
    out = re.sub(r"\d{5,}", "[号码]", text or "")
    name = (student_name or "").strip()
    if name and len(name) >= 2:
        out = out.replace(name, "[某人]")
    return out
