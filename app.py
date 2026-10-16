"""
作业抄袭检测系统 - Flask Web 应用
提供美观的 Web 界面进行在线抄袭检测
"""

import os
import sys
import json
import uuid
import webbrowser
import socket
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from detection_engine import DetectionEngine
from similarity import METRIC_KEYS, METRIC_LABELS, normalize_legacy_result
from aigc_detector import DETECTOR_VERSION
from settings_store import save_settings, profiles as settings_profiles
from llm_client import save_api_key, clear_cache as clear_llm_cache
from config import WEB_CONFIG, DETECTION_CONFIG, SIMILARITY_THRESHOLDS, BASE_DIR, IS_FROZEN


def _resource_path(relative: str) -> str:
    """只读资源（模板/静态文件）目录。

    打包后从 PyInstaller 的临时解包目录 sys._MEIPASS 读取；
    普通运行时从项目目录读取。
    """
    if IS_FROZEN:
        base = getattr(sys, "_MEIPASS", BASE_DIR)
        return os.path.join(base, relative)
    return os.path.join(BASE_DIR, relative)


app = Flask(
    __name__,
    template_folder=_resource_path("templates"),
    static_folder=_resource_path("static"),
)
app.config["SECRET_KEY"] = WEB_CONFIG["secret_key"]
app.config["UPLOAD_FOLDER"] = WEB_CONFIG["upload_folder"]
app.config["MAX_CONTENT_LENGTH"] = WEB_CONFIG["max_content_length"]

# 确保上传目录存在
os.makedirs(WEB_CONFIG["upload_folder"], exist_ok=True)

# 初始化检测引擎
engine = DetectionEngine()

# 历史记录存储
HISTORY_FILE = os.path.join(BASE_DIR, "detection_history.json")


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        return []

    # 旧记录缺少新版指标，读入时补齐形状，界面据此走旧渲染路径
    if not isinstance(history, list):
        return []
    for entry in history:
        if isinstance(entry, dict):
            entry["results"] = [
                normalize_legacy_result(r, SIMILARITY_THRESHOLDS)
                for r in entry.get("results", [])
            ]
    return history


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def save_to_history(mode, standard_answer, results, statistics, extra=None):
    """保存检测记录到历史"""
    history = load_history()
    ts = datetime.now().isoformat()
    entry = {
        "id": len(history) + 1,
        "timestamp": ts,
        "mode": mode,
        "standard_answer": standard_answer,
        "results": results,
        "statistics": statistics
    }
    if extra:
        entry["extra"] = extra
    history.append(entry)
    save_history(history)
    return entry


def attach_photos(result, students):
    """
    将学生上传的答案照片挂到对应检测结果上。

    按 index 挂载而不是按姓名——同名学生（或都被叫「学生1」）会导致照片串档。
    结果列表已按相似度重排，因此用 index 反查原始份数。
    """
    for r in result.get("results", []):
        idx = r.get("index")
        if isinstance(idx, int) and 0 <= idx < len(students):
            r["photos"] = students[idx].get("photos") or []
        else:
            r["photos"] = []


def attach_student_photos(result, students):
    """互查模式：照片挂在「人」上而不是「结果」上，按 index 对应。"""
    for entry in result.get("students", []):
        idx = entry.get("index")
        if isinstance(idx, int) and 0 <= idx < len(students):
            entry["photos"] = students[idx].get("photos") or []


# ==================== 页面路由 ====================

@app.route("/")
def index():
    """主页"""
    return render_template("index.html")


# ==================== API 路由 ====================

@app.route("/api/detect/text", methods=["POST"])
def api_detect_text():
    """文本检测 API"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    standard = data.get("standard_answer", "").strip()
    students = data.get("students", [])

    if not standard:
        return jsonify({"success": False, "error": "标准答案不能为空"}), 400
    if not students:
        return jsonify({"success": False, "error": "请至少添加一份学生答案"}), 400

    # 过滤空答案
    students = [s for s in students if s.get("text", "").strip()]
    if not students:
        return jsonify({"success": False, "error": "学生答案不能全部为空"}), 400

    result = engine.detect_text_batch(standard, students)
    attach_photos(result, students)
    result["success"] = True
    result["timestamp"] = datetime.now().isoformat()
    save_to_history("text", standard, result["results"], result["statistics"])
    return jsonify(result)


@app.route("/api/answer/generate", methods=["POST"])
def api_generate_answer():
    """
    用大模型生成参考答案（替代原先依赖作业帮/小猿搜题的搜题路径）。

    失败一律回 200 + success:false + hint：接口没配密钥、预算用满这类情况
    不是操作错误，界面要能原样显示「下一步做什么」而不是报 HTTP 错误。
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"success": False, "error": "题目不能为空"}), 400

    result = engine.generate_answer(
        question,
        subject=(data.get("subject") or "").strip(),
        grade=(data.get("grade") or "").strip(),
        answer_style=(data.get("answer_style") or "standard").strip(),
    )
    result["timestamp"] = datetime.now().isoformat()
    return jsonify(result)


@app.route("/api/detect/aigc", methods=["POST"])
def api_detect_aigc():
    """
    AIGC 作文风格线索：可单篇（text）也可多篇（essays）。

    use_external 为真才会调用大模型，而且只送每篇最可疑的几句。
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    essays = data.get("essays")
    if not isinstance(essays, list) or not essays:
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"success": False, "error": "请粘贴作文内容"}), 400
        essays = [{"name": (data.get("student_name") or "").strip(), "text": text}]

    cleaned = []
    for one in essays:
        if not isinstance(one, dict):
            continue
        text = (one.get("text") or "").strip()
        if text:
            cleaned.append({"name": (one.get("name") or "").strip(), "text": text})
    if not cleaned:
        return jsonify({"success": False, "error": "作文内容全部为空"}), 400

    result = engine.detect_aigc_batch(cleaned, use_external=bool(data.get("use_external")))
    result["timestamp"] = datetime.now().isoformat()
    result["detector_version"] = DETECTOR_VERSION
    return jsonify(result), (200 if result["success"] else 400)


@app.route("/api/detect/pairwise", methods=["POST"])
def api_detect_pairwise():
    """学生互查 API：学生之间两两比对，不需要标准答案"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    students = [s for s in data.get("students", []) if (s.get("text") or "").strip()]
    if len(students) < 2:
        return jsonify({"success": False, "error": "互查至少需要两份有文字的答案"}), 400
    if len(students) > DETECTION_CONFIG["max_batch_size"]:
        return jsonify({
            "success": False,
            "error": f"一次最多比对 {DETECTION_CONFIG['max_batch_size']} 份"
        }), 400

    result = engine.detect_pairwise(students)
    attach_student_photos(result, students)
    result["success"] = True
    result["mode"] = "pairwise"
    result["timestamp"] = datetime.now().isoformat()

    # 历史里的 results 存「对」的扁平形式，导出与统计代码路径可直接复用
    pair_rows = [{
        "student_name": f"{p['a_name']} ↔ {p['b_name']}",
        "a_index": p["a_index"],
        "b_index": p["b_index"],
        "weighted_score": p["score"],
        "level": p["level"],
        "metrics_version": p["metrics_version"],
        "fragments": p["fragments"],
        **p["metrics"],
    } for p in result["pairs"]]
    save_to_history("pairwise", "", pair_rows, result["statistics"], extra={
        "students": result["students"],
        "matrix": result["matrix"],
        "groups": result["groups"],
        "per_student": result["per_student"],
        "pair_count": result["pair_count"],
    })

    return jsonify(result)


@app.route("/api/detect/paper", methods=["POST"])
def api_detect_paper():
    """整卷多题 API：逐题比对后按分值汇总成整卷抄袭指数"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    assignment = data.get("assignment") or {}
    questions = assignment.get("questions") or []
    students = data.get("students") or []

    if not questions:
        return jsonify({"success": False, "error": "请至少添加一道题"}), 400
    if not students:
        return jsonify({"success": False, "error": "请至少添加一份学生答案"}), 400

    result = engine.detect_paper(assignment, students)
    if not result.get("success"):
        return jsonify(result), 400

    result["timestamp"] = datetime.now().isoformat()
    save_to_history("paper", "", result["rollup"], result["statistics"], extra={
        "assignment": assignment,
        "per_question": result["per_question"],
        "rollup": result["rollup"],
    })
    return jsonify(result)


@app.route("/api/history", methods=["GET"])
def api_get_history():
    """获取历史记录"""
    history = load_history()
    return jsonify({"success": True, "history": list(reversed(history[-50:]))})


@app.route("/api/history/<int:history_id>", methods=["GET"])
def api_get_history_detail(history_id):
    """获取历史记录详情"""
    history = load_history()
    for entry in history:
        if entry.get("id") == history_id:
            return jsonify({"success": True, "detail": entry})
    return jsonify({"success": False, "error": "记录不存在"}), 404


@app.route("/api/export", methods=["POST"])
def api_export_report():
    """导出检测报告（txt 纯文本 / csv 便于 Excel 统计）"""
    data = request.get_json()
    results = data.get("results", [])
    statistics = data.get("statistics", {})
    standard_answer = data.get("standard_answer", "")
    timestamp = data.get("timestamp", datetime.now().isoformat())
    mode = data.get("mode", "text")
    report_format = (data.get("format") or "txt").lower()

    if not results:
        return jsonify({"success": False, "error": "无数据可导出"}), 400

    stamp = datetime.now().strftime('%Y%m%d%H%M%S')
    if report_format == "csv":
        content = generate_csv_report(results)
        filename = f"report_{stamp}.csv"
        download_name = "抄袭检测报告.csv"
    else:
        content = generate_detailed_report(results, statistics, standard_answer,
                                           timestamp, mode=mode)
        filename = f"report_{stamp}.txt"
        download_name = "抄袭检测报告.txt"

    report_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    with open(report_path, "w", encoding="utf-8", newline="") as f:
        f.write(content)

    return send_file(report_path, as_attachment=True, download_name=download_name)


def generate_detailed_report(results, statistics, standard_answer, timestamp,
                             mode="text"):
    """生成详细的检测报告（阈值一律从配置读取，避免与设置面板脱节）"""
    hi = SIMILARITY_THRESHOLDS.get("high", 0.8)
    mid = SIMILARITY_THRESHOLDS.get("medium", 0.5)
    low = SIMILARITY_THRESHOLDS.get("low", 0.3)
    mode_labels = {"text": "文本比对", "pairwise": "学生互查", "paper": "整卷多题"}

    lines = []
    lines.append("=" * 60)
    lines.append("作业抄袭检测报告")
    lines.append("=" * 60)
    lines.append(f"检测时间: {str(timestamp)[:19].replace('T', ' ')}")
    lines.append(f"检测模式: {mode_labels.get(mode, mode)}")
    lines.append(f"检测份数: {statistics.get('count', 0)}")
    lines.append("")

    if standard_answer:
        lines.append("【标准答案】")
        lines.append(standard_answer[:500] + ("..." if len(standard_answer) > 500 else ""))
        lines.append("")

    lines.append("【统计摘要】")
    lines.append(f"  平均相似度: {statistics.get('avg_score', 0)*100:.1f}%")
    lines.append(f"  最高相似度: {statistics.get('max_score', 0)*100:.1f}%")
    lines.append(f"  最低相似度: {statistics.get('min_score', 0)*100:.1f}%")
    lines.append(f"  抄袭率(≥{mid*100:.0f}%): {statistics.get('plagiarism_rate', 0)*100:.1f}%")
    lines.append(f"  高风险(≥{hi*100:.0f}%): {statistics.get('high_count', 0)} 份")
    lines.append(f"  中风险({mid*100:.0f}-{hi*100:.0f}%): {statistics.get('medium_count', 0)} 份")
    lines.append(f"  低风险({low*100:.0f}-{mid*100:.0f}%): {statistics.get('low_count', 0)} 份")
    lines.append(f"  安全(<{low*100:.0f}%): {statistics.get('safe_count', 0)} 份")
    if statistics.get("low_confidence_count"):
        lines.append(f"  注: {statistics['low_confidence_count']} 份答案过短，分数已按小样本收缩")
    lines.append("")

    lines.append("【详细结果】")
    lines.append("-" * 60)

    for i, r in enumerate(results, 1):
        lines.append(f"\n[{i}] {r.get('student_name', '未知')}")
        lines.append(f"    综合相似度: {r.get('weighted_score', 0)*100:.1f}%  "
                     f"等级: {str(r.get('level', 'safe')).upper()}")
        if r.get("metrics_version") == 0:
            lines.append("    (旧版算法计算的结果，分数口径与当前版本不同)")
        metric_line = "  ".join(f"{METRIC_LABELS[key]} {r.get(key, 0)*100:.1f}%"
                                for key in METRIC_KEYS)
        lines.append(f"    {metric_line}")
        if r.get("low_confidence"):
            lines.append(f"    注意: 答案过短，原始相似度 {r.get('raw_score', 0)*100:.1f}%，"
                         f"已按小样本收缩")

        for frag in (r.get("fragments") or [])[:5]:
            lines.append(f"    雷同片段({frag.get('len', 0)}字): {frag.get('text', '')[:60]}")

        student_answer = r.get('student_answer', '')
        if student_answer:
            snippet = student_answer[:100] + ('...' if len(student_answer) > 100 else '')
            lines.append(f"    答案摘要: {snippet}")

    lines.append("\n" + "=" * 60)
    lines.append("报告结束")
    lines.append("=" * 60)
    return "\n".join(lines)


def generate_csv_report(results):
    """
    生成 CSV（UTF-8 BOM，方便教师直接双击用 Excel 打开不乱码）。
    字段只取教师统计时真正要用的列。
    """
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    header = ["排名", "学生姓名", "综合相似度(%)", "等级"]
    header += [f"{METRIC_LABELS[key]}(%)" for key in METRIC_KEYS]
    header += ["雷同片段数", "答案字数"]
    writer.writerow(header)

    for i, r in enumerate(results, 1):
        row = [i, r.get("student_name", ""),
               f"{r.get('weighted_score', 0)*100:.1f}",
               r.get("level", "")]
        row += [f"{r.get(key, 0)*100:.1f}" for key in METRIC_KEYS]
        row += [len(r.get("fragments") or []), len(r.get("student_answer", ""))]
        writer.writerow(row)

    return "\ufeff" + buffer.getvalue()


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """
    获取当前设置（含可选预设档位，供设置面板渲染）。

    注意 llm 字段：只回脱敏后的 key_masked 与「是否已配置」，
    密钥原文永远不出这台机器——它单独存在 secrets.json 里。
    """
    settings = engine.settings
    return jsonify({
        "success": True,
        "profile": settings["profile"],
        "profiles": settings_profiles(),
        "weights": settings["weights"],
        "thresholds": settings["thresholds"],
        "calibration": settings["calibration"],
        "fragments": settings["fragments"],
        "normalize": settings["normalize"],
        "cluster_margin": settings["cluster_margin"],
        "llm": engine.llm.status(),
        "aigc": settings["aigc"],
    })


@app.route("/api/settings", methods=["POST"])
def api_update_settings():
    """
    更新检测参数并落盘。

    接受两种写法（也可以同时给）：
      {"profile": "long_text"}                     整体套用预设
      {"weights": {...}, "thresholds": {...}, ...} 逐项覆盖
    api_key 是只写字段：只写进 secrets.json，不回显、不进 settings.json。
    非法值一律退回默认，不返回 500——设置面板不该因为一个坏值就崩。
    """
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    # 密钥单独处理：空字符串表示清除
    if "api_key" in data:
        incoming_key = data["api_key"]
        if isinstance(incoming_key, str):
            save_api_key(incoming_key.strip())

    # 以当前设置为底，避免只改一项时把其余项冲回出厂值
    merged = dict(engine.settings)
    for key in ("profile", "weights", "thresholds", "calibration",
                "fragments", "normalize", "cluster_margin", "llm", "aigc"):
        if key in data:
            merged[key] = data[key]

    saved = save_settings(merged)
    engine.apply_settings(saved)

    if data.get("clear_cache"):
        removed = clear_llm_cache()
    else:
        removed = 0

    return jsonify({
        "success": True,
        "message": "设置已保存并生效",
        "profile": saved["profile"],
        "weights": saved["weights"],
        "thresholds": saved["thresholds"],
        "calibration": saved["calibration"],
        "aigc": saved["aigc"],
        "llm": engine.llm.status(),
        "cache_cleared": removed,
    })


@app.route("/api/llm/test", methods=["POST"])
def api_test_llm():
    """
    连通性测试：花几分钱确认接口地址、模型名与密钥是否配对。

    先问 readiness()，配置不全时连请求都不发（不产生任何费用）。
    """
    ok, error_code, message = engine.llm.readiness()
    if not ok:
        return jsonify({"success": False, "error_code": error_code, "error": message,
                        "hint": "请先填写接口地址、模型名称与 API 密钥"})

    reply = engine.llm.chat("只回复两个字：就绪", use_cache=False, max_tokens=8)
    if not reply.get("success"):
        return jsonify({"success": False,
                        "error_code": reply.get("error_code", "llm_failed"),
                        "error": reply.get("error", "测试失败")})

    return jsonify({
        "success": True,
        "message": f"接口可用（模型 {engine.llm.model}）",
        "reply": reply.get("content", "")[:40],
        "cost": reply.get("cost", 0.0),
        "llm": engine.llm.status(),
    })


@app.route("/api/upload/students", methods=["POST"])
def api_upload_students():
    """上传学生答案文件，解析为学生列表"""
    if "file" not in request.files:
        return jsonify({"success": False, "error": "请上传文件"}), 400

    f = request.files["file"]
    if f.filename == "":
        return jsonify({"success": False, "error": "未选择文件"}), 400

    try:
        content = f.read().decode("utf-8-sig")  # 兼容带 BOM 的 UTF-8
    except UnicodeDecodeError:
        return jsonify({"success": False, "error": "文件编码错误，请使用 UTF-8 格式"}), 400

    lines = [l.strip() for l in content.splitlines() if l.strip()]
    if not lines:
        return jsonify({"success": False, "error": "文件内容为空"}), 400

    students = []
    for i, line in enumerate(lines):
        # 支持格式: 姓名,答案 / 姓名\t答案 / 纯答案（每行一份）
        sep = "\t" if "\t" in line else ("," if "," in line else None)
        if sep:
            name, text = line.split(sep, 1)
            students.append({"name": name.strip() or f"学生{i+1}", "text": text.strip()})
        else:
            students.append({"name": f"学生{i+1}", "text": line})

    return jsonify({"success": True, "students": students, "count": len(students)})


@app.route("/api/upload/answer-images", methods=["POST"])
def api_upload_answer_images():
    """上传学生答案照片（支持多张），返回访问 URL 列表"""
    files = request.files.getlist("images")
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify({"success": False, "error": "未收到图片"}), 400

    allowed = set(DETECTION_CONFIG["supported_image_types"])
    urls = []
    for f in files:
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in allowed:
            continue
        # 时间戳+随机后缀避免重名覆盖，jpg 统一存储扩展名
        save_ext = "jpg" if ext in ("jpg", "jpeg") else ext
        filename = f"ans_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}.{save_ext}"
        f.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        urls.append(f"/uploads/{filename}")

    if not urls:
        return jsonify({"success": False, "error": "没有可识别的图片格式"}), 400
    return jsonify({"success": True, "urls": urls})


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    """访问已上传的答案照片"""
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/api/health", methods=["GET"])
def api_health():
    """系统状态：本地检测始终可用，这里只额外报告大模型接口是否就绪。"""
    return jsonify({
        "success": True,
        "modes": ["text", "pairwise", "paper", "aigc"],
        "llm": engine.llm.status()["readiness"],
        "detector_version": DETECTOR_VERSION,
        "timestamp": datetime.now().isoformat(),
    })


# ==================== 错误处理 ====================

@app.errorhandler(413)
def too_large(e):
    return jsonify({"success": False, "error": "文件过大，最大支持 16MB"}), 413


@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "接口不存在"}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"success": False, "error": "服务器内部错误"}), 500


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def start_server(open_browser: bool = True):
    """启动 Web 服务，并在控制台打印本机/手机访问地址。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    port = WEB_CONFIG["port"]
    local_ip = _lan_ip()

    print("\n" + "=" * 52)
    print("  📝 作业抄袭检测系统已启动")
    print(f"  🌐 电脑访问: http://127.0.0.1:{port}")
    print(f"  📱 手机访问: http://{local_ip}:{port}")
    print("  （请让电脑和手机连接同一个 WiFi）")
    print("  ▶ 关闭本窗口即可停止服务")
    print("=" * 52 + "\n")

    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    app.run(
        host=WEB_CONFIG["host"],
        port=port,
        debug=WEB_CONFIG["debug"],
        use_reloader=False,
    )


if __name__ == "__main__":
    start_server(open_browser=True)
