"""Danmaku social-function taxonomy: the single source of truth.

Human-readable definitions and annotation guidance live in docs/taxonomy.md.
The machine-readable copy used by the annotation tool and the browser plugin
is generated from this module:

    python -m danmaku_filter.cli taxonomy > config/taxonomy.json

Edit the Python lists below, then regenerate. Do not hand-edit the JSON.
"""
from __future__ import annotations

import json
import sys

CATEGORY_DEFS = [
    {
        "id": "information",
        "name": "Information",
        "definition": (
            "Adds factual or explanatory content about the video: sources, trivia, "
            "background, translations, timestamps, or explanations of what is on screen."
        ),
        "examples": [
            "这个BGM出自《星际穿越》",
            "注意看右下角的时间",
            "原曲是肖邦的夜曲",
        ],
        "visibility": "show",
        "priority": 3,
    },
    {
        "id": "humor",
        "name": "Humor",
        "definition": (
            "A joke, pun, meme reference, or witty remark whose main purpose is to "
            "amuse, with some content beyond pure laughter."
        ),
        "examples": ["这波操作属实是反向带货", "导演：我谢谢你"],
        "visibility": "show",
        "priority": 6,
    },
    {
        "id": "emotional_resonance",
        "name": "Emotional resonance",
        "definition": (
            "Expresses or echoes emotion, empathy, agreement, or shared feeling with "
            "the video or with other viewers."
        ),
        "examples": ["看到这里真的泪目了", "太真实了，说的就是我"],
        "visibility": "show",
        "priority": 5,
    },
    {
        "id": "irrelevant_repetition",
        "name": "Irrelevant repetition",
        "definition": (
            "Low-information, repetitive, or spam-like reactions: character flooding, "
            "number or word spam, first, front row, passing by, empty hype."
        ),
        "examples": ["哈哈哈哈哈哈哈", "1111", "第一", "前排打卡"],
        "visibility": "collapse",
        "priority": 7,
    },
    {
        "id": "attack",
        "name": "Attack",
        "definition": (
            "Insults, harassment, or hostility directed at a person or group: the "
            "creator, other viewers, or people in the video."
        ),
        "examples": ["up主就是个傻逼", "滚出去吧别恶心人了"],
        "visibility": "hide",
        "priority": 1,
    },
    {
        "id": "conflict",
        "name": "Conflict",
        "definition": (
            "Argumentative or confrontational replies that escalate disagreement, "
            "including provocation and dismissive taunts, without an explicit insult."
        ),
        "examples": ["你懂什么就在这里说", "别杠了，有本事你来"],
        "visibility": "hide",
        "priority": 2,
    },
    {
        "id": "spoiler",
        "name": "Spoiler",
        "definition": (
            "Reveals content that appears later in the video or story and ruins "
            "surprise; requires video context and a timestamp to judge."
        ),
        "examples": ["剧透预警：他最后没活下来", "别急，等下就反转了"],
        "visibility": "hide",
        "priority": 4,
    },
    {
        "id": "political",
        "name": "Political stance",
        "definition": (
            "States, implies, or mocks a political position on public affairs: policy, "
            "institutions, leaders, elections, or national, ethnic and regional identity. "
            "The stance can be serious or ironic."
        ),
        "examples": ["这政策早该改了", "懂的都懂", "又开始赢麻了"],
        "visibility": "show",
        "priority": 8,
    },
    {
        "id": "inflammatory",
        "name": "Inflammatory bait",
        "definition": (
            "Deliberately provokes or takes sides in a heated social issue (gender, region, "
            "fandom, morality) to start a fight or rally a camp, without necessarily "
            "insulting a specific person."
        ),
        "examples": ["这也能洗？", "两边都别装了", "不服来辩"],
        "visibility": "hide",
        "priority": 2,
    },
]

OTHER_DEF = {
    "id": "other",
    "name": "Other / unclassifiable",
    "definition": (
        "Text whose social function cannot be judged from the text and timestamp, or that "
        "does not serve any function above: off-topic chatter, private inside references, "
        "raw symbol strings, or unreadable fragments."
    ),
    "examples": ["？？？", "蹲一个", "-----"],
    "visibility": "show",
    "priority": 99,
    "fallback": True,
    "usage": (
        "Last resort, not a safe default. Label other only when every tag scores below "
        "0.2; when a comment is merely ambiguous, give the closest tag a low score and "
        "explain the choice in the note."
    ),
}

# Ordered decision procedure: the annotation guide and the LLM prompt must run
# the same checks in the same order, otherwise label boundaries drift apart.
DECISION_ORDER = [
    (
        "noise",
        "Noise first. If the comment is only symbols, repeated characters, a "
        "digit/letter flood, empty hype or a check-in (？？？, 。。。。, 1111, kkkk, "
        "哈哈哈哈 with nothing else, 第一, 前排, 打卡), score "
        "irrelevant_repetition 0.8-1.0 instead of treating it as a joke.",
    ),
    (
        "risk",
        "Risk second. Insulting a person or group is attack; taunting or escalating "
        "an argument without an insult is conflict; baiting a crowd on a heated "
        "social issue is inflammatory; revealing a concrete plot event or ending is "
        "spoiler. Risk tags outrank flavour tags and can stack with each other.",
    ),
    (
        "information",
        "Information before flavour. If the comment adds a fact, source, credit, "
        "translation, timestamp, title, number or a genuine question about the "
        "video, score information 0.6 or higher even when the tone is playful. A "
        "joke that carries a fact keeps information high and humour moderate.",
    ),
    (
        "humour_vs_emotion",
        "Humour versus resonance. Humour needs language play: a pun, meme twist, "
        "absurd comparison, reversal or reference. A plain emotional reaction "
        "(好听, 泪目, 太真实了, 破防了, 帅, 好感动) is emotional_resonance even when "
        "it reads funny. If both apply, humour 0.6-0.8 and emotional_resonance "
        "0.4-0.6.",
    ),
    (
        "other",
        "Only then other. Keep other below 0.2 unless no function above reaches 0.2.",
    ),
]


def decision_order() -> str:
    """Numbered decision procedure shared by prompt v3 and the annotation guide."""
    return chr(10).join(
        "%d. [%s] %s" % (index, name, text)
        for index, (name, text) in enumerate(DECISION_ORDER, start=1)
    )


def prompt_guide_v3() -> str:
    """Category guide plus the ordered decision procedure (prompt v3)."""
    return (
        prompt_guide()
        + chr(10)
        + "Decision order (apply in this order; a later step does not cancel an earlier one that truly applies):"
        + chr(10)
        + decision_order()
    )


DECISION_ORDER_V4 = [
    (
        "noise",
        "Noise first, but only pure noise. If the whole comment is symbols, repeated "
        "characters, a digit/letter flood, empty hype or a check-in and nothing else "
        "(？？？, 。。。。, 1111, kkkk, 纯 哈哈哈哈, 第一, 前排, 打卡), score "
        "irrelevant_repetition 0.8-1.0. A flood plus a word of commentary is not pure "
        "noise: 哈哈哈哈这翻译 is humour.",
    ),
    (
        "risk",
        "Risk second, and only for real hostility. Insulting a person or group is "
        "attack; escalating an argument or taunting without an insult is conflict; "
        "baiting a crowd on a heated social issue is inflammatory; revealing a concrete "
        "plot event or ending is spoiler. A mild call-out of the creator (编剧你出来, "
        "导演我谢谢你) is humour or conflict, not attack.",
    ),
    (
        "information",
        "Information before flavour, and it is never cancelled by a joke. If the comment "
        "adds a fact, source, credit, translation, timestamp, title, number or a genuine "
        "question about the video, score information 0.6 or higher even when the tone is "
        "playful and even when humour ends up dominant. Dominant is the primary intent; "
        "a co-present fact still has to keep its score.",
    ),
    (
        "humour_vs_emotion",
        "Humour versus resonance. Humour needs language play: a pun, meme twist, absurd "
        "comparison, reversal, reference, or a laughter marker attached to a remark "
        "(笑死, 哈哈哈哈, 离谱, 好家伙). A plain feeling about the video (好听, 泪目, "
        "太真实了, 破防了, 帅, 好感动) with no joke is emotional_resonance. If both "
        "apply, humour 0.6-0.8 and emotional_resonance 0.4-0.6.",
    ),
    (
        "other",
        "Only then other. Keep other below 0.2 unless no function above reaches 0.2.",
    ),
]


def prompt_guide_v4() -> str:
    """Category guide plus the tightened decision procedure (prompt v4)."""
    return (
        prompt_guide()
        + chr(10)
        + "Decision order (apply in this order; a later step does not cancel an earlier one that truly applies):"
        + chr(10)
        + decision_order_v4()
    )


def decision_order_v4() -> str:
    """Numbered v4 decision order, one line per step."""
    return (
        chr(10)
        + chr(10).join(
            "%d. %s" % (index, text)
            for index, (_name, text) in enumerate(DECISION_ORDER_V4, start=1)
        )
    )


CATEGORY_IDS = [category["id"] for category in CATEGORY_DEFS]
CATEGORY_BY_ID = {category["id"]: category for category in CATEGORY_DEFS}
FALLBACK_ID = "other"


def spec() -> dict:
    return {
        "version": 0.3,
        "language": "zh",
        "fallback": FALLBACK_ID,
        "note": "Generated from danmaku_filter/taxonomy.py; edit the Python source, then regenerate.",
        "categories": CATEGORY_DEFS + [OTHER_DEF],
    }


def prompt_guide() -> str:
    """Compact category guide used inside the LLM prompt."""
    lines = []
    for category in CATEGORY_DEFS:
        examples = " / ".join(category["examples"])
        lines.append(f"- {category['id']} ({category['name']}): {category['definition']} Examples: {examples}")
    lines.append(
        "- other (Other / unclassifiable): last resort, not a safe default. Use it only when "
        "every function above would score below 0.2 (symbol spam, off-topic chatter, inside "
        "jokes with no recoverable meaning). If a comment is merely ambiguous, give the "
        "closest function a low score (0.2-0.4) and explain the choice in the note."
    )
    return "\n".join(lines)


def main() -> int:
    print(json.dumps(spec(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
