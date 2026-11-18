"""Rule baseline for danmaku social-function tagging.

Two layers:

* **Cue matching** - substring cues for the seven-plus-two categories. These are
  the high-confidence matches (0.85-0.92).
* **Shape heuristics** - patterns that are extremely common in Chinese danmaku
  and carry a function on their own: laughter runs (哈哈/233), pure symbols,
  digits, repeated single characters, "first!/front row" markers, appreciation
  one-liners (好听/帅/牛), question asks, and requests for a source (求链接).
  Those land at 0.6-0.8 so a user threshold still gates the weak ones.

The rule layer is what the local service uses for videos that have no offline
LLM tag cache, so coverage matters: a comment that falls through to "other"
cannot be shaped by the viewer's per-label preferences.
"""
from __future__ import annotations

import re

from ..types import ClassificationResult, Danmaku
from .base import Classifier

ATTACK_CUES = (
    "傻逼", "煞笔", "沙比", "sb", "nmsl", "cnm", "你妈", "妈卖批", "草泥马", "脑残", "弱智",
    "智障", "废物", "垃圾玩意", "人渣", "滚出去", "滚蛋", "去死", "死全家", "有病吧",
    "神经病", "杂种", "狗东西", "狗叫", "闭嘴", "恶心人", "晦气", "什么玩意", "恶臭",
)
SPOILER_CUES = (
    "剧透", "后面会", "后面就", "等下会", "接下来会", "结局是", "凶手", "其实他", "最后他",
    "大结局", "会死", "死的是", "剧透预警", "提前剧透", "结尾是", "下一集", "第几集",
)
CONFLICT_CUES = (
    "你懂什么", "别杠", "杠精", "有本事", "谁问你了", "护主", "孝子", "引战", "对线",
    "不服来辩", "你行你上", "急了", "急什么", "带节奏", "洗地", "硬洗", "典中典", "什么成分",
    "扣帽子", "二极管", "上纲上线", "屁股歪", "急了急了", "不讲逻辑",
)
POLITICAL_CUES = (
    "政治", "政府", "官方", "国家", "体制", "政策", "外交", "资本", "资本家", "阶级",
    "爱国", "民族", "台独", "民进党", "左派", "右派", "选举", "领导", "涉政",
    # Geopolitics and institutions dominate the danmaku of news / commentary
    # videos, which is where the political tag earns its keep. Kept to proper
    # nouns and concrete institutions so ordinary praise still falls through.
    "美国", "欧洲", "中东", "法国", "英国", "德国", "日本", "韩国", "俄罗斯",
    "乌克兰", "以色列", "巴勒斯坦", "伊朗", "印度", "特朗普", "川普", "拜登",
    "普京", "联合国", "北约", "欧盟", "白宫", "外交部", "发言人", "制裁",
    "关税", "战争", "移民", "难民", "大选", "投票", "国会", "议会", "国防",
    "军费", "经济下行", "失业", "房价", "社保", "医保", "舆论", "立场",
)
INFLAMMATORY_CUES = (
    "引战", "来吵", "吵起来", "骂起来", "谁不服", "杠上", "开战", "上线对线", "地图炮",
    "你们这群", "低龄", "小学生", "键盘侠", "网暴", "小粉红", "殖人", "汉奸",
)
INFO_CUES = (
    "出自", "原曲", "原版", "bgm", "背景音乐", "科普", "解释一下", "前情提要", "伏笔",
    "致敬", "翻译", "时间线", "出处", "番名", "歌名", "链接", "bv号", "av号", "工具",
    "教程", "数据", "来源", "第几", "叫什么名字", "哪一集", "字幕组",
)
EMOTION_CUES = (
    "泪目", "哭了", "泪崩", "破防", "心疼", "感动", "共鸣", "真实", "爱了", "温柔",
    "意难平", "好听", "太好听", "帅", "好帅", "可爱", "心动", "想起", "回忆", "青春",
    "爷青回", "泪奔", "治愈", "温暖", "顶不住", "绷不住了", "好家伙", "绝了",
)
HUMOR_CUES = (
    "梗", "笑死", "神评", "太草", "草", "哈哈哈", "绷不住", "节目效果", "这波", "神来之笔",
    "离谱", "好活", "整活", "上大分", "xswl", "233", "666", "牛批", "笑不活了", "哈哈",
    "笑死我了", "乐", "太骚了", "整挺好",
)

REPEAT_PHRASES = {
    "1", "11", "111", "1111", "233", "333", "666", "第一", "前排", "路过", "打卡", "来了",
    "沙发", "空降", "二刷", "三刷", "四刷", "签到", "到此一游", "留名", "冒泡", "火钳刘明",
}

LAUGH_ONLY = re.compile(r"^(?:哈|h|H|草|2|3|6|啊)+$")
PANGO = re.compile(r"^[\s\W_]+$")
DIGITS_ONLY = re.compile(r"^[0-9０-９\s.]+$")
REPEAT_ONLY = re.compile(r"^(.{1,2})\1{2,}$")
QUESTION = re.compile(r"[?？]$")
REQUEST = re.compile(r"^(求|请问|有人知道|谁知道|求问)")
PRAISE = re.compile(r"(好听|好看|好帅|好美|好强|太强|牛逼|牛啊|厉害|帅死|帅爆|绝了|美哭)")
SOURCE_ASK = re.compile(r"(链接|叫什么|什么歌|什么番|哪部|出处|名字|几分钟)")


def _match(text: str, cues: tuple[str, ...]) -> str | None:
    lowered = text.lower()
    for cue in cues:
        if len(cue) >= 2:
            if cue.lower() in lowered:
                return cue
        elif text == cue or text.startswith(cue) or text.endswith(cue):
            return cue
    return None


def _repetition(text: str) -> str | None:
    if text in REPEAT_PHRASES:
        return text
    if LAUGH_ONLY.match(text):
        return "laugh-only"
    if REPEAT_ONLY.match(text):
        return "repeated pattern"
    if PANGO.match(text):
        return "punctuation only"
    if DIGITS_ONLY.match(text) and len(text) <= 8:
        return "digits only"
    return None


class RuleClassifier(Classifier):
    name = "rules"

    def classify(self, dm: Danmaku) -> ClassificationResult:
        text = dm.text.strip()
        if not text:
            return ClassificationResult("irrelevant_repetition", 0.7, "empty text", self.name)

        # Shape rules first: "哈哈哈"/"233"/"？？？" carry no other function.
        cue = _repetition(text)
        if cue is not None:
            return ClassificationResult("irrelevant_repetition", 0.8, f"repetition: {cue}", self.name)

        checks = (
            ("attack", ATTACK_CUES, 0.92),
            ("spoiler", SPOILER_CUES, 0.88),
            ("conflict", CONFLICT_CUES, 0.85),
            ("inflammatory", INFLAMMATORY_CUES, 0.85),
            ("political", POLITICAL_CUES, 0.8),
            ("information", INFO_CUES, 0.86),
            ("emotional_resonance", EMOTION_CUES, 0.85),
            ("humor", HUMOR_CUES, 0.85),
        )
        for label, cues, confidence in checks:
            cue = _match(text, cues)
            if cue is not None:
                return ClassificationResult(label, confidence, f"matched cue: {cue}", self.name)

        # Softer shape heuristics.
        if PRAISE.search(text):
            return ClassificationResult("emotional_resonance", 0.72, "praise phrase", self.name)
        if REQUEST.match(text) or (SOURCE_ASK.search(text) and len(text) <= 20):
            return ClassificationResult("information", 0.68, "asks for a source", self.name)
        if QUESTION.search(text) and len(text) <= 24:
            return ClassificationResult("information", 0.6, "question", self.name)
        return ClassificationResult("other", 0.5, "no rule matched", self.name)
