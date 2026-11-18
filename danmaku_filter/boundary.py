"""Minimal-pair boundary probes for the danmaku taxonomy.

Each pair holds two comments that share almost the same words but sit on
opposite sides of a label boundary (information vs humour, humour vs emotional
resonance, spoiler vs joke, spam vs joke, risk tags). They serve three jobs:

1. few-shot anchors inside the LLM prompt (see prompt_style v3),
2. a regression test corpus, so a prompt change cannot silently collapse one
   label into another,
3. an evaluation set for docs/experiments/e8-boundary.md.

The expected labels are constructed with the sentence, not human-annotated; they
encode the decision order written in docs/taxonomy.md and in
taxonomy.DECISION_ORDER. Treat them as a designed instrument, not as ground
truth about what a random viewer would say.
"""
from __future__ import annotations

# (focus, left, right, why)
# left / right = (text, expected_dominant, {tag: minimum score})
PAIRS = [
    # information vs humour: the fact must survive the joke
    (
        "information vs humor",
        ("这首歌是1987年的", "information", {"information": 0.6}),
        ("这首歌1987年的，我爸都会唱", "information", {"information": 0.6, "humor": 0.4}),
        "same fact, the second one adds a joke; a playful tone does not erase the fact",
    ),
    (
        "information vs humor",
        ("原曲是肖邦的夜曲", "information", {"information": 0.6}),
        ("原曲是肖邦的夜曲，难怪这么催眠", "information", {"information": 0.6, "humor": 0.4}),
        "the second one is a joke built on the fact, not instead of it",
    ),
    (
        "information vs humor",
        ("字幕在3:42", "information", {"information": 0.6}),
        ("字幕在3:42，我瞎了三年", "information", {"information": 0.6, "humor": 0.4}),
        "timestamp stays information even when the comment laughs at itself",
    ),
    (
        "information vs humor",
        ("这是第3集", "information", {"information": 0.6}),
        ("这才第3集？进度条骗我", "information", {"information": 0.5, "humor": 0.5}),
        "episode number plus surprise; information outranks the joke",
    ),
    (
        "information vs humor",
        ("原著里没有这段", "information", {"information": 0.6}),
        ("原著里没有这段，编剧你出来", "information", {"information": 0.6, "humor": 0.5}),
        "adaptation note plus a jab; the note is the dominant function",
    ),
    # information vs emotional resonance
    (
        "information vs emotional_resonance",
        ("这集只有24分钟", "information", {"information": 0.6}),
        ("这集看哭了", "emotional_resonance", {"emotional_resonance": 0.8}),
        "episode fact vs personal reaction",
    ),
    (
        "information vs emotional_resonance",
        ("这首歌原唱是李宗盛", "information", {"information": 0.6}),
        ("这首歌听一次哭一次", "emotional_resonance", {"emotional_resonance": 0.8}),
        "credit vs feeling",
    ),
    (
        "information vs emotional_resonance",
        ("这段是实拍", "information", {"information": 0.6}),
        ("这段实拍太震撼了", "emotional_resonance", {"emotional_resonance": 0.7}),
        "production fact vs awe",
    ),
    # humour vs emotional resonance
    (
        "humor vs emotional_resonance",
        ("好听", "emotional_resonance", {"emotional_resonance": 0.8}),
        ("好听，笑死", "humor", {"humor": 0.7}),
        "a plain reaction is resonance; added laughter makes it humour",
    ),
    (
        "humor vs emotional_resonance",
        ("泪目", "emotional_resonance", {"emotional_resonance": 0.8}),
        ("泪目，结果下一句给我整笑了", "humor", {"humor": 0.6, "emotional_resonance": 0.5}),
        "the twist turns the feeling into a joke",
    ),
    (
        "humor vs emotional_resonance",
        ("破防了", "emotional_resonance", {"emotional_resonance": 0.8}),
        ("破防了，这剧本是谁写的哈哈哈", "humor", {"humor": 0.7}),
        "venting vs mocking the writing",
    ),
    (
        "humor vs emotional_resonance",
        ("帅", "emotional_resonance", {"emotional_resonance": 0.8}),
        ("帅得我想报警", "humor", {"humor": 0.7}),
        "a one-word compliment is resonance; an absurd comparison is humour",
    ),
    # spoiler vs joke
    (
        "spoiler vs humor",
        ("凶手是他弟弟", "spoiler", {"spoiler": 0.8}),
        ("我赌五毛凶手是他弟弟", "humor", {"humor": 0.7}),
        "stating the twist vs guessing it as banter",
    ),
    (
        "spoiler vs humor",
        ("他最后没活下来", "spoiler", {"spoiler": 0.8}),
        ("他最后没活下来，我剧透了吗", "humor", {"humor": 0.6, "spoiler": 0.4}),
        "the meta joke lands on a real spoiler, but the dominant function is the joke",
    ),
    (
        "spoiler vs humor",
        ("第8集才反转", "spoiler", {"spoiler": 0.6}),
        ("第8集才反转，我先睡为敬", "humor", {"humor": 0.6}),
        "plot timing vs dropping the show as a bit",
    ),
    # spam vs humor
    (
        "irrelevant_repetition vs humor",
        ("哈哈哈哈", "irrelevant_repetition", {"irrelevant_repetition": 0.8}),
        ("哈哈哈哈这翻译", "humor", {"humor": 0.7}),
        "pure laughter is spam; laughter plus a target is a joke",
    ),
    (
        "irrelevant_repetition vs humor",
        ("1111", "irrelevant_repetition", {"irrelevant_repetition": 0.8}),
        ("1111？这是报数吗", "humor", {"humor": 0.7}),
        "keyboard flood vs a remark about the flood",
    ),
    (
        "irrelevant_repetition vs humor",
        ("？？？", "irrelevant_repetition", {"irrelevant_repetition": 0.8}),
        ("？？？这也行", "humor", {"humor": 0.6}),
        "bare punctuation vs punctuation with a comment",
    ),
    (
        "irrelevant_repetition vs humor",
        ("好家伙", "irrelevant_repetition", {"irrelevant_repetition": 0.6}),
        ("好家伙，这波反向带货", "humor", {"humor": 0.7}),
        "empty hype vs a full joke",
    ),
    # risk tags
    (
        "attack vs conflict",
        ("up主就是个傻逼", "attack", {"attack": 0.8}),
        ("你懂什么就在这里说", "conflict", {"conflict": 0.7}),
        "insult vs dismissive challenge",
    ),
    (
        "conflict vs inflammatory",
        ("别杠了，有本事你来", "conflict", {"conflict": 0.7}),
        ("不服来辩", "inflammatory", {"inflammatory": 0.7}),
        "a retort between two people vs baiting a crowd",
    ),
    (
        "political vs inflammatory",
        ("这政策早该改了", "political", {"political": 0.7}),
        ("这也能洗？", "inflammatory", {"inflammatory": 0.7}),
        "a stance vs a call to pile on",
    ),
    (
        "attack vs inflammatory",
        ("滚出去吧别恶心人", "attack", {"attack": 0.8}),
        ("两边都别装了", "inflammatory", {"inflammatory": 0.7}),
        "an insult aimed at someone vs a line that tries to start a fight",
    ),
]


def probe_items() -> list[dict]:
    """Flatten the pairs into evaluation rows."""
    rows: list[dict] = []
    for index, (focus, left, right, why) in enumerate(PAIRS, start=1):
        for side, (text, dominant, must) in (("a", left), ("b", right)):
            rows.append(
                {
                    "id": "b%02d%s" % (index, side),
                    "pair": index,
                    "focus": focus,
                    "text": text,
                    "expect_dominant": dominant,
                    "must": must,
                    "why": why,
                }
            )
    return rows


def fewshot_pairs(limit: int = 6) -> list[tuple[str, str]]:
    """Compact anchors for the prompt: one (left, right) pair per focus."""
    seen: set[str] = set()
    picked: list[tuple[str, str]] = []
    for focus, left, right, _why in PAIRS:
        if focus in seen:
            continue
        seen.add(focus)
        picked.append((left[0], right[0]))
        if len(picked) >= limit:
            break
    return picked
