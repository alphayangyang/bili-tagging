
import json, random, re, pathlib, datetime

ROOT = pathlib.Path('/home/alphayang/bili-tagging')
cache = json.loads((ROOT/'data/tags/BV1GJ411x7h7.json').read_text(encoding='utf-8'))
items = list(cache['items'].values())
print('total', len(items))

def clean(text):
    t = re.sub(r'\s+', ' ', text).strip()
    return t

pool = []
seen = set()
for it in items:
    t = clean(it['text'])
    if not (2 <= len(t) <= 18):
        continue
    if t in seen:
        continue
    seen.add(t)
    pool.append({
        't': it['time_ms'],
        'text': t,
        'dominant': it.get('dominant'),
        'tags': it.get('tags') or {},
        'evidence': it.get('evidence') or '',
    })
print('pool', len(pool))

quota = {'humor': 120, 'emotional_resonance': 44, 'information': 44,
         'irrelevant_repetition': 26, 'spoiler': 6, 'other': 8, 'conflict': 2}
random.seed(20261104)
picked = []
for label, n in quota.items():
    cand = [p for p in pool if p['dominant'] == label]
    random.shuffle(cand)
    picked.extend(cand[:n])
print('picked', len(picked))
picked.sort(key=lambda p: p['t'])

# Simulated second video: political / inflammatory / attack / conflict heavy.
sim = [
 ("这政策早该改了", 2.0, 'political'), ("懂的都懂", 6.5, 'political'),
 ("又开始赢麻了", 11.0, 'political'), ("这话说得没毛病", 15.5, 'political'),
 ("别光盯着一个角度吧", 19.0, 'political'), ("数据呢？张口就来", 23.5, 'conflict'),
 ("两边都别装了", 28.0, 'inflammatory'), ("这也能洗？", 32.0, 'inflammatory'),
 ("不服来辩", 36.5, 'inflammatory'), ("笑死，逻辑呢", 40.0, 'humor'),
 ("这条楼里全是节奏", 44.0, 'inflammatory'), ("我先蹲个后续", 48.0, 'other'),
 ("前面说得对，别被带节奏", 52.0, 'political'), ("这就开始扣帽子了？", 56.0, 'attack'),
 ("你连原文都没看", 60.0, 'attack'), ("理性讨论不行吗", 64.0, 'conflict'),
 ("这段确实有信息量", 68.0, 'information'), ("哈哈哈哈哈", 72.0, 'humor'),
 ("说到心坎里了", 76.0, 'emotional_resonance'), ("1", 80.0, 'irrelevant_repetition'),
 ("蹲一个课代表", 84.0, 'other'), ("这波站他", 88.0, 'political'),
 ("节奏狗滚出去", 92.0, 'attack'), ("阴阳怪气什么呢", 96.0, 'inflammatory'),
 ("有本事拿数据说话", 100.0, 'conflict'), ("这也能扯到立场", 104.0, 'political'),
 ("笑不活了", 108.0, 'humor'), ("泪目", 112.0, 'emotional_resonance'),
 ("重复刷屏的能不能停", 116.0, 'irrelevant_repetition'), ("其实是剪辑问题", 120.0, 'information'),
 ("别吵了别吵了", 124.0, 'conflict'), ("赢了赢了行了吧", 128.0, 'political'),
 ("这标题就带节奏", 132.0, 'inflammatory'), ("直接举报", 136.0, 'attack'),
 ("讲道理这段挺客观", 140.0, 'information'), ("呜呜呜", 144.0, 'emotional_resonance'),
 ("666", 148.0, 'irrelevant_repetition'), ("细思极恐", 152.0, 'other'),
 ("这也能上热门", 156.0, 'political'), ("兄弟冷静", 160.0, 'conflict'),
 ("我是来看弹幕的", 164.0, 'humor'), ("前排出售瓜子", 168.0, 'irrelevant_repetition'),
 ("说得好", 172.0, 'emotional_resonance'), ("证据链呢", 176.0, 'information'),
]
sim_items = []
for text, sec, label in sim:
    sim_items.append({
        't': int(sec * 1000),
        'text': text,
        'dominant': label,
        'tags': {label: 0.55 + (0.35 if label in ('attack','inflammatory') else 0.2)},
        'evidence': '',
    })

dist = {}
for p in picked:
    dist[p['dominant']] = dist.get(p['dominant'], 0) + 1

out = {
    'generated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'note': 'Scenario A: 1407 real danmaku of BV1GJ411x7h7 tagged by deepseek-flash; a stratified sample is shown. Scenario B is synthetic, written to exercise political / inflammatory / attack tags.',
    'scenarios': [
        {
            'id': 'music',
            'title': '80年代迪厅必放金曲（真实数据抽样）',
            'bvid': 'BV1GJ411x7h7',
            'duration': 213,
            'simulated': False,
            'source': 'deepseek-flash 离线打标 · 1,407 条中分层抽 %d 条' % len(picked),
            'distribution': dist,
            'items': picked,
        },
        {
            'id': 'debate',
            'title': '时政 / 对喷切片（模拟数据）',
            'bvid': 'BV1xx411c7mD',
            'duration': 180,
            'simulated': True,
            'source': '人工构造 %d 条，用于展示政治 / 引战 / 攻击标签与折叠记录' % len(sim_items),
            'distribution': {},
            'items': sim_items,
        },
    ],
}
dest = ROOT/'prototype/data/demo.json'
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print('wrote', dest, dest.stat().st_size, 'bytes')
print(dist)
