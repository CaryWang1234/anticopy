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
║   多模式检测 · 三维度分析 · 智能报警              ║
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
  python main.py image <图片路径> <学生答案> [学生姓名]
                                  图片检测
  python main.py demo             运行演示
  python main.py help             显示帮助
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


def run_image(args):
    """图片检测模式"""
    if len(args) < 2:
        print("❌ 用法: python main.py image <图片路径> <学生答案> [学生姓名]")
        sys.exit(1)

    from detection_engine import DetectionEngine
    engine = DetectionEngine()

    image_path = args[0]
    student_answer = args[1]
    student_name = args[2] if len(args) > 2 else "学生1"

    result = engine.detect_image(image_path, student_answer, student_name)
    if result.get("success"):
        print(f"\n📊 检测结果:")
        print(f"   综合相似度: {result['weighted_score']*100:.1f}%")
        print(f"   等级: {result['level'].upper()}")
    else:
        print(f"\n❌ 检测失败: {result.get('error')}")


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
    elif command == "image":
        run_image(sys.argv[2:])
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
