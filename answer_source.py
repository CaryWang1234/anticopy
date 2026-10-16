"""
作业抄袭检测系统 - 参考答案获取（大模型生成）

替代原先依赖作业帮 / 小猿搜题的「搜题」路径。这两家的拍照搜题功能在
「双减」政策下被限制（答案查看需实名验证、只向家长教师开放），并不存在
可用的公开 API，因此这里改为走通用大模型生成参考答案与评分要点。

关键取舍：
- 结果一律标注「AI 生成，建议老师校对」。大模型会做错题，参考答案错了
  整批比对结果就全错，这个责任不能由算法悄悄担。
- 走 llm_client，因此自带题干级缓存、每日预算闸与用量记账。
- 未配置密钥时不静默失败，而是回一句可操作的提示：改用文本模式粘贴答案。
"""

from llm_client import LLMClient

# 题干长度上限：过长的输入既贵又说明老师粘错了东西（整页 OCR 文本）
MAX_QUESTION_CHARS = 1200

_SYSTEM_PROMPT = (
    "你是中小学阅卷老师手里的参考答案助手。你的任务只有一个：给出某道题的参考答案"
    "和评分要点，供老师比对学生的作答。你需要：\n"
    "1. 严格按题目要求作答，不自作主张扩展题意；\n"
    "2. 只给要点和必要步骤，不写长篇讲解，不寒暄、不声明自己是 AI；\n"
    "3. 题目信息不足以唯一确定答案时，在 uncertainty 里说明缺什么条件，"
    "并给出最可能的答案；\n"
    "4. 只输出 JSON，不要输出 JSON 以外的任何文字。"
)


def _user_prompt(question: str, subject: str, grade: str, answer_style: str) -> str:
    style_map = {
        "short": "答案很短（填空、简答、判断类），reference_answer 控制在 120 字内",
        "standard": "标准解答，给出关键步骤或要点，reference_answer 控制在 300 字内",
        "essay": "作文或长题，reference_answer 给写作要点与参考思路，控制在 400 字内",
    }
    lines = [
        f"题目：{question}",
        f"学段：{grade or '未指定'}",
        f"学科：{subject or '未指定'}",
        f"答案要求：{style_map.get(answer_style, style_map['standard'])}",
        "",
        "按下面结构输出 JSON：",
        '{"reference_answer": "参考答案正文",',
        ' "key_points": ["评分要点1", "评分要点2"],',
        ' "uncertainty": "题目条件不足时说明，否则留空字符串"}',
    ]
    return "\n".join(lines)


def generate_reference(question: str, subject: str = "", grade: str = "",
                       answer_style: str = "standard", client: LLMClient = None) -> dict:
    """
    生成参考答案与评分要点。

    :param question: 题目文本（图片模式请先由 OCR 转成文本）
    :param subject: 学科提示，可空
    :param grade: 学段提示，可空
    :param answer_style: short / standard / essay
    :return: {"success", "reference_answer", "key_points", "uncertainty",
              "from_cache", "cost", "usage", "model", "error", "error_code"}
    """
    question = (question or "").strip()
    if len(question) < 2:
        return _failure("题目内容太短，无法判断要答什么", "bad_input")

    active = client or LLMClient()
    result = active.chat_json(
        _user_prompt(question[:MAX_QUESTION_CHARS], subject, grade, answer_style),
        _SYSTEM_PROMPT,
        max_tokens=700,
    )

    if not result.get("success"):
        return _failure(result.get("error", "生成失败"),
                        result.get("error_code", "llm_failed"))

    data = result.get("data") or {}
    reference = str(data.get("reference_answer") or "").strip()
    if not reference:
        return _failure("接口返回的结果里没有参考答案正文", "empty_answer")

    key_points = data.get("key_points")
    if not isinstance(key_points, list):
        key_points = []

    return {
        "success": True,
        "reference_answer": reference,
        "key_points": [str(point).strip() for point in key_points if str(point).strip()],
        "uncertainty": str(data.get("uncertainty") or "").strip(),
        # 界面上必须显示这条提示：AI 生成的答案可能错，老师要先看一眼
        "notice": "参考答案由 AI 生成，可能存在错误，请先核对再用于比对",
        "from_cache": bool(result.get("from_cache")),
        "cost": result.get("cost", 0.0),
        "usage": result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0}),
        "model": active.model,
        "error": "",
        "error_code": "",
    }


def _failure(message: str, code: str) -> dict:
    """失败也要给出下一步做什么，不能只留一个错误码。"""
    hint = {
        "not_configured": "请在 设置 → 大模型接口 填写接口地址与模型名称",
        "no_key": "请在 设置 → 大模型接口 填写 API 密钥；也可以直接手动粘贴标准答案",
        "budget_exceeded": "今日预算已用完，明天再用，或直接手动粘贴标准答案",
        "disabled": "该功能已在设置中关闭，可直接手动粘贴标准答案",
    }.get(code, "")
    return {
        "success": False,
        "reference_answer": "",
        "key_points": [],
        "uncertainty": "",
        "notice": "",
        "from_cache": False,
        "cost": 0.0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        "model": "",
        "error": message,
        "error_code": code,
        "hint": hint,
    }
