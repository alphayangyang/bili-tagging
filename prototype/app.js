(function () {
  'use strict';

  var TAGS = [
    { id: 'information', zh: '信息', en: 'information', color: '#4cc9f0' },
    { id: 'humor', zh: '幽默', en: 'humor', color: '#ffd166' },
    { id: 'emotional_resonance', zh: '共鸣', en: 'emotional resonance', color: '#f78c6b' },
    { id: 'irrelevant_repetition', zh: '无意义重复', en: 'irrelevant repetition', color: '#9aa0a6' },
    { id: 'attack', zh: '攻击', en: 'attack', color: '#ef476f' },
    { id: 'conflict', zh: '争论', en: 'conflict', color: '#ff9f1c' },
    { id: 'spoiler', zh: '剧透', en: 'spoiler', color: '#b388ff' },
    { id: 'political', zh: '政治', en: 'political stance', color: '#2ec4b6' },
    { id: 'inflammatory', zh: '引战', en: 'inflammatory', color: '#e63946' },
    { id: 'other', zh: '其他', en: 'other / unclassifiable', color: '#8d99ae' }
  ];
  var TAG_BY_ID = {};
  TAGS.forEach(function (tag) { TAG_BY_ID[tag.id] = tag; });

  var DEFAULTS = {
    information: 'show', humor: 'show', emotional_resonance: 'show',
    irrelevant_repetition: 'collapse', attack: 'hide', conflict: 'hide',
    spoiler: 'hide', political: 'show', inflammatory: 'hide', other: 'show'
  };
  var PROTECTED = { information: true, other: true };
  var LANE_HEIGHT = 36;
  var SPEED = 0.105;

  var state = {
    scenarios: [],
    index: 0,
    scenario: null,
    items: [],
    prefs: Object.assign({}, DEFAULTS),
    threshold: 0.6,
    density: { windowS: 10, maxVisible: 12, exceed: 'collapse' },
    enabled: true,
    dmOn: true,
    playing: true,
    time: 0,
    duration: 180,
    cursor: 0,
    stats: { show: 0, collapse: 0, hide: 0 },
    active: [],
    shownWindow: [],
    folded: [],
    log: [],
    laneFree: []
  };

  var el = {
    video: document.getElementById('video'),
    layer: document.getElementById('danmaku-layer'),
    play: document.getElementById('play-btn'),
    time: document.getElementById('time-label'),
    progress: document.getElementById('progress'),
    played: document.getElementById('played'),
    buffer: document.getElementById('buffer'),
    knob: document.getElementById('knob'),
    dmToggle: document.getElementById('dm-toggle'),
    enabled: document.getElementById('enabled'),
    tagList: document.getElementById('tag-list'),
    statShow: document.getElementById('stat-show'),
    statCollapse: document.getElementById('stat-collapse'),
    statHide: document.getElementById('stat-hide'),
    threshold: document.getElementById('threshold'),
    thresholdOut: document.getElementById('threshold-out'),
    window: document.getElementById('window'),
    maxVisible: document.getElementById('maxvisible'),
    exceed: document.getElementById('exceed'),
    foldedList: document.getElementById('folded-list'),
    foldedEmpty: document.getElementById('folded-empty'),
    logList: document.getElementById('log-list'),
    logEmpty: document.getElementById('log-empty'),
    tabs: document.getElementById('panel-tabs'),
    tabCount: document.getElementById('tab-count'),
    scenarioTabs: document.getElementById('scenario-tabs'),
    dataBadge: document.getElementById('data-badge'),
    posterTitle: document.getElementById('poster-title'),
    posterSub: document.getElementById('poster-sub'),
    videoTitle: document.getElementById('video-title'),
    metaSource: document.getElementById('meta-source'),
    metaDuration: document.getElementById('meta-duration'),
    metaCount: document.getElementById('meta-count'),
    pausedBadge: document.getElementById('paused-badge'),
    playerBadge: document.getElementById('player-badge-text'),
    toast: document.getElementById('toast'),
    correction: document.getElementById('correction'),
    reset: document.getElementById('reset-btn'),
    statusText: document.getElementById('status-text')
  };

  function fmtTime(ms) {
    var total = Math.max(0, Math.floor(ms / 1000));
    var m = Math.floor(total / 60);
    var s = total % 60;
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }

  function scoreOf(item) {
    var score = item.tags ? Number(item.tags[item.dominant]) : 0;
    return isFinite(score) ? score : 0;
  }

  function actionOf(item) {
    if (!state.enabled) return 'show';
    if (item.forceShow) return 'show';
    var label = item.dominant && TAG_BY_ID[item.dominant] ? item.dominant : 'other';
    var action = state.prefs[label] || 'show';
    if (action !== 'show' && scoreOf(item) < state.threshold) return 'show';
    if (action === 'show') {
      var now = state.time;
      state.shownWindow = state.shownWindow.filter(function (t) { return now - t <= state.density.windowS * 1000; });
      if (!PROTECTED[label] && state.shownWindow.length >= state.density.maxVisible && state.density.exceed !== 'show') {
        action = state.density.exceed;
      }
    }
    return action;
  }

  function updateStats() {
    el.statShow.textContent = state.stats.show;
    el.statCollapse.textContent = state.stats.collapse;
    el.statHide.textContent = state.stats.hide;
    el.tabCount.textContent = state.folded.length;
    var foldedNow = state.stats.collapse + state.stats.hide;
    el.playerBadge.textContent = foldedNow > 0
      ? (state.scenario && state.scenario.simulated ? '新标签演示' : '已折叠 ' + foldedNow + ' 条')
      : (state.scenario && state.scenario.simulated ? '新标签演示' : '过滤中');
  }

  function progressRatio() {
    return state.duration > 0 ? state.time / state.duration : 0;
  }

  function paintProgress() {
    var ratio = Math.min(1, progressRatio());
    el.played.style.width = (ratio * 100).toFixed(2) + '%';
    el.knob.style.left = (ratio * 100).toFixed(2) + '%';
    el.time.textContent = fmtTime(state.time) + ' / ' + fmtTime(state.duration);
  }

  function tagBadge(label) {
    var tag = TAG_BY_ID[label] || TAG_BY_ID.other;
    var span = document.createElement('span');
    span.className = 'tag-badge';
    span.textContent = tag.zh;
    span.style.color = tag.color;
    span.style.background = hexAlpha(tag.color, 0.14);
    span.style.borderColor = hexAlpha(tag.color, 0.35);
    return span;
  }

  function hexAlpha(hex, alpha) {
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
  }

  function toast(message) {
    el.toast.textContent = message;
    el.toast.hidden = false;
    el.toast.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(function () {
      el.toast.classList.remove('show');
      setTimeout(function () { el.toast.hidden = true; }, 200);
    }, 2200);
  }

  function buildTagList() {
    el.tagList.innerHTML = '';
    TAGS.forEach(function (tag) {
      var row = document.createElement('div');
      row.className = 'tag-row';

      var dot = document.createElement('span');
      dot.className = 'tag-dot';
      dot.style.background = tag.color;

      var name = document.createElement('div');
      name.className = 'tag-name';
      var zh = document.createElement('b');
      zh.textContent = tag.zh;
      var en = document.createElement('span');
      en.textContent = tag.en;
      name.appendChild(zh);
      name.appendChild(en);

      var count = document.createElement('span');
      count.className = 'tag-count';
      count.textContent = state.items.filter(function (it) { return it.dominant === tag.id; }).length;

      var seg = document.createElement('div');
      seg.className = 'seg';
      [['show', '显'], ['collapse', '折'], ['hide', '隐']].forEach(function (pair) {
        var b = document.createElement('button');
        b.type = 'button';
        b.textContent = pair[1];
        b.title = tag.zh + '：' + { show: '显示', collapse: '折叠', hide: '隐藏' }[pair[0]];
        b.dataset.label = tag.id;
        b.dataset.action = pair[0];
        b.addEventListener('click', function () {
          state.prefs[tag.id] = pair[0];
          syncSeg();
          applyChange('「' + tag.zh + '」策略改为' + { show: '显示', collapse: '折叠', hide: '隐藏' }[pair[0]]);
        });
        seg.appendChild(b);
      });

      row.appendChild(dot);
      row.appendChild(name);
      row.appendChild(count);
      row.appendChild(seg);
      el.tagList.appendChild(row);
    });
    syncSeg();
  }

  function syncSeg() {
    var buttons = el.tagList.querySelectorAll('.seg button');
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      var on = state.prefs[b.dataset.label] === b.dataset.action;
      b.classList.toggle('on-' + b.dataset.action, on);
    }
  }

  function applyChange(message) {
    state.folded = [];
    state.stats = { show: 0, collapse: 0, hide: 0 };
    state.shownWindow = [];
    state.active.forEach(function (a) { a.el.remove(); });
    state.active = [];
    state.cursor = firstIndexAt(state.time);
    state.items.forEach(function (it) { it.forceShow = false; });
    updateStats();
    renderFolded();
    if (message) toast(message + ' · 已从当前进度重新应用');
  }

  function firstIndexAt(time) {
    var items = state.items;
    var lo = 0, hi = items.length;
    while (lo < hi) {
      var mid = (lo + hi) >> 1;
      if (items[mid].t < time - 500) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  function laneCount() {
    var height = el.video.clientHeight || 420;
    return Math.max(3, Math.floor((height * 0.76) / LANE_HEIGHT));
  }

  function spawn(item) {
    var width = Math.max(60, item.text.length * 13 + 20);
    var lanes = laneCount();
    var best = 0;
    var bestFree = Infinity;
    for (var i = 0; i < lanes; i++) {
      var free = state.laneFree[i] || 0;
      if (free <= state.time) { best = i; bestFree = free; break; }
      if (free < bestFree) { bestFree = free; best = i; }
    }
    var gap = Math.round(width / SPEED) + 220;
    state.laneFree[best] = state.time + gap;

    var node = document.createElement('div');
    node.className = 'dm' + (item.dominant === 'attack' || item.dominant === 'inflammatory' ? ' dm-accent' : '');
    node.textContent = item.text;
    el.layer.appendChild(node);

    var videoWidth = el.video.clientWidth || 800;
    var record = {
      el: node,
      item: item,
      startX: videoWidth,
      y: 14 + best * LANE_HEIGHT,
      speed: SPEED + Math.random() * 0.008,
      spawnTime: state.time,
      width: width
    };
    node.style.top = record.y + 'px';
    node.style.transform = 'translateX(' + record.startX + 'px)';
    state.active.push(record);
  }

  function addFolded(item, action) {
    state.folded.unshift({ item: item, action: action, at: state.time });
    if (state.folded.length > 80) state.folded.pop();
    renderFolded();
  }

  function renderFolded() {
    el.foldedList.innerHTML = '';
    el.foldedEmpty.hidden = state.folded.length > 0;
    state.folded.forEach(function (entry) {
      var card = document.createElement('div');
      card.className = 'folded';

      var top = document.createElement('div');
      top.className = 'folded-top';
      var t = document.createElement('span');
      t.className = 't';
      t.textContent = fmtTime(entry.item.t);
      top.appendChild(t);
      top.appendChild(tagBadge(entry.item.dominant));
      var kind = document.createElement('span');
      kind.textContent = entry.action === 'hide' ? '已隐藏' : '已折叠';
      kind.style.color = entry.action === 'hide' ? '#ff9dab' : '#ffd166';
      kind.style.fontSize = '10.5px';
      top.appendChild(kind);
      var score = document.createElement('span');
      score.className = 'folded-score';
      score.textContent = scoreOf(entry.item).toFixed(2);
      top.appendChild(score);
      card.appendChild(top);

      var text = document.createElement('div');
      text.className = 'folded-text';
      text.textContent = entry.item.text;
      text.style.opacity = entry.action === 'hide' ? '0.55' : '1';
      card.appendChild(text);

      var acts = document.createElement('div');
      acts.className = 'folded-acts';
      var expand = document.createElement('button');
      expand.type = 'button';
      expand.className = 'act-btn';
      expand.textContent = '展开原文';
      expand.addEventListener('click', function () { revealEntry(entry); });
      var fix = document.createElement('button');
      fix.type = 'button';
      fix.className = 'act-btn';
      fix.textContent = '改标签';
      fix.addEventListener('click', function (event) { openCorrection(entry, event.currentTarget); });
      acts.appendChild(expand);
      acts.appendChild(fix);
      card.appendChild(acts);

      el.foldedList.appendChild(card);
    });
    el.tabCount.textContent = state.folded.length;
  }

  function revealEntry(entry) {
    entry.item.forceShow = true;
    state.folded = state.folded.filter(function (e) { return e !== entry; });
    renderFolded();
    spawn(entry.item);
    state.stats.show += 1;
    updateStats();
    addLog('展开', entry.item.dominant, entry.item.dominant, entry.item.text);
    toast('已临时展开：「' + entry.item.text + '」');
  }

  function addLog(verb, from, to, text) {
    state.log.unshift({ verb: verb, from: from, to: to, text: text, at: state.time });
    if (state.log.length > 40) state.log.pop();
    renderLog();
  }

  function renderLog() {
    el.logList.innerHTML = '';
    el.logEmpty.hidden = state.log.length > 0;
    state.log.forEach(function (row) {
      var item = document.createElement('div');
      item.className = 'log-item';
      var lt = document.createElement('span');
      lt.className = 'lt';
      lt.textContent = fmtTime(row.at);
      item.appendChild(lt);
      if (row.verb === '展开') {
        var b1 = document.createElement('b');
        b1.textContent = '展开原文';
        item.appendChild(b1);
        var rest = document.createElement('span');
        rest.textContent = '「' + row.text + '」';
        rest.style.color = '#9aa3b2';
        item.appendChild(rest);
      } else {
        var from = tagBadge(row.from);
        item.appendChild(from);
        var arrow = document.createElement('span');
        arrow.className = 'arrow';
        arrow.textContent = '→';
        item.appendChild(arrow);
        var to = tagBadge(row.to);
        item.appendChild(to);
      }
      var ok = document.createElement('span');
      ok.className = 'ok';
      ok.textContent = '已记录';
      item.appendChild(ok);
      el.logList.appendChild(item);
    });
  }

  function openCorrection(entry, anchor) {
    el.correction.innerHTML = '';
    el.correction.hidden = false;
    var title = document.createElement('p');
    title.className = 'pop-title';
    title.textContent = '这条其实属于：';
    el.correction.appendChild(title);
    var grid = document.createElement('div');
    grid.className = 'pop-grid';
    TAGS.forEach(function (tag) {
      var b = document.createElement('button');
      b.type = 'button';
      var dot = document.createElement('i');
      dot.style.background = tag.color;
      b.appendChild(dot);
      b.appendChild(document.createTextNode(tag.zh));
      b.addEventListener('click', function () {
        closeCorrection();
        correctEntry(entry, tag.id);
      });
      grid.appendChild(b);
    });
    el.correction.appendChild(grid);

    var rect = anchor.getBoundingClientRect();
    var width = 250;
    var top = rect.bottom + 8;
    var left = Math.min(window.innerWidth - width - 16, Math.max(16, rect.left - 120));
    if (top + 260 > window.innerHeight) top = Math.max(16, rect.top - 268);
    el.correction.style.top = top + 'px';
    el.correction.style.left = left + 'px';
  }

  function closeCorrection() {
    el.correction.hidden = true;
  }

  function correctEntry(entry, label) {
    var item = entry.item;
    var from = item.dominant;
    if (from === label) { toast('标签未变化'); return; }
    item.dominant = label;
    item.tags = {};
    item.tags[label] = 0.9;
    item.forceShow = false;
    state.folded = state.folded.filter(function (e) { return e !== entry; });
    addLog('纠正', from, label, item.text);
    if (actionOf(item) === 'show') {
      spawn(item);
      state.stats.show += 1;
      updateStats();
      renderFolded();
      toast('已记录：' + TAG_BY_ID[from].zh + ' → ' + TAG_BY_ID[label].zh);
    } else {
      state.folded.unshift({ item: item, action: actionOf(item), at: state.time });
      renderFolded();
      toast('已记录：' + TAG_BY_ID[from].zh + ' → ' + TAG_BY_ID[label].zh + '（仍不在显示策略内）');
    }
    buildTagList();
  }

  function tick(dt) {
    if (!state.playing) return;
    state.time += dt;
    if (state.time >= state.duration) {
      state.time = state.duration;
      setPlaying(false);
    }
    while (state.cursor < state.items.length && state.items[state.cursor].t <= state.time) {
      var item = state.items[state.cursor];
      state.cursor += 1;
      var action = actionOf(item);
      if (action === 'show') {
        state.stats.show += 1;
        if (state.dmOn) {
          spawn(item);
          state.shownWindow.push(state.time);
        }
      } else if (action === 'collapse') {
        state.stats.collapse += 1;
        addFolded(item, 'collapse');
      } else {
        state.stats.hide += 1;
        addFolded(item, 'hide');
      }
    }
    updateStats();
    paintProgress();
  }

  function frame(now) {
    if (!state.lastTs) state.lastTs = now;
    var dt = Math.min(120, now - state.lastTs);
    state.lastTs = now;
    tick(dt);

    var videoWidth = el.video.clientWidth || 800;
    state.active = state.active.filter(function (a) {
      var left = a.startX - (state.time - a.spawnTime) * a.speed;
      if (left + a.width < 0 || left > videoWidth + 400) {
        a.el.remove();
        return false;
      }
      a.el.style.transform = 'translateX(' + left.toFixed(1) + 'px)';
      return true;
    });
    requestAnimationFrame(frame);
  }

  function setPlaying(next) {
    state.playing = next;
    el.video.classList.toggle('playing', next);
    el.play.textContent = next ? '❚❚' : '▶';
    el.pausedBadge.hidden = next;
  }

  function resetPlayback() {
    state.time = 0;
    state.cursor = 0;
    state.stats = { show: 0, collapse: 0, hide: 0 };
    state.folded = [];
    state.log = [];
    state.shownWindow = [];
    state.laneFree = [];
    state.active.forEach(function (a) { a.el.remove(); });
    state.active = [];
    el.layer.innerHTML = '';
    state.items.forEach(function (it) { it.forceShow = false; });
    updateStats();
    renderFolded();
    renderLog();
    paintProgress();
  }

  function loadScenario(index) {
    state.index = index;
    state.scenario = state.scenarios[index];
    state.items = state.scenario.items.map(function (item) {
      return {
        t: item.t,
        text: item.text,
        dominant: item.dominant,
        tags: Object.assign({}, item.tags || {}),
        evidence: item.evidence || '',
        forceShow: false
      };
    });
    state.duration = state.scenario.duration * 1000;
    state.prefs = Object.assign({}, DEFAULTS);
    state.enabled = true;
    el.enabled.checked = true;
    buildTagList();
    resetPlayback();
    el.posterTitle.textContent = state.scenario.title;
    el.posterSub.textContent = state.scenario.simulated ? '模拟数据 · 用于展示新标签' : '真实弹幕 · deepseek-flash 打标';
    el.videoTitle.textContent = state.scenario.title;
    el.metaSource.textContent = state.scenario.source;
    el.metaDuration.textContent = fmtTime(state.duration);
    el.metaCount.textContent = state.scenario.simulated ? '模拟播放 46.2 万 · 弹幕 1,283' : '播放 128.4 万 · 弹幕 2.1 万';
    el.dataBadge.textContent = state.scenario.simulated ? '模拟数据' : '真实数据 · 113/1407';
    el.dataBadge.classList.toggle('sim', !!state.scenario.simulated);
    el.statusText.textContent = state.scenario.simulated ? '本地服务 · 模拟数据' : '本地服务 · 演示模式';
    el.playerBadge.textContent = state.scenario.simulated ? '新标签演示' : '过滤中';

    var buttons = el.scenarioTabs.querySelectorAll('.tab-btn');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.toggle('active', Number(buttons[i].dataset.index) === index);
    }
    setPlaying(true);
    state.lastTs = 0;
  }

  function buildScenarioTabs() {
    el.scenarioTabs.innerHTML = '';
    state.scenarios.forEach(function (scenario, index) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'tab-btn' + (index === 0 ? ' active' : '');
      b.dataset.index = String(index);
      b.textContent = scenario.simulated ? '② ' + scenario.title : '① ' + scenario.title;
      b.addEventListener('click', function () { loadScenario(index); });
      el.scenarioTabs.appendChild(b);
    });
  }

  function bindEvents() {
    el.play.addEventListener('click', function () {
      if (state.time >= state.duration) resetPlayback();
      setPlaying(!state.playing);
    });
    el.dmToggle.addEventListener('click', function () {
      state.dmOn = !state.dmOn;
      el.dmToggle.textContent = '弹幕 ' + (state.dmOn ? '开' : '关');
      el.dmToggle.classList.toggle('off', !state.dmOn);
      if (!state.dmOn) {
        state.active.forEach(function (a) { a.el.remove(); });
        state.active = [];
      }
      toast(state.dmOn ? '弹幕已开启' : '弹幕已关闭（过滤仍在计数）');
    });
    el.enabled.addEventListener('change', function () {
      state.enabled = el.enabled.checked;
      applyChange(state.enabled ? '过滤已开启' : '过滤已关闭');
    });
    el.threshold.addEventListener('input', function () {
      state.threshold = Number(el.threshold.value);
      el.thresholdOut.textContent = state.threshold.toFixed(2);
      applyChange('阈值改为 ' + state.threshold.toFixed(2));
    });
    el.window.addEventListener('change', function () {
      state.density.windowS = Math.max(2, Number(el.window.value) || 10);
      applyChange('密度窗口改为 ' + state.density.windowS + ' 秒');
    });
    el.maxVisible.addEventListener('change', function () {
      state.density.maxVisible = Math.max(1, Number(el.maxVisible.value) || 12);
      applyChange('窗口上限改为 ' + state.density.maxVisible + ' 条');
    });
    el.exceed.addEventListener('change', function () {
      state.density.exceed = el.exceed.value;
      applyChange('超限动作改为' + { collapse: '折叠', hide: '隐藏', show: '放行' }[state.density.exceed]);
    });
    el.reset.addEventListener('click', function () {
      state.prefs = Object.assign({}, DEFAULTS);
      state.threshold = 0.6;
      el.threshold.value = '0.6';
      el.thresholdOut.textContent = '0.60';
      state.density = { windowS: 10, maxVisible: 12, exceed: 'collapse' };
      el.window.value = '10';
      el.maxVisible.value = '12';
      el.exceed.value = 'collapse';
      state.enabled = true;
      el.enabled.checked = true;
      state.dmOn = true;
      el.dmToggle.textContent = '弹幕 开';
      el.dmToggle.classList.remove('off');
      buildTagList();
      resetPlayback();
      setPlaying(true);
      toast('已恢复默认设置');
    });
    el.tabs.addEventListener('click', function (event) {
      var button = event.target.closest('.tab');
      if (!button) return;
      var view = button.dataset.view;
      Array.prototype.forEach.call(el.tabs.querySelectorAll('.tab'), function (tab) {
        tab.classList.toggle('active', tab === button);
      });
      ['tags', 'folded', 'log'].forEach(function (name) {
        document.getElementById('view-' + name).hidden = name !== view;
      });
    });
    el.progress.addEventListener('click', function (event) {
      var rect = el.progress.getBoundingClientRect();
      var ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      state.time = ratio * state.duration;
      state.cursor = firstIndexAt(state.time);
      state.stats = { show: 0, collapse: 0, hide: 0 };
      state.folded = [];
      state.shownWindow = [];
      state.laneFree = [];
      state.active.forEach(function (a) { a.el.remove(); });
      state.active = [];
      renderFolded();
      paintProgress();
    });
    document.addEventListener('click', function (event) {
      if (!el.correction.hidden && !el.correction.contains(event.target) && !event.target.classList.contains('act-btn')) {
        closeCorrection();
      }
    });
    document.addEventListener('keydown', function (event) {
      if (event.code === 'Space') {
        event.preventDefault();
        if (state.time >= state.duration) resetPlayback();
        setPlaying(!state.playing);
      }
    });
  }

  function boot() {
    bindEvents();
    el.thresholdOut.textContent = '0.60';
    fetch('data/demo.json')
      .then(function (res) { return res.json(); })
      .then(function (data) {
        state.scenarios = data.scenarios;
        buildScenarioTabs();
        loadScenario(0);
        requestAnimationFrame(frame);
      })
      .catch(function (error) {
        el.dataBadge.textContent = '数据加载失败';
        console.error(error);
      });
  }

  boot();
})();
