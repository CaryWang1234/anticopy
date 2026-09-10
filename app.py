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
from werkzeug.utils import secure_filename
from detection_engine import DetectionEngine
from config import WEB_CONFIG, DETECTION_CONFIG, WEIGHTS, SIMILARITY_THRESHOLDS, BASE_DIR, IS_FROZEN


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
            return json.load(f)
    except Exception:
        return []


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def save_to_history(mode, standard_answer, results, statistics):
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
    history.append(entry)
    save_history(history)
    return entry


def attach_photos(result, students):
    """将学生上传的答案照片按姓名挂到对应检测结果上"""
    photo_map = {}
    for i, s in enumerate(students):
        name = s.get("name") or f"学生{i+1}"
        if s.get("photos"):
            photo_map[name] = s["photos"]
    for r in result.get("results", []):
        r["photos"] = photo_map.get(r.get("student_name"), [])


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


@app.route("/api/detect/image", methods=["POST"])
def api_detect_image():
    """图片检测 API"""
    if "image" not in request.files:
        return jsonify({"success": False, "error": "请上传图片"}), 400

    image_file = request.files["image"]
    if image_file.filename == "":
        return jsonify({"success": False, "error": "未选择文件"}), 400

    # 验证文件类型
    ext = image_file.filename.rsplit(".", 1)[-1].lower() if "." in image_file.filename else ""
    if ext not in DETECTION_CONFIG["supported_image_types"]:
        return jsonify({"success": False, "error": f"不支持的图片格式: {ext}"}), 400

    # 保存文件
    filename = secure_filename(f"upload_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}")
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    image_file.save(filepath)

    # 获取学生答案
    students_json = request.form.get("students", "[]")
    try:
        students = json.loads(students_json)
    except json.JSONDecodeError:
        students = []

    if not students:
        return jsonify({"success": False, "error": "请至少添加一份学生答案"}), 400

    result = engine.detect_image_batch(filepath, students)
    result["timestamp"] = datetime.now().isoformat()

    if result.get("success"):
        attach_photos(result, students)
        save_to_history("image", result.get("standard_answer", ""),
                        result["results"], result["statistics"])

    # 清理临时文件
    try:
        os.remove(filepath)
    except OSError:
        pass

    return jsonify(result)


@app.route("/api/detect/api-search", methods=["POST"])
def api_detect_with_search():
    """通过搜题 API 获取标准答案后检测"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    question = data.get("question", "").strip()
    students = data.get("students", [])

    if not question:
        return jsonify({"success": False, "error": "题目不能为空"}), 400
    if not students:
        return jsonify({"success": False, "error": "请至少添加一份学生答案"}), 400

    result = engine.detect_text_with_api(question, students)
    result["timestamp"] = datetime.now().isoformat()

    if result.get("success"):
        attach_photos(result, students)
        save_to_history("api", result.get("standard_answer", ""),
                        result["results"], result["statistics"])

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
    """导出检测报告"""
    data = request.get_json()
    results = data.get("results", [])
    statistics = data.get("statistics", {})
    standard_answer = data.get("standard_answer", "")
    timestamp = data.get("timestamp", datetime.now().isoformat())

    if not results:
        return jsonify({"success": False, "error": "无数据可导出"}), 400

    report = generate_detailed_report(results, statistics, standard_answer, timestamp)

    report_path = os.path.join(
        app.config["UPLOAD_FOLDER"],
        f"report_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    return send_file(report_path, as_attachment=True, download_name="抄袭检测报告.txt")


def generate_detailed_report(results, statistics, standard_answer, timestamp):
    """生成详细的检测报告"""
    lines = []
    lines.append("=" * 60)
    lines.append("作业抄袭检测报告")
    lines.append("=" * 60)
    lines.append(f"检测时间: {timestamp[:19].replace('T', ' ')}")
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
    lines.append(f"  抄袭率(≥50%): {statistics.get('plagiarism_rate', 0)*100:.1f}%")
    lines.append(f"  高风险(≥80%): {statistics.get('high_count', 0)} 份")
    lines.append(f"  中风险(50-80%): {statistics.get('medium_count', 0)} 份")
    lines.append(f"  低风险(30-50%): {statistics.get('low_count', 0)} 份")
    lines.append(f"  安全(<30%): {statistics.get('safe_count', 0)} 份")
    lines.append("")

    lines.append("【详细结果】")
    lines.append("-" * 60)

    for i, r in enumerate(results, 1):
        lines.append(f"\n[{i}] {r.get('student_name', '未知')}")
        lines.append(f"    综合相似度: {r['weighted_score']*100:.1f}%  等级: {r['level'].upper()}")
        lines.append(f"    精确匹配: {r['exact_match']*100:.1f}%  "
                     f"序列相似: {r['sequence_similarity']*100:.1f}%  "
                     f"关键词重叠: {r['keyword_overlap']*100:.1f}%")
        student_answer = r.get('student_answer', '')
        if student_answer:
            snippet = student_answer[:100] + ('...' if len(student_answer) > 100 else '')
            lines.append(f"    答案摘要: {snippet}")

    lines.append("\n" + "=" * 60)
    lines.append("报告结束")
    lines.append("=" * 60)
    return "\n".join(lines)


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    """获取当前设置"""
    return jsonify({
        "success": True,
        "weights": engine.calculator.weights,
        "thresholds": SIMILARITY_THRESHOLDS
    })


@app.route("/api/settings", methods=["POST"])
def api_update_settings():
    """更新检测参数"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "请求数据为空"}), 400

    if "weights" in data:
        w = data["weights"]
        engine.calculator.weights = {
            "exact_match": w.get("exact_match", 0.4),
            "sequence_similarity": w.get("sequence_similarity", 0.35),
            "keyword_overlap": w.get("keyword_overlap", 0.25)
        }

    if "thresholds" in data:
        t = data["thresholds"]
        SIMILARITY_THRESHOLDS["high"] = t.get("high", 0.8)
        SIMILARITY_THRESHOLDS["medium"] = t.get("medium", 0.5)
        SIMILARITY_THRESHOLDS["low"] = t.get("low", 0.3)

    return jsonify({"success": True, "message": "设置已更新"})


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
    """系统健康检查"""
    api_status = engine.api_client.health_check()
    return jsonify({
        "success": True,
        "apis": api_status,
        "timestamp": datetime.now().isoformat()
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
