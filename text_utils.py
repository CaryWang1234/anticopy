"""
作业抄袭检测系统 - 文本处理基础层

本模块只做「把文字变成可比对的形式」这件事，不做任何打分，供 similarity.py 使用：

1. normalize()            文本归一化，同时产出「归一化字符下标 -> 原文下标」映射。
                          这个映射是雷同片段能精确圈回教师看到的原文的关键。
2. char_ngram_counter()   字符 2/3-gram 词频
3. term_counter()         jieba 分词词频（过滤单字与停用词）
4. find_fragments()       定位两段文本之间连续的雷同片段

设计约束：
- 只依赖标准库 + jieba，不引入新的第三方依赖
- 归一化过程必须保持「下标可回溯」，因此所有变换都是逐字符或等长替换
"""

import math
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

import jieba


# ==================== 常量 ====================

# 停用词：过于常见，出现在两边都不能说明抄袭
STOP_WORDS = frozenset({
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
    "我们", "你们", "他们", "什么", "怎么", "这个", "那个", "可以",
    "因为", "所以", "但是", "而且", "然后", "还是", "还有", "就是",
    "一样", "这么", "那么", "一下", "已经", "如果", "虽然", "不过",
})

# 保留的符号：在数学/理科答案里带语义，不应当作标点丢弃
KEEP_SYMBOLS = frozenset({"%", "+", "-", "=", "/", "^", "*", "$", "°"})

# 等长单位归一：左右长度必须一致，否则会破坏下标映射
UNIT_SUBS = (
    ("千米", "km"),
    ("公里", "km"),
    ("厘米", "cm"),
    ("毫米", "mm"),
    ("分米", "dm"),
    ("千克", "kg"),
    ("公斤", "kg"),
)

# 雷同片段参数
FRAGMENT_MIN_LEN = 8        # 匹配块低于此长度不认为是有意义的雷同
FRAGMENT_MIN_KEEP = 12      # 低于此长度还要再过一道「常用短语」过滤
FRAGMENT_MERGE_GAP = 3      # 两个匹配块之间间隔不超过这么多字符则合并
MAX_FRAGMENTS = 20

# 常见套话：短片段如果只是这些，不足以作为抄袭证据
COMMON_PHRASES = frozenset({
    "答案是", "综上所述", "由此可得", "根据题意", "由题意", "如图所示",
    "我们可以", "可以得出", "也就是说", "一般地", "已知条件", "由上式",
    "代入得", "化简得", "所以有", "因此有", "同理可得", "由此可见",
    "需要注意的是", "理论上", "实际上", "事实上", "换句话说", "解得",
})

# 繁简对照（部分覆盖，仅在高频常用字范围内）。
# 说明：这是一张数据表而非算法，覆盖不全；默认关闭，由设置项 normalize.traditional 控制。
TRAD_TO_SIMP = {
    "個": "个", "們": "们", "為": "为", "爲": "为", "這": "这", "來": "来",
    "說": "说", "時": "时", "間": "间", "學": "学", "習": "习", "題": "题",
    "計": "计", "數": "数", "據": "据", "點": "点", "線": "线", "圖": "图",
    "標": "标", "準": "准", "確": "确", "認": "认", "過": "过", "進": "进",
    "還": "还", "對": "对", "錯": "错", "開": "开", "關": "关", "現": "现",
    "實": "实", "發": "发", "髮": "发", "長": "长", "結": "结", "論": "论",
    "證": "证", "義": "义", "語": "语", "讀": "读", "課": "课", "冊": "册",
    "業": "业", "級": "级", "師": "师", "試": "试", "驗": "验", "觀": "观",
    "應": "应", "該": "该", "條": "条", "環": "环", "產": "产", "員": "员",
    "務": "务", "動": "动", "體": "体", "積": "积", "極": "极", "樣": "样",
    "種": "种", "類": "类", "組": "组", "織": "织", "統": "统", "節": "节",
    "範": "范", "圍": "围", "價": "价", "錢": "钱", "費": "费", "車": "车",
    "飛": "飞", "機": "机", "場": "场", "館": "馆", "醫": "医", "藥": "药",
    "無": "无", "從": "从", "讓": "让", "沒": "没", "純": "纯", "記": "记",
    "憶": "忆", "轉": "转", "換": "换", "變": "变", "溫": "温", "熱": "热",
    "東": "东", "詞": "词", "舊": "旧", "於": "于", "並": "并", "萬": "万",
    "億": "亿", "兩": "两", "覺": "觉", "響": "响", "樂": "乐", "釋": "释",
    "總": "总", "屬": "属", "櫃": "柜", "檔": "档", "網": "网", "絡": "络",
    "電": "电", "腦": "脑", "農": "农", "銷": "销", "額": "额", "質": "质",
    "適": "适", "簡": "简", "單": "单", "雜": "杂", "復": "复", "複": "复",
    "補": "补", "償": "偿", "護": "护", "練": "练", "慣": "惯", "態": "态",
    "誤": "误", "區": "区", "劃": "划", "創": "创", "製": "制", "導": "导",
    "執": "执", "參": "参", "閱": "阅", "輸": "输", "贏": "赢", "賽": "赛",
    "鐘": "钟", "鎖": "锁", "錄": "录", "鑰": "钥", "顯": "显", "預": "预",
    "順": "顺", "頻": "频", "顧": "顾", "風": "风", "飯": "饭", "飲": "饮",
    "餓": "饿", "馬": "马", "鳥": "鸟", "魚": "鱼", "黃": "黄", "齊": "齐",
    "龍": "龙", "龜": "龟", "嗎": "吗", "廢": "废", "廚": "厨", "廳": "厅",
    "張": "张", "彈": "弹", "強": "强", "歸": "归", "歲": "岁", "歷": "历",
    "殘": "残", "殺": "杀", "氣": "气", "決": "决", "況": "况", "淨": "净",
    "涼": "凉", "減": "减", "測": "测", "濕": "湿", "濟": "济", "滿": "满",
    "漸": "渐", "潔": "洁", "濃": "浓", "牆": "墙", "獨": "独", "獲": "获",
    "畢": "毕", "監": "监", "盤": "盘", "眾": "众", "稱": "称", "穩": "稳",
    "窮": "穷", "筆": "笔", "簽": "签", "籃": "篮", "糾": "纠", "紀": "纪",
    "約": "约", "紅": "红", "紙": "纸", "細": "细", "終": "终", "絕": "绝",
    "絲": "丝", "經": "经", "綠": "绿", "維": "维", "緊": "紧", "編": "编",
    "緩": "缓", "縮": "缩", "績": "绩", "繫": "系", "續": "续", "罰": "罚",
    "羅": "罗", "聯": "联", "聲": "声", "職": "职", "聽": "听", "脈": "脉",
    "腳": "脚", "膚": "肤", "臉": "脸", "臨": "临", "舉": "举", "艱": "艰",
    "藝": "艺", "蘭": "兰", "處": "处", "虛": "虚", "號": "号", "蟲": "虫",
    "裝": "装", "裡": "里", "裏": "里", "褲": "裤", "見": "见", "規": "规",
    "視": "视", "覽": "览", "訂": "订", "訊": "讯", "討": "讨", "訓": "训",
    "訪": "访", "設": "设", "許": "许", "訴": "诉", "診": "诊", "註": "注",
    "評": "评", "詩": "诗", "話": "话", "詳": "详", "誌": "志", "誰": "谁",
    "調": "调", "談": "谈", "請": "请", "諾": "诺", "謀": "谋", "講": "讲",
    "謝": "谢", "識": "识", "譜": "谱", "議": "议", "豐": "丰", "豬": "猪",
    "貝": "贝", "負": "负", "財": "财", "貢": "贡", "責": "责", "貨": "货",
    "貴": "贵", "買": "买", "貸": "贷", "貼": "贴", "資": "资", "賓": "宾",
    "賬": "账", "贈": "赠", "趕": "赶", "趨": "趋", "躍": "跃", "軌": "轨",
    "軍": "军", "軟": "软", "較": "较", "載": "载", "輔": "辅", "輕": "轻",
    "輛": "辆", "輪": "轮", "辦": "办", "迴": "回", "連": "连", "週": "周",
    "達": "达", "違": "违", "遠": "远", "選": "选", "遺": "遗", "邊": "边",
    "邏": "逻", "郵": "邮", "鄉": "乡", "採": "采", "鋼": "钢", "鍋": "锅",
    "鎮": "镇", "門": "门", "閉": "闭", "閒": "闲", "陽": "阳", "陰": "阴",
    "隊": "队", "階": "阶", "隨": "随", "險": "险", "隱": "隐", "雖": "虽",
    "雙": "双", "雞": "鸡", "離": "离", "難": "难", "雲": "云", "靜": "静",
    "頁": "页", "頂": "顶", "項": "项", "須": "须", "頭": "头", "顏": "颜",
    "願": "愿", "麗": "丽", "黨": "党", "乾": "干", "幹": "干", "麵": "面",
    "佈": "布",
}


# ==================== 归一化 ====================

def normalize(text: str, traditional: bool = False):
    """
    归一化文本，并保留「归一化下标 -> 原文下标」映射。

    :param text: 原文
    :param traditional: 是否做繁转简（部分覆盖，默认关闭）
    :return: (norm_str, index_map)

    变换顺序：逐字符 NFKC（全角转半角）→ 小写 → 可选繁转简 → 丢弃空白/控制符/标点
    之后再对「等长单位」做整体替换（等长替换不会破坏 index_map）
    """
    if not text:
        return "", []

    out_chars = []
    index_map = []

    for i, raw in enumerate(text):
        # NFKC 可能把 1 个字符展开成多个（如 ⅓ -> 1⁄3），逐字符处理才能保住映射
        for ch in unicodedata.normalize("NFKC", raw):
            ch = ch.lower()
            if traditional:
                ch = TRAD_TO_SIMP.get(ch, ch)

            category = unicodedata.category(ch)[0]
            if category in ("C", "Z"):          # 控制符 / 空白分隔符
                continue
            if category in ("P", "S") and ch not in KEEP_SYMBOLS:   # 标点 / 符号
                continue

            out_chars.append(ch)
            index_map.append(i)

    norm = "".join(out_chars)
    for pattern, replacement in UNIT_SUBS:
        if pattern in norm:
            norm = norm.replace(pattern, replacement)

    return norm, index_map


def norm_only(text: str, traditional: bool = False) -> str:
    """只取归一化后的字符串（不需要下标映射时用）。"""
    return normalize(text, traditional=traditional)[0]


# ==================== 向量构建 ====================

def char_ngram_counter(norm: str, sizes=(2, 3)) -> Counter:
    """
    字符 n-gram 词频。键为 (size, gram)，避免 2-gram 与 3-gram 撞键。
    """
    counter = Counter()
    n = len(norm)
    for size in sizes:
        if n < size:
            continue
        for i in range(n - size + 1):
            counter[(size, norm[i:i + size])] += 1
    return counter


def term_counter(norm: str) -> Counter:
    """jieba 分词词频，过滤单字与停用词（保留数字，答案里的数值有意义）。"""
    counter = Counter()
    if not norm:
        return counter
    for word in jieba.cut(norm):
        word = word.strip()
        if len(word) < 2 or word in STOP_WORDS:
            continue
        counter[word] += 1
    return counter


def cosine(counter_a: Counter, counter_b: Counter, sublinear: bool = False) -> float:
    """
    两个词频 Counter 的余弦相似度。
    sublinear=True 时把 tf 压成 1+log(tf)，避免高频词过度主导（长答案更稳）。
    """
    if not counter_a or not counter_b:
        return 0.0

    def weight(tf: int) -> float:
        if not sublinear:
            return float(tf)
        return 1.0 + math.log(tf)

    # 遍历较小的一侧，减少字典查找
    if len(counter_a) > len(counter_b):
        counter_a, counter_b = counter_b, counter_a

    dot = 0.0
    for key, tf_a in counter_a.items():
        tf_b = counter_b.get(key)
        if tf_b:
            dot += weight(tf_a) * weight(tf_b)

    if dot == 0.0:
        return 0.0

    norm_a = sum(weight(tf) ** 2 for tf in counter_a.values()) ** 0.5
    norm_b = sum(weight(tf) ** 2 for tf in counter_b.values()) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def jaccard(set_a, set_b) -> float:
    """集合 Jaccard 相似系数。"""
    if not set_a or not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def containment(set_a, set_b) -> float:
    """
    短边包含度：交集 / 较短一侧的规模。
    用于捕捉「只抄了一半」——Jaccard 会因为长边太大而稀释这种情况。
    """
    if not set_a or not set_b:
        return 0.0
    smaller = len(set_a) if len(set_a) <= len(set_b) else len(set_b)
    if smaller == 0:
        return 0.0
    return len(set_a & set_b) / smaller


# ==================== 雷同片段定位 ====================

def _is_trivial(text: str) -> bool:
    """片段是否只是数字/符号这类巧合雷同（例如两边答案都是 42）。"""
    for ch in text:
        if not (ch.isdigit() or ch in ".%+-=/*^"):
            return False
    return True


def find_fragments(norm_a: str, map_a: list, norm_b: str, map_b: list,
                   min_len: int = FRAGMENT_MIN_LEN,
                   max_frags: int = MAX_FRAGMENTS) -> list:
    """
    定位两段归一化文本之间连续的雷同片段。

    :param norm_a: A 的归一化文本
    :param map_a: A 的归一化下标 -> 原文下标
    :return: [{"a_start","a_end","b_start","b_end","text","len"}, ...]
             区间为原文下标，左闭右开，可直接用于高亮
    """
    if not norm_a or not norm_b or not map_a or not map_b:
        return []

    matcher = SequenceMatcher(None, norm_a, norm_b, autojunk=False)
    blocks = [(b.a, b.b, b.size) for b in matcher.get_matching_blocks()
              if b.size >= min_len]
    if not blocks:
        return []

    # 合并间隔很近的块：学生改几个字不应把一个长片段切断
    merged = []
    for a, b, size in blocks:
        if merged:
            last_a, last_b, last_size = merged[-1]
            if (a - (last_a + last_size) <= FRAGMENT_MERGE_GAP
                    and b - (last_b + last_size) <= FRAGMENT_MERGE_GAP):
                merged[-1] = (last_a, last_b, (a + size) - last_a)
                continue
        merged.append((a, b, size))

    fragments = []
    for a, b, size in merged:
        text = norm_a[a:a + size]
        if _is_trivial(text):
            continue
        if size < FRAGMENT_MIN_KEEP and text in COMMON_PHRASES:
            continue

        fragments.append({
            "a_start": map_a[a],
            "a_end": map_a[a + size - 1] + 1,
            "b_start": map_b[b],
            "b_end": map_b[b + size - 1] + 1,
            "text": text,
            "len": size,
        })

    fragments.sort(key=lambda f: f["len"], reverse=True)
    return fragments[:max_frags]


def expand_fragment(orig: str, start: int, end: int, pad: int = 0) -> str:
    """按需扩展片段区间（给界面留一点上下文）。"""
    if not orig:
        return ""
    s = max(0, start - pad)
    e = min(len(orig), end + pad)
    return orig[s:e]
