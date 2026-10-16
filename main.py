"""
作业抄袭检测系统 - 主入口
支持多种运行模式：Web / 交互 / 文本 / 图片 / 演示
"""

import sys
import os

# 修复 Windows 终端编码问题
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def show_banner():
    print("""
╔══════════════════════════════════════════════════╗
║                                                  ║
║   🛡️  AntiCopy - 作业抄袭智能检测系统  🛡️       ║
║                                                  ║
║   四种模式 · 六维分析 · 雷同片段 · AIGC 线索      ║
║                                                  ║
╚══════════════════════════════════════════════════╝
    """)


def show_help():
    print("""
使用方法:
  python main.py                  启动 Web 界面 (推荐)
  python main.py interactive      交互式命令行检测
  python main.py text <标准答案> <学生答案> [学生姓名]
                                  单份文本检测
  python main.py aigc <作文txt路径>
                                  单篇作文的 AI 风格线索
  python main.py demo             运行演示
  python main.py help             显示帮助

Web 界面包含四种检测模式:
  文本比对 / 学生互查 / 整卷多题 / AIGC 风格线索

大模型相关（可选，不配置也能用全部本地功能）:
  AI 生成参考答案、AIGC 外部二次判别
  在 设置 → 大模型接口 填写 OpenAI 兼容的接口地址、模型名与密钥
  有每日预算闸门与用量统计，默认不发任何外部请求

算法自测:
  python selftest.py              校验归一化、片段定位、校准、聚类、AIGC 与路由
    """)


def run_web():
    """启动 Web 服务"""
    from app import start_server
    start_server(open_browser=True)


def run_interactive():
    """交互式模式"""
    from detection_engine import DetectionEngine
    engine = DetectionEngine()
    engine.detect_interactive()


def run_text(args):
    """文本检测模式"""
    if len(args) < 2:
        print("❌ 用法: python main.py text <标准答案> <学生答案> [学生姓名]")
        sys.exit(1)

    from detection_engine import DetectionEngine
    engine = DetectionEngine()

    standard = args[0]
    student_answer = args[1]
    student_name = args[2] if len(args) > 2 else "学生1"

    result = engine.detect_text(standard, student_answer, student_name)
    print(f"\n📊 检测结果:")
    print(f"   综合相似度: {result['weighted_score']*100:.1f}%")
    print(f"   等级: {result['level'].upper()}")


def run_aigc(args):
    """AIGC 风格线索模式：读一篇 txt 作文，打印线索与最可疑的句子"""
    if len(args) < 1:
        print("❌ 用法: python main.py aigc <作文txt路径>")
        sys.exit(1)

    path = args[0]
    if not os.path.exists(path):
        print(f"❌ 文件不存在: {path}")
        sys.exit(1)

    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError:
        print("❌ 文件编码错误，请使用 UTF-8 格式")
        sys.exit(1)

    from detection_engine import DetectionEngine
    engine = DetectionEngine()
    name = os.path.splitext(os.path.basename(path))[0]
    result = engine.detect_aigc(text, student_name=name)

    if not result.get("success"):
        print(f"❌ {result.get('error')}")
        return

    print(f"\n🔍 {name}：线索强度 {result['level_label']} "
          f"({result['ai_score'] * 100:.1f}%)，全文 {result['char_count']} 字")
    for signal in result["signals"]:
        if not signal["applicable"]:
            continue
        print(f"   · {signal['label']}：{signal['metric']} — {signal['reading']}")
    print("\n最可疑的句子：")
    for item in result["evidence"][:3]:
        print(f"   [{item['score']:.2f}] {item['text'][:40]}")
    print(f"\n⚠️ {result['notice']}")


def run_demo():
    """演示模式"""
    from detection_engine import DetectionEngine
    engine = DetectionEngine()

    print("\n📝 运行演示...\n")

    standard = "Python是一种高级编程语言，由Guido van Rossum于1989年发明。Python采用缩进表示代码块，支持面向对象、函数式和过程式编程范式。"

    students = [
        {"name": "张三", "text": "Python是一种高级编程语言，由Guido van Rossum在1989年创造。Python使用缩进来表示代码块，支持面向对象、函数式和过程式编程。"},
        {"name": "李四", "text": "Java是由Sun Microsystems开发的面向对象编程语言。Java的特点是跨平台、安全性高，广泛应用于企业级开发。"},
        {"name": "王五", "text": "Python是高级编程语言，Guido van Rossum发明。用缩进表示代码块，支持多种编程范式包括面向对象和函数式。"},
        {"name": "赵六", "text": "C语言是由Dennis Ritchie在贝尔实验室开发的通用编程语言。C语言高效灵活，是许多现代语言的基础。"},
    ]

    result = engine.detect_text_batch(standard, students)

    print("\n📖 标准答案:")
    print(f"   {standard}")

    print(f"\n📊 检测完成！共检测 {result['statistics']['count']} 份")
    print(f"   平均相似度: {result['statistics']['avg_score']*100:.1f}%")
    print(f"   抄袭率: {result['statistics']['plagiarism_rate']*100:.1f}%")

    # AIGC 线索：控制台路径的回归样例（全部本地计算，不发外部请求）
    essay = (
        "诚信是立身之本。在当今社会，诚信的品质显得尤为重要。首先，诚信能够增进人与人之间的"
        "信任。其次，诚信有助于构建和谐的社会环境。综上所述，我们应当践行诚信，让诚信成为"
        "人生的底色，让社会因诚信而更加美好。"
    )
    aigc = engine.detect_aigc(essay)
    print(f"\n🔍 AIGC 风格线索演示: {aigc['level_label']} "
          f"({aigc['ai_score'] * 100:.1f}%)")
    for signal in aigc["signals"][:3]:
        print(f"   · {signal['label']}: {signal['metric']} — {signal['reading']}")
    print(f"   ⚠️ 这是线索，不是判定：{aigc['notice'][:40]}...")


def main():
    show_banner()

    if len(sys.argv) < 2:
        run_web()
        return

    command = sys.argv[1].lower()

    if command == "help" or command == "-h" or command == "--help":
        show_help()
    elif command == "interactive":
        run_interactive()
    elif command == "text":
        run_text(sys.argv[2:])
    elif command == "aigc":
        run_aigc(sys.argv[2:])
    elif command == "demo":
        run_demo()
    elif command == "web":
        run_web()
    else:
        print(f"❌ 未知命令: {command}")
        show_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
