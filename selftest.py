"""
作业抄袭检测系统 - 算法自测

无第三方测试框架，直接 `python selftest.py` 运行。
覆盖：文本归一化与下标映射、片段定位、相似度各维度、互查聚类、整卷汇总。

任何一条断言失败即返回非 0 退出码，便于在改动后快速回归。
"""

import sys
import traceback

# Windows 控制台可能不是 UTF-8，先兜住，避免打印中文报错
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


_PASSED = 0
_FAILED = []


def check(name, condition, detail=""):
    global _PASSED
    if condition:
        _PASSED += 1
        print(f"  PASS  {name}")
    else:
        _FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ==================== S1 文本处理基础层 ====================

def test_normalize():
    print("\n[归一化]")
    from text_utils import normalize, norm_only

    norm, idx = normalize("Python 是一种高级语言。")
    check("丢弃空白与标点", norm == "python是一种高级语言", f"实际={norm!r}")
    check("下标映射长度一致", len(norm) == len(idx), f"{len(norm)} vs {len(idx)}")
    check("下标映射单调不减", all(idx[i] <= idx[i + 1] for i in range(len(idx) - 1)))
    check("下标可回溯原文",
          all("Python 是一种高级语言。"[idx[i]].lower() == norm[i] for i in range(4)),
          f"idx={idx[:4]}")

    norm2, _ = normalize(norm)
    check("归一化幂等", norm2 == norm, f"{norm2!r} != {norm!r}")

    check("全角转半角", norm_only("１２３ＡＢ") == "123ab", repr(norm_only("１２３ＡＢ")))
    check("全角百分号归一", norm_only("８０％") == "80%", repr(norm_only("８０％")))
    check("千分位空格被吃掉", norm_only("1 000") == "1000", repr(norm_only("1 000")))
    check("等长单位替换保持下标", norm_only("5千米") == "5km", repr(norm_only("5千米")))

    check("繁转简默认关闭", norm_only("學習") == "學習", repr(norm_only("學習")))
    check("繁转简开启后生效", norm_only("學習", traditional=True) == "学习",
          repr(norm_only("學習", traditional=True)))

    check("空输入不炸", normalize("") == ("", []))


def test_fragments():
    print("\n[雷同片段定位]")
    from text_utils import normalize, find_fragments

    shared = "光合作用是植物利用光能合成有机物的过程"
    text_a = "前面先写一些完全不相关的内容" + shared + "后面再写一点结尾的话"
    text_b = "开头和别人不一样的一段说明文字" + shared + "最后是不同的收尾语句"

    norm_a, map_a = normalize(text_a)
    norm_b, map_b = normalize(text_b)
    frags = find_fragments(norm_a, map_a, norm_b, map_b)

    check("找到至少一个片段", len(frags) >= 1)
    if frags:
        f = frags[0]
        check("片段长度正确", f["len"] == len(shared), f"len={f['len']}")
        check("A 侧原文切片逐字相等", text_a[f["a_start"]:f["a_end"]] == shared,
              repr(text_a[f["a_start"]:f["a_end"]]))
        check("B 侧原文切片逐字相等", text_b[f["b_start"]:f["b_end"]] == shared,
              repr(text_b[f["b_start"]:f["b_end"]]))

    # 短片段+纯数字不做为证据
    norm_c, map_c = normalize("答案是42")
    norm_d, map_d = normalize("结果是42")
    check("纯数字雷同被过滤", find_fragments(norm_c, map_c, norm_d, map_d) == [])

    # 完全不同的文本不应报片段
    norm_e, map_e = normalize("今天天气很好适合出门散步")
    norm_f, map_f = normalize("数学作业的第三题需要用方程求解")
    check("无关文本无片段", find_fragments(norm_e, map_e, norm_f, map_f) == [])


def test_vectors():
    print("\n[向量与相似系数]")
    from text_utils import char_ngram_counter, cosine, jaccard, containment, term_counter

    counter = char_ngram_counter("abcd", sizes=(2, 3))
    check("n-gram 数量正确", sum(counter.values()) == 5, f"实际={sum(counter.values())}")

    same = char_ngram_counter("光合作用")
    check("同一向量余弦为 1", close(cosine(same, same), 1.0), repr(cosine(same, same)))
    check("同一集合 Jaccard 为 1", close(jaccard({"a", "b"}, {"a", "b"}), 1.0))
    check("无关集合 Jaccard 为 0", jaccard({"a"}, {"b"}) == 0.0)
    check("包含度按短边计算", close(containment({"a", "b"}, {"a", "b", "c"}), 1.0),
          repr(containment({"a", "b"}, {"a", "b", "c"})))
    check("空集合安全", cosine(term_counter(""), term_counter("")) == 0.0)

    # 词频：重复词应比只出现一次更相似
    heavy = term_counter("光合作用光合作用光合作用")
    light = term_counter("光合作用")
    check("词频余弦介于 0 和 1", 0.0 < cosine(heavy, light, sublinear=True) <= 1.0)


# ==================== S2 相似度算法 ====================

DEMO_STANDARD = ("Python是一种高级编程语言，由Guido van Rossum于1989年发明。"
                 "Python采用缩进表示代码块，支持面向对象、函数式和过程式编程范式。")
DEMO_ANSWERS = [
    ("张三", "Python是一种高级编程语言，由Guido van Rossum在1989年创造。"
             "Python使用缩进来表示代码块，支持面向对象、函数式和过程式编程。"),
    ("李四", "Java是由Sun Microsystems开发的面向对象编程语言。"
             "Java的特点是跨平台、安全性高，广泛应用于企业级开发。"),
    ("王五", "Python是高级编程语言，Guido van Rossum发明。"
             "用缩进表示代码块，支持多种编程范式包括面向对象和函数式。"),
    ("赵六", "C语言是由Dennis Ritchie在贝尔实验室开发的通用编程语言。"
             "C语言高效灵活，是许多现代语言的基础。"),
]


def test_similarity():
    print("\n[相似度算法]")
    from similarity import SimilarityCalculator, METRIC_KEYS, METRICS_VERSION

    calc = SimilarityCalculator()
    students = [{"name": name, "text": text} for name, text in DEMO_ANSWERS]
    results = calc.batch_calculate(DEMO_STANDARD, students)
    by_name = {r["student_name"]: r for r in results}

    check("结果按相似度降序", results[0]["weighted_score"] >= results[-1]["weighted_score"])
    check("排序后首位是照抄的那份", results[0]["student_name"] == "张三",
          f"实际={results[0]['student_name']}")

    check("照抄的判为高风险", by_name["张三"]["level"] == "high",
          f"{by_name['张三']['weighted_score']}")
    check("无关答案判为安全", by_name["李四"]["level"] == "safe",
          f"{by_name['李四']['weighted_score']}")
    check("另一份无关答案判为安全", by_name["赵六"]["level"] == "safe",
          f"{by_name['赵六']['weighted_score']}")
    check("改写过的介于两者之间",
          by_name["李四"]["weighted_score"] < by_name["王五"]["weighted_score"] < by_name["张三"]["weighted_score"],
          f"李四={by_name['李四']['weighted_score']} 王五={by_name['王五']['weighted_score']}")

    check("六项指标齐全",
          all(key in by_name["张三"] for key in METRIC_KEYS),
          f"缺={[k for k in METRIC_KEYS if k not in by_name['张三']]}")
    check("带指标口径版本", all(r["metrics_version"] == METRICS_VERSION for r in results))

    # 旧公式的硬伤：无关文本里零散的单字/双字巧合会被算进「精确匹配」
    unrelated_a = "光合作用是植物利用光能合成有机物的过程"
    unrelated_b = "动物细胞的结构包括细胞膜细胞质和细胞核"
    pair = calc.calculate(unrelated_a, unrelated_b)
    check("无关同长文本不虚高", pair["exact_match"] < 0.15 and pair["level"] == "safe",
          f"exact={pair['exact_match']} level={pair['level']}")

    # 只抄一半：既不该溜过去，也不该被判成整段照抄
    half = calc.calculate(
        "光合作用是植物利用光能合成有机物的过程需要叶绿素参与",
        "光合作用是植物利用光能合成有机物的过程今天天气不错适合出门散步")
    check("只抄一半不判安全", half["level"] != "safe",
          f"score={half['weighted_score']} level={half['level']}")
    check("只抄一半不判整段照抄", half["level"] != "high",
          f"score={half['weighted_score']}")
    check("只抄一半有片段证据", len(half["fragments"]) >= 1, f"len={len(half['fragments'])}")
    check("只抄一半覆盖度偏低", half["coverage"] < 1.0, f"cov={half['coverage']}")

    # 改标点不该躲过检测
    punctuated = calc.calculate("光合作用是植物利用光能合成有机物的过程",
                                "光合作用，是植物利用光能合成有机物的过程！")
    check("改标点后仍判高风险", punctuated["level"] == "high" and punctuated["coverage"] == 1.0,
          f"score={punctuated['weighted_score']} cov={punctuated['coverage']}")

    # 证据片段能圈回原文
    frags = by_name["张三"]["fragments"]
    check("照抄的那份带雷同片段", len(frags) >= 1, f"len={len(frags)}")
    if frags:
        from text_utils import norm_only
        f = frags[0]
        a_slice = by_name["张三"]["student_answer"][f["a_start"]:f["a_end"]]
        b_slice = DEMO_STANDARD[f["b_start"]:f["b_end"]]
        check("片段 A 侧可回溯原文", norm_only(a_slice) == f["text"], repr(a_slice))
        check("片段 B 侧可回溯原文", norm_only(b_slice) == f["text"], repr(b_slice))


def test_calibration():
    print("\n[小样本校准]")
    from similarity import SimilarityCalculator
    from config import CALIBRATION_CONFIG

    calc = SimilarityCalculator()

    # 两字完全相同：字面 100% 一致，但不能判高风险
    tiny = calc.calculate("不会", "不会")
    check("两字相同不判高风险", tiny["level"] != "high", f"score={tiny['weighted_score']}")
    check("两字相同标记低置信", tiny["low_confidence"] is True)
    check("两字相同时保留原始分", tiny["raw_score"] > tiny["weighted_score"],
          f"raw={tiny['raw_score']} score={tiny['weighted_score']}")

    # 中等长度：仍受低置信度封顶，但已进入中风险
    mid_text = "植物的光合作用过程"
    mid = calc.calculate(mid_text, mid_text)
    check("中等短答案封顶不为高风险", mid["level"] != "high", f"score={mid['weighted_score']}")
    check("中等短答案已进中风险", mid["weighted_score"] >= 0.5, f"score={mid['weighted_score']}")

    # 足够长：不再收缩
    long_text = DEMO_STANDARD
    long_result = calc.calculate(long_text, long_text)
    check("长文本满分", close(long_result["weighted_score"], 1.0, 1e-3),
          f"score={long_result['weighted_score']}")
    check("长文本置信度为 1", close(long_result["confidence"], 1.0, 1e-6))
    check("长文本不标记低置信", long_result["low_confidence"] is False)

    # 校准可关闭
    off = SimilarityCalculator(calibration=dict(CALIBRATION_CONFIG, enabled=False))
    off_result = off.calculate("不会", "不会")
    check("关闭校准后不收缩",
          close(off_result["weighted_score"], off_result["raw_score"], 1e-6),
          f"{off_result['weighted_score']} vs {off_result['raw_score']}")

    # 空文本
    blank = calc.calculate("", "有内容")
    check("空文本零分安全", blank["weighted_score"] == 0.0 and blank["level"] == "safe")


def test_weights():
    print("\n[权重与统计]")
    from similarity import SimilarityCalculator, METRIC_KEYS
    from config import WEIGHT_PROFILES

    calc = SimilarityCalculator()
    resolved = calc.weights_for("short_answer")
    check("权重表补齐到六项", set(resolved) == set(METRIC_KEYS), f"{sorted(resolved)}")
    check("预设权重非负", all(v >= 0 for v in resolved.values()))

    # 旧格式（只有三项）不应崩，缺失项从预设补齐
    legacy = SimilarityCalculator(weights={
        "exact_match": 0.4, "sequence_similarity": 0.35, "keyword_overlap": 0.25})
    legacy_weights = legacy.weights_for("short_answer")
    check("旧三项权重可兼容", set(legacy_weights) == set(METRIC_KEYS))
    check("旧权重中缺失项有补默认值", legacy_weights["char_gram_jaccard"] > 0)

    # 权重自归一化：整表翻倍不应改变分数
    doubled = SimilarityCalculator(weights={k: v * 2 for k, v in
                                            WEIGHT_PROFILES["short_answer"]["weights"].items()})
    base_score = calc.calculate(DEMO_STANDARD, DEMO_ANSWERS[0][1])["weighted_score"]
    doubled_score = doubled.calculate(DEMO_STANDARD, DEMO_ANSWERS[0][1])["weighted_score"]
    check("权重自归一化", close(base_score, doubled_score, 1e-6),
          f"{base_score} vs {doubled_score}")

    stats = calc.get_statistics(calc.batch_calculate(
        DEMO_STANDARD, [{"name": n, "text": t} for n, t in DEMO_ANSWERS]))
    for key in ("count", "avg_score", "max_score", "min_score", "high_count",
                "medium_count", "low_count", "safe_count", "plagiarism_rate"):
        check(f"统计保留旧键 {key}", key in stats)
    check("统计新增低置信计数", stats["low_confidence_count"] == 0)
    check("统计新增平均长度", stats["min_len_avg"] > 0)


# ==================== S3 数据流与兼容 ====================

def test_data_flow():
    print("\n[数据流与历史兼容]")
    from app import attach_photos, generate_detailed_report
    from similarity import normalize_legacy_result
    from config import SIMILARITY_THRESHOLDS

    # 照片按份数挂载：同名学生不能串档
    students = [
        {"name": "学生1", "text": "a", "photos": ["/uploads/a.jpg"]},
        {"name": "学生1", "text": "b", "photos": ["/uploads/b.jpg"]},
    ]
    result = {"results": [
        {"index": 1, "student_name": "学生1"},
        {"index": 0, "student_name": "学生1"},
    ]}
    attach_photos(result, students)
    check("重名学生照片不串档(第2份)", result["results"][0]["photos"] == ["/uploads/b.jpg"],
          f"{result['results'][0]['photos']}")
    check("重名学生照片不串档(第1份)", result["results"][1]["photos"] == ["/uploads/a.jpg"],
          f"{result['results'][1]['photos']}")

    # 老格式历史记录
    legacy = {"exact_match": 0.9286, "sequence_similarity": 0.9286,
              "keyword_overlap": 0.8, "weighted_score": 0.8964, "level": "high",
              "student_name": "张三"}
    normalized = normalize_legacy_result(legacy)
    check("旧记录保留原始数值", normalized["exact_match"] == 0.9286)
    check("旧记录标记为旧版口径", normalized["metrics_version"] == 0)
    check("旧记录补齐新指标键",
          normalized["char_gram_jaccard"] == 0.0 and normalized["coverage"] == 0.0)
    check("旧记录补齐片段字段", normalized["fragments"] == [])
    check("旧记录保留等级", normalized["level"] == "high")

    filled = normalize_legacy_result({"weighted_score": 0.62})
    check("缺等级时按阈值回推", filled["level"] == "medium", filled["level"])

    # 报告文案跟随配置阈值，不再写死
    original = dict(SIMILARITY_THRESHOLDS)
    try:
        SIMILARITY_THRESHOLDS["high"] = 0.7
        SIMILARITY_THRESHOLDS["medium"] = 0.45
        report = generate_detailed_report([], {"count": 0}, "", "2026-01-01T00:00:00")
        check("报告高风险阈值跟随配置", "高风险(≥70%)" in report)
        check("报告中风险区间跟随配置", "中风险(45-70%)" in report)
    finally:
        SIMILARITY_THRESHOLDS.clear()
        SIMILARITY_THRESHOLDS.update(original)

    # CSV 导出
    from app import generate_csv_report
    csv_text = generate_csv_report([{
        "student_name": "张三", "weighted_score": 0.812, "level": "high",
        "exact_match": 0.878, "sequence_similarity": 0.921, "keyword_overlap": 0.81,
        "char_gram_jaccard": 0.691, "char_gram_cosine": 0.853, "coverage": 0.794,
        "fragments": [{"len": 20}], "student_answer": "答案内容",
    }])
    check("CSV 带 BOM", csv_text.startswith("\ufeff"))
    check("CSV 含表头与学生行", "学生姓名" in csv_text and "张三" in csv_text)


# ==================== S4 设置持久化 ====================

def test_settings():
    print("\n[设置持久化]")
    import os
    from settings_store import (SETTINGS_FILE, defaults, sanitize, load_settings,
                                save_settings, apply_to_calculator, profiles)
    from similarity import SimilarityCalculator

    base = defaults()
    check("默认设置含各分区",
          all(key in base for key in ("profile", "weights", "thresholds",
                                      "calibration", "fragments", "normalize",
                                      "cluster_margin")))
    check("默认档位是短答题", base["profile"] == "short_answer")
    check("预设档位可枚举", "long_text" in profiles() and "pairwise" in profiles())

    dirty = sanitize({
        "weights": {"exact_match": -5, "keyword_overlap": 99},
        "thresholds": {"high": 0.3, "medium": 0.5, "low": 0.7},
        "cluster_margin": 99,
        "calibration": {"floor": 5},
    })
    check("越界权重被夹取",
          dirty["weights"]["exact_match"] == 0.0 and dirty["weights"]["keyword_overlap"] == 1.0,
          f"exact={dirty['weights']['exact_match']} kw={dirty['weights']['keyword_overlap']}")
    check("倒挂阈值退回默认", dirty["thresholds"] == base["thresholds"])
    check("越界聚类阈值被夹取", dirty["cluster_margin"] == 0.5, dirty["cluster_margin"])
    check("越界校准系数被夹取", dirty["calibration"]["floor"] == 0.95)
    check("非字典输入不抛异常", isinstance(sanitize("不是字典"), dict))
    check("缺失字段不抛异常", isinstance(sanitize({"thresholds": None}), dict))

    long_text = sanitize({"profile": "long_text"})
    check("选预设即整体切换权重", long_text["weights"]["sequence_similarity"] == 0.25)
    check("选预设后记录档位名", long_text["profile"] == "long_text")
    check("只改权重即标记自定义", sanitize({"weights": {"exact_match": 0.9}})["profile"] == "custom")

    # 落盘 → 重新加载（跑完恢复原状，不污染仓库）
    existed = os.path.exists(SETTINGS_FILE)
    backup = None
    if existed:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            backup = f.read()
    try:
        saved = save_settings({"profile": "long_text",
                               "thresholds": {"high": 0.7, "medium": 0.45, "low": 0.25}})
        check("保存返回生效值", saved["thresholds"]["high"] == 0.7)
        check("设置已落盘", os.path.exists(SETTINGS_FILE))

        loaded = load_settings()
        check("重启后阈值保留", loaded["thresholds"]["high"] == 0.7)
        check("重启后档位保留", loaded["profile"] == "long_text")

        calc = SimilarityCalculator()
        apply_to_calculator(calc, loaded)
        check("设置可应用到计算器",
              calc.thresholds["high"] == 0.7 and calc.profile == "long_text")

        from detection_engine import DetectionEngine
        engine = DetectionEngine()
        check("引擎构造即加载设置", engine.calculator.thresholds["high"] == 0.7)
        check("报警器阈值同步", engine.alerter.thresholds["high"] == 0.7)
    finally:
        if backup is not None:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                f.write(backup)
        elif os.path.exists(SETTINGS_FILE):
            os.remove(SETTINGS_FILE)


# ==================== S5 学生互查 ====================

def _unique_answers(count, length=40):
    """造一批互不相同的答案，用来验证聚类不会被噪声误连。"""
    answers = []
    for i in range(count):
        text = "".join(chr(0x4E00 + (i * 37 + k * 13) % 900) for k in range(length))
        answers.append(text)
    return answers


def test_pairwise():
    print("\n[学生互查]")
    import time
    from similarity import SimilarityCalculator

    calc = SimilarityCalculator()

    base = ("光合作用是植物利用光能合成有机物的过程，需要叶绿素吸收光能，"
            "把二氧化碳和水转化成有机物并释放氧气")
    variants = [
        base,
        base.replace("释放氧气", "放出氧气"),
        base.replace("需要叶绿素", "要有叶绿素"),
        base + "。",
        base.replace("二氧化碳", "二氧化碳气体")[:len(base) + 4],
    ]
    others = _unique_answers(45)
    students = ([{"name": f"抄{i + 1}", "text": text} for i, text in enumerate(variants)]
                + [{"name": f"独立{i + 1}", "text": text} for i, text in enumerate(others)])

    started = time.time()
    data = calc.pairwise_calculate(students)
    elapsed = time.time() - started
    print(f"        （50 人互查耗时 {elapsed:.2f}s，{data['pair_count']} 对）")

    check("矩阵规模正确", len(data["matrix"]) == 50 and len(data["matrix"][0]) == 50)
    check("对角线为 1", all(close(data["matrix"][i][i], 1.0) for i in range(50)))
    check("矩阵对称",
          all(close(data["matrix"][i][j], data["matrix"][j][i])
              for i in range(0, 50, 7) for j in range(0, 50, 5)))
    check("对数统计完整", data["pair_count"] == 50 * 49 // 2, data["pair_count"])
    check("返回的对经过截断", len(data["pairs"]) <= 50)

    gangs = [g for g in data["groups"] if g["kind"] == "gang"]
    check("聚出 1 个团伙", len(gangs) == 1, f"实际 {len(gangs)} 个")
    if gangs:
        members = set(gangs[0]["member_names"])
        expected = {f"抄{i + 1}" for i in range(5)}
        check("团伙成员正是那 5 份近似答案", members == expected,
              f"多出={sorted(members - expected)} 缺失={sorted(expected - members)}")
        check("团伙内部最低分也偏高", gangs[0]["min_internal"] >= 0.5,
              gangs[0]["min_internal"])

    check("无关同学不结伙",
          all("独立" not in g["member_names"] for g in data["groups"]),
          f"被误连={[g['member_names'] for g in data['groups'] if any('独立' in n for n in g['member_names'])]}")

    per_student = data["per_student"]
    check("按最像的一份降序",
          per_student[0]["max_score"] >= per_student[-1]["max_score"])
    top_names = {s["name"] for s in per_student[:5]}
    check("最可疑的前 5 位是那 5 份", top_names == {f"抄{i + 1}" for i in range(5)}, top_names)
    check("指认了最像的同伴", all(s["partner"] for s in per_student[:5]))

    stats = data["statistics"]
    check("统计以对为单位", stats["pair_count"] == data["pair_count"])
    check("统计含团伙数", stats["group_count"] == 1, stats["group_count"])
    check("统计含最大团伙规模", stats["largest_group"] == 5, stats["largest_group"])
    check("统计含人数", stats["count"] == 50 and stats["matrix_size"] == 50)

    # 两人互抄：应识别为「疑似对」并且给出证据片段
    two = calc.pairwise_calculate([
        {"name": "甲", "text": base},
        {"name": "乙", "text": base.replace("释放氧气", "放出氧气")},
    ])
    check("两人互抄形成一组", len(two["groups"]) == 1, f"{len(two['groups'])}")
    if two["groups"]:
        check("两人组标记为疑似对", two["groups"][0]["kind"] == "pair")
        check("疑似对带证据片段",
              bool(two["groups"][0]["evidence"]["fragment"]),
              two["groups"][0]["evidence"])
    check("片段只在前若干对定位",
          two["pairs"][0]["fragments"] and all(
              not p["fragments"] for p in two["pairs"][31:]),
          "应为前 30 对之外无片段")

    # 少于两人无法互查
    from detection_engine import DetectionEngine
    engine = DetectionEngine()
    empty = engine.calculator.pairwise_calculate([])
    check("空输入不炸", empty["pair_count"] == 0 and empty["groups"] == []
          and empty["statistics"]["count"] == 0)


# ==================== S6 整卷多题 ====================

PAPER_QUESTIONS = [
    {"id": "Q1", "stem": "光合作用", "full_score": 10,
     "standard_answer": "光合作用是植物利用光能合成有机物的过程需要叶绿素参与"},
    {"id": "Q2", "stem": "牛顿第一定律", "full_score": 20,
     "standard_answer": "牛顿第一定律说明物体在不受外力作用时保持匀速直线运动状态"},
    {"id": "Q3", "stem": "水的组成", "full_score": 10,
     "standard_answer": "水的化学式是H2O由两个氢原子和一个氧原子组成是一种化合物"},
]


def test_paper():
    print("\n[整卷多题]")
    from detection_engine import DetectionEngine

    engine = DetectionEngine()
    questions = [dict(q) for q in PAPER_QUESTIONS]

    students = [
        {"name": "抄子", "answers": {
            "Q1": questions[0]["standard_answer"],
            "Q2": questions[1]["standard_answer"],
            "Q3": "太阳系一共有八颗行星按照离太阳由近到远依次排列在宇宙中",
        }},
        {"name": "独立", "answers": {
            "Q1": "树木在阳光照射下会慢慢长高并且把空气中的成分固定下来",
            "Q2": "如果一个物体受到的合力为零那它就会一直保持原来的运动状态",
            "Q3": "食盐溶解在水里之后可以通过加热蒸发的方式重新得到固体颗粒",
        }},
        {"name": "缺答", "answers": {
            "Q1": "叶片里的绿色物质能够把太阳能变成化学能储存起来供植物使用",
            "Q2": "",
            "Q3": "分子由原子构成原子又可以分成更小的微粒存在于物质之中",
        }},
    ]

    result = engine.detect_paper({"name": "第3单元测验", "questions": questions},
                                 students)
    check("整卷检测成功", result.get("success") is True, result.get("error"))
    check("返回每题结果", len(result["per_question"]) == 3)
    check("逐题返回学生结果", all(len(q["results"]) >= 2 for q in result["per_question"]))

    rollup = {r["name"]: r for r in result["rollup"]}
    check("整卷指数最高的仍是照抄者",
          result["rollup"][0]["name"] == "抄子", result["rollup"][0]["name"])
    check("照抄整卷指数高于独立作答者",
          rollup["抄子"]["paper_score"] > rollup["独立"]["paper_score"],
          f"抄子={rollup['抄子']['paper_score']} 独立={rollup['独立']['paper_score']}")
    check("逐字照抄的题拿到满分",
          close(rollup["抄子"]["per_question"]["Q1"], 1.0, 1e-3),
          rollup["抄子"]["per_question"]["Q1"])
    check("列出高度雷同的题目",
          rollup["抄子"]["high_risk_questions"] == ["Q1", "Q2"],
          rollup["抄子"]["high_risk_questions"])
    check("雷同题目占的分值正确",
          close(rollup["抄子"]["high_risk_score"], 30.0, 1e-6),
          rollup["抄子"]["high_risk_score"])
    check("独立作答者无雷同题",
          rollup["独立"]["high_risk_questions"] == [],
          rollup["独立"]["high_risk_questions"])

    check("缺答计入 missing_count", rollup["缺答"]["missing_count"] == 1,
          rollup["缺答"]["missing_count"])
    check("缺答的题目从分母剔除", close(rollup["缺答"]["answered_full_score"], 20.0, 1e-6),
          rollup["缺答"]["answered_full_score"])
    check("完整作答者分母为总分", close(rollup["抄子"]["answered_full_score"], 40.0, 1e-6),
          rollup["抄子"]["answered_full_score"])
    check("指出最可疑的题", rollup["抄子"]["weakest_question"] in ("Q1", "Q2"),
          rollup["抄子"]["weakest_question"])

    stats = result["statistics"]
    check("统计含题目数", stats["question_count"] == 3)
    check("统计指出全班最可疑的题",
          stats["weakest_question"] in ("Q1", "Q2"), stats["weakest_question"])
    check("统计缺答总数", stats["missing_total"] == 1, stats["missing_total"])

    # 题号重复时自动去重，避免按题汇总串行
    dup = engine.detect_paper({"name": "重复题号", "questions": [
        {"id": "Q1", "standard_answer": "第一题的参考答案内容", "full_score": 5},
        {"id": "Q1", "standard_answer": "第二题的参考答案内容", "full_score": 5},
    ]}, [{"name": "甲", "answers": {"Q1": "第一题的参考答案内容"}},
         {"name": "乙", "answers": {"Q1": "完全不一样的另外一段作答"}}])
    check("重复题号被去重", len({q["qid"] for q in dup["per_question"]}) == 2,
          [q["qid"] for q in dup["per_question"]])

    # 规模上限
    big = engine.detect_paper(
        {"name": "超量", "questions": [{"id": f"Q{i}", "standard_answer": "x"} for i in range(20)]},
        [{"name": f"学生{i}", "answers": {}} for i in range(30)])
    check("超出格子上限被拒绝", big["success"] is False, big.get("error"))

    # 空输入
    check("无题目被拒绝",
          engine.detect_paper({"questions": []}, students)["success"] is False)
    check("无学生被拒绝",
          engine.detect_paper({"questions": questions}, [])["success"] is False)


# ==================== S9 路由与前端静态一致性 ====================

TEXT_PAYLOAD = {
    "standard_answer": "光合作用是植物利用光能合成有机物的过程，需要叶绿素参与，"
                       "把二氧化碳和水转化成有机物并释放氧气",
    "students": [
        {"name": "甲", "text": "光合作用是植物利用光能合成有机物的过程，需要叶绿素参与，"
                               "把二氧化碳和水转化成有机物并释放氧气"},
        {"name": "乙", "text": "植物能够把太阳能固定下来变成化学能存储在身体里面供自己使用"},
    ],
}

PAIRWISE_PAYLOAD = {
    "students": [
        {"name": "甲", "text": "光合作用是植物利用光能合成有机物的过程，需要叶绿素参与其过程"},
        {"name": "乙", "text": "光合作用是植物利用光能合成有机物的过程，需要叶绿素参与其步骤"},
        {"name": "丙", "text": "太阳系一共有八颗行星按照离太阳由近到远依次排列在宇宙之中"},
    ],
}

PAPER_PAYLOAD = {
    "assignment": {"name": "单元测验", "questions": [
        {"id": "Q1", "full_score": 10,
         "standard_answer": "光合作用是植物利用光能合成有机物的过程需要叶绿素参与其中"},
        {"id": "Q2", "full_score": 20,
         "standard_answer": "牛顿第一定律说明物体在不受外力作用时保持匀速直线运动状态"},
    ]},
    "students": [
        {"name": "甲", "answers": {
            "Q1": "光合作用是植物利用光能合成有机物的过程需要叶绿素参与其中",
            "Q2": "牛顿第一定律说明物体在不受外力作用时保持匀速直线运动状态"}},
        {"name": "乙", "answers": {
            "Q1": "叶片里的绿色物质能把太阳能转成化学能储存起来供植物自己使用",
            "Q2": "物体如果不受力就会一直保持原来的运动状态不会自己停下来"}},
    ],
}


def test_routes():
    print("\n[HTTP 路由]")
    import json
    import os
    import tempfile
    import app as app_module
    import settings_store
    import llm_client
    from app import app as flask_app, engine

    client = flask_app.test_client()

    # 把历史、设置、密钥、用量、缓存、导出报告都指到临时文件，测试绝不碰用户真实数据
    temp_dir = tempfile.mkdtemp(prefix="anticopy_test_")
    original_history_file = app_module.HISTORY_FILE
    original_settings_file = settings_store.SETTINGS_FILE
    original_upload_dir = flask_app.config["UPLOAD_FOLDER"]
    original_files = (llm_client.SECRETS_FILE, llm_client.USAGE_FILE, llm_client.CACHE_FILE)
    original_env_key = os.environ.pop("ANTICOPY_LLM_API_KEY", None)
    app_module.HISTORY_FILE = os.path.join(temp_dir, "history.json")
    settings_store.SETTINGS_FILE = os.path.join(temp_dir, "settings.json")
    llm_client.SECRETS_FILE = os.path.join(temp_dir, "secrets.json")
    llm_client.USAGE_FILE = os.path.join(temp_dir, "usage.json")
    llm_client.CACHE_FILE = os.path.join(temp_dir, "cache.json")
    # 导出会真的落盘，不指到临时目录就会在 uploads/ 里留下一堆测试报告
    export_dir = os.path.join(temp_dir, "exports")
    os.makedirs(export_dir, exist_ok=True)
    flask_app.config["UPLOAD_FOLDER"] = export_dir

    try:
        # 首页与静态资源
        page = client.get("/")
        check("首页可访问", page.status_code == 200)
        html = page.get_data(as_text=True)
        check("首页含四种检测模式",
              all(mode in html for mode in
                  ('data-mode="text"', 'data-mode="pairwise"', 'data-mode="paper"',
                   'data-mode="aigc"')))
        check("已下线的搜题模式不再出现在界面",
              'data-mode="image"' not in html and 'data-mode="api"' not in html)
        check("静态样式可取", client.get("/static/css/style.css").status_code == 200)
        check("静态脚本可取", client.get("/static/js/app.js").status_code == 200)
        check("不再引用远程字体", "fonts.googleapis.com" not in html)

        # 文本检测
        resp = client.post("/api/detect/text", json=TEXT_PAYLOAD)
        data = resp.get_json()
        check("文本检测返回成功", resp.status_code == 200 and data.get("success"))
        check("文本检测带指标版本",
              data["results"][0].get("metrics_version") == 2)
        check("文本检测带雷同片段", len(data["results"][0].get("fragments", [])) >= 1)

        # 互查
        resp = client.post("/api/detect/pairwise", json=PAIRWISE_PAYLOAD)
        data = resp.get_json()
        check("互查返回成功", resp.status_code == 200 and data.get("success"))
        check("互查返回模式标记", data.get("mode") == "pairwise")
        check("互查返回矩阵", len(data.get("matrix", [])) == 3)
        check("互查返回团伙字段", "groups" in data and "per_student" in data)
        check("互查学生带上答案原文",
              all("text" in s for s in data.get("students", [])))
        check("互查识别出疑似对",
              any(g["kind"] == "pair" for g in data["groups"]), data["groups"])

        resp = client.post("/api/detect/pairwise", json={"students": [
            {"name": "只有一个", "text": "孤零零的一份答案"}]})
        check("互查不足两人被拒", resp.status_code == 400)

        # 整卷
        resp = client.post("/api/detect/paper", json=PAPER_PAYLOAD)
        data = resp.get_json()
        check("整卷返回成功", resp.status_code == 200 and data.get("success"))
        check("整卷返回逐题结果", len(data.get("per_question", [])) == 2)
        check("整卷返回汇总并带平均指标",
              data["rollup"] and "avg_metrics" in data["rollup"][0])
        check("整卷指出雷同题",
              data["rollup"][0]["high_risk_questions"] == ["Q1", "Q2"],
              data["rollup"][0]["high_risk_questions"])

        # AIGC 风格线索（纯本地，不需要任何接口）
        resp = client.post("/api/detect/aigc", json={"text": AI_STYLE_ESSAY,
                                                     "student_name": "甲"})
        data = resp.get_json()
        check("AIGC 单篇返回成功", resp.status_code == 200 and data.get("success"))
        check("AIGC 返回模式标记", data.get("mode") == "aigc")
        check("AIGC 单篇结果在 results 里", len(data.get("results", [])) == 1)
        first = data["results"][0]
        check("AIGC 结果带线索强度与信号",
              "ai_score" in first and "signals" in first and "evidence" in first)
        check("AIGC 结果带检测器版本", data.get("detector_version") == 1)
        check("AIGC 给出可解释信号",
              all(s.get("reading") for s in data["results"][0]["signals"] if s["applicable"]))
        check("AIGC 证据下标能取回原句",
              all(AI_STYLE_ESSAY[e["start"]:e["end"]] == e["text"]
                  for e in data["results"][0]["evidence"]))
        check("AIGC 未勾选外部时不发请求", data["results"][0]["external"] is None)
        check("AIGC 结果声明不是判定", "不是判定" in data["results"][0]["notice"])

        resp = client.post("/api/detect/aigc", json={"essays": [
            {"name": "甲", "text": AI_STYLE_ESSAY},
            {"name": "乙", "text": STUDENT_STYLE_ESSAY},
        ]})
        data = resp.get_json()
        check("AIGC 批量返回两篇", resp.status_code == 200 and len(data["results"]) == 2)
        check("AIGC 批量按线索强度倒序",
              data["results"][0]["ai_score"] >= data["results"][1]["ai_score"])
        check("AIGC 批量先看到最可疑的那篇", data["results"][0]["name"] == "甲")
        check("AIGC 统计给出分档计数",
              data["statistics"]["strong_count"] == 1 and data["statistics"]["weak_count"] == 1,
              data["statistics"])

        resp = client.post("/api/detect/aigc", json={"essays": [
            {"name": "太短", "text": "诚信很重要。我们要讲诚信。"}]})
        data = resp.get_json()
        check("AIGC 过短篇目被拒绝并说明原因",
              data["success"] and data["results"][0]["error_code"] == "too_short"
              and data["statistics"]["rejected"] == 1, data["results"][0])
        check("AIGC 空内容被拒",
              client.post("/api/detect/aigc", json={"text": "   "}).status_code == 400)
        check("AIGC 超量被拒",
              client.post("/api/detect/aigc", json={
                  "essays": [{"name": f"作{i}", "text": AI_STYLE_ESSAY}
                             for i in range(60)]}).status_code == 400)

        # 参考答案生成：未配置接口时必须干净降级，并告诉老师下一步做什么
        resp = client.post("/api/answer/generate", json={"question": "光合作用的场所是哪里"})
        data = resp.get_json()
        check("未配置接口时生成失败但不报错状态码",
              resp.status_code == 200 and data.get("success") is False)
        check("失败回错误码与下一步提示",
              data.get("error_code") in ("disabled", "not_configured", "no_key")
              and "手动粘贴" in data.get("hint", ""), data.get("error_code"))
        check("空题目被拒",
              client.post("/api/answer/generate", json={"question": ""}).status_code == 400)

        # 参数校验
        check("文本检测缺标准答案被拒",
              client.post("/api/detect/text", json={"students": []}).status_code == 400)
        check("空请求体被拒",
              client.post("/api/detect/text", json={}).status_code == 400)

        # 设置读写
        resp = client.get("/api/settings")
        data = resp.get_json()
        check("设置读取成功且含预设", data.get("success") and "profiles" in data)
        resp = client.post("/api/settings", json={"thresholds": {"high": 0.75}})
        check("设置保存成功", resp.status_code == 200 and resp.get_json().get("success"))
        check("保存后阈值即时生效",
              engine.calculator.thresholds["high"] == 0.75,
              engine.calculator.thresholds["high"])
        client.post("/api/settings", json={"thresholds": {"high": 0.8, "medium": 0.5}})
        check("阈值已复原", engine.calculator.thresholds["high"] == 0.8)

        # 密钥：只写不回显，且不进 settings.json
        data = client.get("/api/settings").get_json()
        check("设置里没有密钥原文", "sk-" not in json.dumps(data))
        check("设置含大模型状态与 AIGC 参数",
              isinstance(data.get("llm"), dict) and isinstance(data.get("aigc"), dict))
        check("未配置密钥时 has_key 为假", data["llm"]["has_key"] is False)

        resp = client.post("/api/settings", json={
            "api_key": "sk-test-abcdef123456", "clear_cache": False,
            "llm": {"base_url": "https://api.example.com/v1", "model": "some-model",
                    "enabled": True, "daily_budget": 0.5, "timeout": 5}})
        data = resp.get_json()
        check("写入密钥后响应不含原文", "sk-test-abcdef123456" not in json.dumps(data))
        check("写入密钥后只回脱敏形式",
              data["llm"]["has_key"] is True and data["llm"]["key_masked"] == "sk-t...56",
              data["llm"]["key_masked"])
        check("密钥落在 secrets.json 而不是 settings.json",
              os.path.exists(llm_client.SECRETS_FILE)
              and "sk-test-abcdef123456" not in
              open(settings_store.SETTINGS_FILE, encoding="utf-8").read())
        check("配置预算已生效", data["llm"]["daily_budget"] == 0.5
              and engine.llm.model == "some-model")
        check("预算余额按设置回报", data["llm"]["budget_remaining"] == 0.5)

        # 有密钥但接口不可达：测试连接应给出可操作的错误，而不是崩
        resp = client.post("/api/llm/test", json={})
        data = resp.get_json()
        check("接口测试失败时返回错误说明",
              resp.status_code == 200 and data.get("success") is False
              and data.get("error_code") in ("unreachable", "timeout", "http_404",
                                             "http_4xx", "bad_response", "http_5xx"),
              data.get("error_code"))
        check("有密钥后参考答案接口不再报未配置",
              client.post("/api/answer/generate",
                          json={"question": "光合作用的场所"}).get_json().get("error_code")
              not in ("not_configured", "no_key"))

        resp = client.post("/api/settings", json={"api_key": ""})
        check("空字符串清除密钥", resp.get_json()["llm"]["has_key"] is False)
        check("清除后不再回显任何密钥", "sk-t" not in json.dumps(resp.get_json()))

        # AIGC 参数可保存并即时生效（strong 必须高于 medium，否则整体退回出厂）
        resp = client.post("/api/settings", json={"aigc": {"levels": {"strong": 0.55}}})
        check("AIGC 阈值保存后检测器即刻采用",
              engine.aigc.levels["strong"] == 0.55, engine.aigc.levels)
        client.post("/api/settings", json={"aigc": {"levels": {"strong": 0.6, "medium": 0.4}}})
        check("AIGC 阈值复原", engine.aigc.levels["strong"] == 0.6)
        check("倒挂的 AIGC 档位被退回出厂",
              client.post("/api/settings",
                          json={"aigc": {"levels": {"strong": 0.1, "medium": 0.9}}}
                          ).get_json()["aigc"]["levels"]["strong"] == 0.6)

        # 导出
        resp = client.post("/api/export", json={
            "results": TEXT_PAYLOAD and client.post(
                "/api/detect/text", json=TEXT_PAYLOAD).get_json()["results"],
            "statistics": {}, "standard_answer": "x", "mode": "text", "format": "txt"})
        check("导出文本报告", resp.status_code == 200
              and "作业抄袭检测报告" in resp.get_data(as_text=True))
        resp = client.post("/api/export", json={
            "results": [{"student_name": "甲", "weighted_score": 0.9, "level": "high"}],
            "mode": "text", "format": "csv"})
        check("导出 CSV", resp.status_code == 200
              and "学生姓名" in resp.get_data(as_text=True))
        check("导出无数据被拒",
              client.post("/api/export", json={"results": []}).status_code == 400)

        # 历史
        resp = client.get("/api/history")
        data = resp.get_json()
        check("历史接口可用", data.get("success") is True)
        check("历史里能看到刚写入的记录", len(data.get("history", [])) >= 1)
        latest = data["history"][0]
        check("历史记录带模式标记", latest.get("mode") in
              ("text", "pairwise", "paper"), latest.get("mode"))

        detail = client.get(f"/api/history/{latest['id']}").get_json()
        check("历史详情可读", detail.get("success") is True)
        check("历史详情结果已补全形状",
              all("metrics_version" in r for r in detail["detail"]["results"]))
        check("不存在的历史返回 404",
              client.get("/api/history/999999").status_code == 404)

        # 学生答案文件上传
        import io as _io
        upload = client.post("/api/upload/students", data={
            "file": (_io.BytesIO("张三,光合作用把光能变成化学能\n李四,牛顿第一定律".encode("utf-8")),
                     "answers.txt")
        }, content_type="multipart/form-data")
        data = upload.get_json()
        check("文本文件导入成功", data.get("success") and data.get("count") == 2)
        check("导入保留姓名", data["students"][0]["name"] == "张三")
    finally:
        app_module.HISTORY_FILE = original_history_file
        settings_store.SETTINGS_FILE = original_settings_file
        flask_app.config["UPLOAD_FOLDER"] = original_upload_dir
        (llm_client.SECRETS_FILE, llm_client.USAGE_FILE,
         llm_client.CACHE_FILE) = original_files
        if original_env_key:
            os.environ["ANTICOPY_LLM_API_KEY"] = original_env_key
        # 引擎里残留的临时配置清掉，避免影响后续用例
        engine.apply_settings(settings_store.load_settings())
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_dom_ids():
    """静态校验：app.js 里 getElementById 的每个 id 都要在模板里存在。

    这类笔误只会在浏览器里以 TypeError 的形式炸掉，跑一遍就能提前发现。
    """
    print("\n[前端 id 一致性]")
    import os
    import re

    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "templates", "index.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(base, "static", "js", "app.js"), encoding="utf-8") as f:
        js = f.read()

    html_ids = set(re.findall(r'id="([^"]+)"', html))
    js_ids = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", js))

    missing = sorted(js_ids - html_ids)
    check("脚本引用的 id 都存在于模板", not missing, f"缺失={missing}")

    # JS 里动态生成的新模式 id 也要能对上
    for required in ("paper-questions", "student-cards-paper", "matrix-grid",
                     "group-list", "rollup-list", "per-question-list",
                     "filter-chips", "result-search", "preset-select"):
        check(f"新增界面元素 {required}", required in html_ids)


def test_css_classes():
    """静态校验：模板与脚本里用到的 class 都要在样式表里有定义。

    漏定义不会报错，只会静默地样式失效——正是这种问题最难在代码里看出来。
    """
    print("\n[样式类一致性]")
    import os
    import re

    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "templates", "index.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(base, "static", "js", "app.js"), encoding="utf-8") as f:
        js = f.read()
    with open(os.path.join(base, "static", "css", "style.css"), encoding="utf-8") as f:
        css = f.read()

    # 去掉 lookbehind：`.btn.active` 这类复合选择器也要能取到 active
    css_classes = set(re.findall(r'\.([a-zA-Z][\w-]*)', css))

    check("样式表括号配对", css.count("{") == css.count("}"),
          f"{{={css.count('{')} }}={css.count('}')}")
    check("样式表包含 v3 新组件",
          all(sel in css for sel in (".matrix-grid", ".frag-hit", ".gang-card",
                                     ".result-toolbar", ".panel-section")))

    used = set()
    for pattern in (r'class="([^"]+)"', r"class='([^']+)'"):
        for match in re.findall(pattern, html + js):
            used.update(match.split())
    for match in re.findall(r'className\s*=\s*[`\'"]([^`\'"]+)', js):
        used.update(match.split())
    for match in re.findall(r'classList\.(?:add|remove|toggle)\(\s*[\'"]([^\'"]+)', js):
        used.update(match.split())

    # 只保留合法类名：模板字面量里的 ${...} 片段会被这里自然滤掉
    used = {name for name in used if re.fullmatch(r'[a-zA-Z][\w-]*', name)}

    # 运行时状态类由脚本动态加/减，这里不要求它们作为独立选择器出现
    runtime_state = {"active", "expanded", "flash", "filtered-out", "dragover"}

    missing = sorted(name for name in used - runtime_state if name not in css_classes)
    check("用到的样式类都有定义", not missing, f"未定义={missing}")


# ==================== A3 AIGC 风格线索 ====================

AI_STYLE_ESSAY = (
    "读书是一种提升自我认知的重要途径。在当今社会，知识的更新速度日新月异，阅读已经不再是"
    "单纯的兴趣爱好，而是每个人成长道路上不可或缺的一部分。首先，读书能够拓宽我们的视野，"
    "让我们在不同的文化与思想之间建立联系。其次，读书有助于培养独立思考的能力，使我们在纷繁"
    "复杂的信息中保持清醒的判断。此外，读书还能够涵养情操，让人的精神世界更加丰盈。"
    "综上所述，读书的意义不仅体现在知识的积累上，更体现在人格的塑造中。我们应当以书为友，"
    "在阅读中汲取智慧，在实践中砥砺前行，让人生因阅读而更加辽阔，让时代因阅读而更加充满生机。"
)

STUDENT_STYLE_ESSAY = (
    "我其实不太爱看书。真的。上次妈妈给我买了一本《朝花夕拾》，我才看了三页就睡着了！"
    "但是去年暑假不一样，那天停电，热得我根本没法出门，我翻到了抽屉里那本特别旧的《西游记》，"
    "是爷爷以前买的，封面都卷起来了。我看着看着就天黑了也没发现。孙悟空被压了五百年，"
    "他会不会也想找人聊聊天？我忽然有点懂他，因为我在教室里被换到最后一排的那个月，"
    "也特别想找人说句话。书大概就是这个用处吧，不是让人变聪明，是让你知道自己没疯。"
)

# 认真套用课堂模板的好学生：这是 AI 检测最容易误伤的一类，必须压在中等线以下
FORMULAIC_STUDENT_ESSAY = (
    "我的理想是当一名老师。因为我的语文老师对我们特别好。首先，老师上课讲得很清楚，"
    "复杂的题目他都会再写一遍。其次，他每天都很早到教室，我们就没有人迟到了。"
    "有一次我考砸了，他在操场上叫住我，问我是不是没睡好。我觉得老师特别厉害。"
    "最后，我希望自己以后也能这样对学生。所以我现在要把字写好，把计算练熟，"
    "不然以后站在讲台上，学生可能听不懂我在讲什么。这就是我的理想。"
)


class _FakeClient:
    """替身接口：记录送出去的提示词，返回固定分值。"""

    def __init__(self, reply=None):
        self.reply = reply
        self.model = "fake-model"
        self.prompts = []

    def chat_json(self, user_prompt, system_prompt="", use_cache=True, max_tokens=None):
        self.prompts.append((user_prompt, system_prompt))
        if self.reply is not None:
            return dict(self.reply)
        return {"success": True, "data": {"ai_score": 1.0, "reasons": ["依据一"],
                                          "uncertainty": ""},
                "from_cache": False, "cost": 0.01, "error": "", "error_code": ""}


def _signal_of(result, key):
    """从结果里取某项信号，便于断言写得更直白。"""
    return next(signal for signal in result["signals"] if signal["key"] == key)


def test_aigc():
    print("\n[AIGC 风格线索]")
    import re

    from aigc_detector import AIGCDetector, anonymize, split_sentences, _scale
    from config import AIGC_CONFIG

    detector = AIGCDetector()

    # ---------- 判别力 ----------
    ai = detector.analyze(AI_STYLE_ESSAY)
    kid = detector.analyze(STUDENT_STYLE_ESSAY)
    formula = detector.analyze(FORMULAIC_STUDENT_ESSAY)

    check("AI 腔判为强线索", ai["success"] and ai["level"] == "strong", f"score={ai['ai_score']}")
    check("学生口语判为弱线索", kid["success"] and kid["level"] == "weak", f"score={kid['ai_score']}")
    check("八股好学生不误判为中等以上",
          formula["success"] and formula["ai_score"] < AIGC_CONFIG["levels"]["medium"],
          f"score={formula['ai_score']}")
    check("AI 腔分数远高于学生习作", ai["ai_score"] - kid["ai_score"] > 0.4,
          f"差={ai['ai_score'] - kid['ai_score']:.3f}")
    # 实测事实：好学生写「首先/其次/最后」的密度比 AI 腔还高。
    # 所以套话密度不能单独定级，只能作为六分之一。若有人调高它的权重，
    # 下面这条「综合分仍能区分」会失败。
    ai_tpl = _signal_of(ai, "template_density")["tendency"]
    formula_tpl = _signal_of(formula, "template_density")["tendency"]
    check("套话密度会把好学生判得更重（所以它不能单独定级）",
          formula_tpl >= ai_tpl, f"ai={ai_tpl:.3f} formula={formula_tpl:.3f}")
    check("但综合分仍能区分两者", ai["ai_score"] > formula["ai_score"] + 0.2,
          f"ai={ai['ai_score']} formula={formula['ai_score']}")

    # ---------- 可解释性 ----------
    check("每项信号都有数值与解释",
          all(s["metric"] and s["reading"] for s in ai["signals"] if s["applicable"]))
    check("倾向值都在 0..1",
          all(0.0 <= s["tendency"] <= 1.0 for s in ai["signals"]))
    check("权重与出厂一致", detector.weights == AIGC_CONFIG["weights"])
    check("结论一律标注不是判定", "不是判定" in ai["notice"])
    check("信号按可疑程度倒序",
          [s["tendency"] for s in ai["signals"]] == sorted(
              [s["tendency"] for s in ai["signals"]], reverse=True))

    # ---------- 证据可回溯 ----------
    check("证据下标能取回原句",
          all(AI_STYLE_ESSAY[e["start"]:e["end"]] == e["text"] for e in ai["evidence"]))
    check("证据句按原文顺序排列",
          [e["start"] for e in ai["evidence"]] == sorted(e["start"] for e in ai["evidence"]))
    check("证据数量受限", len(ai["evidence"]) <= AIGC_CONFIG["evidence_max"])
    check("每条证据都写了为什么被圈出", all(e["reasons"] for e in ai["evidence"]))

    # ---------- 长度置信 ----------
    short = detector.analyze("诚信是一种美德。在我们的生活中，诚信无处不在。我们每个人都应该讲诚信。"
                             "只有讲诚信，社会才会更加美好。")
    check("过短文本直接拒绝", not short["success"] and short["error_code"] == "too_short")
    check("短文给了下一步", bool(short.get("hint")))

    medium_len = detector.analyze(AI_STYLE_ESSAY[:120])
    check("不足置信长度标记低置信",
          medium_len["success"] and medium_len["low_confidence"]
          and medium_len["confidence"] < 1.0)
    check("短文本线索强度封顶", medium_len["ai_score"] <= AIGC_CONFIG["short_text_cap"],
          f"score={medium_len['ai_score']}")
    check("notice 里说明样本偏短", "样本偏短" in medium_len["notice"])
    check("长文本置信度满", ai["confidence"] == 1.0 and not ai["low_confidence"])

    # ---------- 句子切分 ----------
    sentences = split_sentences("今天下雨了。我去接同学！好吗？好吧")
    check("切句数量正确", len(sentences) == 4, f"n={len(sentences)}")
    check("切句下标可逆", all("今天下雨了。我去接同学！好吗？好吧"[s["start"]:s["end"]] == s["text"]
                          for s in sentences))
    few_sentences = detector.analyze(
        "诚信是一种美德，是我们每个人都要有的品质，也是社会运转的基础。"
        "只有讲诚信，别人才愿意与你合作，事情才可能做成。"
        "所以我们要从自己做起，从小事做起，把诚信当作立身之本。")
    check("句子很少也能出结果", few_sentences["success"])
    check("句数不足时该项标记为不适用",
          not _signal_of(few_sentences, "sentence_uniformity")["applicable"])
    usable = [s for s in few_sentences["signals"] if s["applicable"]]
    expect = (sum(s["weight"] * s["tendency"] for s in usable)
              / sum(s["weight"] for s in usable))
    check("不适用的信号不参与加权",
          close(few_sentences["ai_score"],
                round(min(expect, AIGC_CONFIG["short_text_cap"]), 4), 1e-6),
          f"expect={expect:.4f} got={few_sentences['ai_score']}")

    # ---------- 映射函数 ----------
    check("反向映射正确", _scale(0.1, 0.6, 0.2) == 1.0 and _scale(0.6, 0.6, 0.2) == 0.0)
    check("正向映射正确", close(_scale(0.9, 0.2, 1.0), 0.875, 1e-9))
    check("超界夹住", _scale(-1.0, 0.2, 1.0) == 0.0 and _scale(9.0, 0.2, 1.0) == 1.0)

    # ---------- 篇章规整度 ----------
    block = ("读书是一种提升自我认知的重要途径。首先，读书能够拓宽我们的视野。"
             "其次，读书能够培养独立思考的能力。再次，读书能够涵养人的情操。")
    essay = "\n\n".join([block.replace("拓宽", "开阔"), block.replace("培养", "塑造"),
                         block.replace("涵养", "丰富"), block])
    structured = detector.analyze(essay)
    structure_signal = next(s for s in structured["signals"] if s["key"] == "structure_regularity")
    check("齐平段落触发篇章规整度", structure_signal["tendency"] > 0.5,
          f"tendency={structure_signal['tendency']:.3f}")
    check("段落数被统计", structured["statistics"]["paragraphs"] == 4)

    # ---------- 脱敏 ----------
    check("长数字串被替换", "[号码]" in anonymize("我的号码是13812345678呀"))
    check("短数字保留", "3页" in anonymize("我才看了3页"))
    check("姓名被替换", "张三" not in anonymize("张三今天很高兴", "张三"))
    check("两位以上姓名才替换", anonymize("他今天很高兴", "王") == "他今天很高兴")

    # ---------- 外部接口增强 ----------
    fake = _FakeClient()
    fused = detector.analyze(AI_STYLE_ESSAY, use_external=True, client=fake, student_name="林小满")
    check("融合分 = 本地 0.6 + 外部 0.4",
          fused["ai_score"] == round(0.6 * fused["local_score"] + 0.4 * 1.0, 4),
          f"ai={fused['ai_score']} local={fused['local_score']}")
    check("本地分单独保留", fused["local_score"] == ai["ai_score"])
    check("外部结果标注模型与成本", fused["external"]["model"] == "fake-model"
          and fused["external"]["cost"] == 0.01)
    check("融合权重写明", fused["external"]["fused_with"] == AIGC_CONFIG["external_weight"])
    prompt = fake.prompts[0][0]
    check("不发送学生姓名", "林小满" not in prompt)
    sent_lines = [line.split(". ", 1)[1] for line in prompt.splitlines()
                  if re.match(r"^\d+\. ", line)]
    check("送出的是编号句", len(sent_lines) == fused["external"]["sentences_sent"])
    check("不发送全文", AI_STYLE_ESSAY not in prompt
          and sum(len(one) for one in sent_lines) <= len(AI_STYLE_ESSAY),
          f"送出字数={sum(len(one) for one in sent_lines)} 原文={len(AI_STYLE_ESSAY)}")
    check("发送句数受限", fused["external"]["sentences_sent"] <= AIGC_CONFIG["review_sentences_max"])
    check("提示词里写明只是节选", "最可疑" in prompt and "已做轻量脱敏" in prompt)
    check("系统提示禁止把工整当证据", "结构工整" in fake.prompts[0][1])

    bad = _FakeClient(reply={"success": False, "error": "今日预算已达上限",
                             "error_code": "budget_exceeded", "from_cache": False})
    degraded = detector.analyze(AI_STYLE_ESSAY, use_external=True, client=bad)
    check("接口失败时保留本地线索",
          degraded["success"] and degraded["ai_score"] == ai["ai_score"]
          and not degraded["external"]["success"])
    check("接口失败给出原因", "预算" in degraded["external"]["error"]
          and degraded["external"]["error_code"] == "budget_exceeded")
    check("接口失败提示本地仍可用", "本地线索仍然有效" in degraded["external"]["hint"])

    junk = _FakeClient(reply={"success": True, "data": {"ai_score": "说不清"},
                              "from_cache": False, "cost": 0.0, "error": "", "error_code": ""})
    bad_score = detector.analyze(AI_STYLE_ESSAY, use_external=True, client=junk)
    check("接口给出非数值时不融合",
          bad_score["ai_score"] == ai["ai_score"] and bad_score["external"]["error_code"] == "bad_score")

    # 未配置密钥：默认客户端必须干净地退化，而不是抛错
    unconfigured = detector.analyze(AI_STYLE_ESSAY, use_external=True)
    check("未配置接口时降级为本地结果",
          unconfigured["success"] and not unconfigured["external"]["success"]
          and unconfigured["external"]["error_code"] in ("not_configured", "no_key", "disabled"),
          f"code={unconfigured['external']['error_code']}")

    # ---------- 配置热更新 ----------
    tuned = AIGCDetector({"levels": {"strong": 0.99, "medium": 0.98}})
    check("阈值可覆盖", tuned.analyze(AI_STYLE_ESSAY)["level"] == "weak")
    check("覆盖后其余项沿用出厂",
          tuned.weights == AIGC_CONFIG["weights"]
          and tuned.config["min_chars"] == AIGC_CONFIG["min_chars"])
    tuned.update_config({"evidence_max": 2})
    check("热更新生效", len(tuned.analyze(AI_STYLE_ESSAY)["evidence"]) <= 2)


# ==================== 运行入口 ====================

def main():
    print("=" * 56)
    print("AntiCopy 算法自测")
    print("=" * 56)

    # 自测不应把噪声写进用户的运行日志，也不该刷屏控制台。
    # 报警文案与控制台输出由 `python main.py demo` 覆盖验证。
    import os
    import tempfile
    import config
    config.ALERT_CONFIG["log_file"] = os.path.join(
        tempfile.gettempdir(), "anticopy_selftest_alerts.log")
    config.ALERT_CONFIG["console"] = False

    tests = [
        test_normalize,
        test_fragments,
        test_vectors,
        test_similarity,
        test_calibration,
        test_weights,
        test_data_flow,
        test_settings,
        test_pairwise,
        test_paper,
        test_aigc,
        test_routes,
        test_dom_ids,
        test_css_classes,
    ]

    for test in tests:
        try:
            test()
        except Exception:
            _FAILED.append(test.__name__)
            print(f"  ERROR {test.__name__}")
            traceback.print_exc()

    print("\n" + "=" * 56)
    if _FAILED:
        print(f"结果：{_PASSED} 项通过，{len(_FAILED)} 项失败")
        for name in _FAILED:
            print(f"  - {name}")
        return 1
    print(f"结果：全部 {_PASSED} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
