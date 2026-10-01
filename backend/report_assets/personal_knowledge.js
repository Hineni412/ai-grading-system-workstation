(() => {
  const root = document.querySelector('.knowledge-map');
  if (!root) return;
  const data = JSON.parse(root.querySelector('.kn-data').textContent);
  const nodes = new Map(data.nodes.map(node => [node.key, node]));
  const buttons = new Map([...root.querySelectorAll('.kn-node')].map(button => [button.dataset.key, button]));
  const inspector = root.querySelector('.kn-inspector');
  let active = '';
  const number = value => Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)));
  const percent = value => number(Math.floor(value * 1000) / 10);
  function element(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  }
  const edgesFor = key => data.edges.filter(edge => edge.topic_key === key || edge.skill_key === key)
    .sort((a,b) => b.same_part_question_count - a.same_part_question_count || b.question_count - a.question_count);
  function draw() {
    root.querySelectorAll('.kn-board').forEach(board => {
      const svg = board.querySelector('.kn-lines');
      svg.replaceChildren();
      const bounds = board.getBoundingClientRect();
      svg.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`);
      for (const edge of edgesFor(active)) {
        const left = buttons.get(edge.topic_key), right = buttons.get(edge.skill_key);
        if (!left || !right || !board.contains(left) || !board.contains(right)) continue;
        const l = left.getBoundingClientRect(), r = right.getBoundingClientRect();
        const x1 = l.right - bounds.left, y1 = l.top + l.height / 2 - bounds.top;
        const x2 = r.left - bounds.left, y2 = r.top + r.height / 2 - bounds.top;
        const mid = (x1 + x2) / 2;
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`);
        if (!edge.same_part_question_count) path.setAttribute('class', 'is-cooccurrence');
        svg.append(path);
      }
    });
  }
  function select(key, toggle = true) {
    active = toggle && active === key ? '' : key;
    const edges = edgesFor(active);
    const related = new Set(edges.flatMap(edge => [edge.topic_key, edge.skill_key]));
    buttons.forEach((button, id) => {
      button.classList.toggle('is-selected', id === active);
      button.classList.toggle('is-related', id !== active && related.has(id));
      button.setAttribute('aria-pressed', String(id === active));
    });
    root.querySelectorAll('.kn-board').forEach(board => board.classList.toggle('has-focus', !!active && !!board.querySelector('.is-selected,.is-related')));
    inspector.replaceChildren();
    const node = nodes.get(active);
    if (!node) {
      inspector.append(element('p', '点选上方节点，查看相关题目与证据。'));
      draw(); return;
    }
    inspector.append(element('h3', `${node.kind === 'skill' ? '技能点' : '知识点'} · ${node.label}`));
    if (data.mode === 'class') {
      inspector.append(element('p', node.mastery == null
        ? '全班该节点证据不足，暂不显示平均掌握度。'
        : `有证据学生平均掌握度 ${percent(node.mastery)}% · ${node.coverage} / ${node.student_count} 人有证据 · 截至 ${data.as_of}`));
      const labels = [['weak', '明显薄弱'], ['unsteady', '还不稳'], ['stable', '较稳定'], ['insufficient', '证据不足']];
      inspector.append(element('p', labels.map(([key, label]) => `${label} ${node.distribution[key]} 人`).join(' · ')));
      inspector.append(element('p', `本次考试相关小问整体得分率 ${percent(node.score / node.full)}%。平均掌握度与本卷得分率分别计算。`));
    } else {
      inspector.append(element('p', node.mastery == null
      ? '当前掌握度：证据不足，暂不判定为 0%。'
      : `${({stable:'较稳定',unsteady:'还不稳',weak:'明显薄弱',insufficient:'证据不足'})[node.tier] || '证据不足'} · 当前掌握度 ${percent(node.mastery)}%${node.interval_low != null && node.interval_high != null ? `（${percent(node.interval_low)}%–${percent(node.interval_high)}%）` : ''} · 作答 ${node.observation_count || 0} 处、全对 ${node.full_correct_count || 0} 处${node.recent_trend ? ` · ${node.recent_trend}` : ''} · 截至 ${data.as_of}`));
      inspector.append(element('p', `本次考试相关小问：${number(node.score)} / ${number(node.full)} 分（得分率 ${Math.round(node.score / node.full * 100)}%）。`));
      if (node.tier === 'insufficient') {
        for (const reference of node.parent_references || []) {
          const tier = ({stable:'较稳定',unsteady:'还不稳',weak:'明显薄弱',insufficient:'证据不足'})[reference.tier] || '证据不足';
          inspector.append(element('p', `上级参考 · ${reference.label}：${tier} · ${percent(reference.mastery)}%${reference.interval_low != null && reference.interval_high != null ? `（${percent(reference.interval_low)}%–${percent(reference.interval_high)}%）` : ''}`));
        }
      }
    }
    const questionList = element('div', undefined, 'kn-question-list');
    for (const question of node.questions) {
      const link = element('a', data.mode === 'class'
        ? `${question.label} 均分 ${number(question.score)}/${number(question.full)} · ${question.count} 人`
        : `${question.label} ${number(question.score)}/${number(question.full)}`);
      link.href = '#' + (data.mode !== 'class' && question.lost ? 'question-' : 'score-') + question.id;
      questionList.append(link);
    }
    inspector.append(questionList);
    inspector.append(element('p', node.step_questions.length
      ? `本卷 ${node.step_questions.length} 个相关小问有步骤层面的证据。上面的本卷分数仍是小问整体分，不是该技能的独立分。`
      : '本卷相关分数属于小问整体表现，不能据此断定每个步骤是否达成。', 'kn-caution'));
    if (edges.length) {
      inspector.append(element('p', '关联依据（来自已确认题库内容）：'));
      const list = element('div', undefined, 'kn-related-list');
      for (const edge of edges) {
        const otherKey = edge.topic_key === active ? edge.skill_key : edge.topic_key;
        const other = nodes.get(otherKey);
        const label = edge.same_part_question_count
          ? `${edge.same_part_question_count} 道有同小问依据`
          : `${edge.question_count} 道仅同题出现`;
        const button = element('button', `${other.label} · ${label}`);
        button.type = 'button';
        button.addEventListener('click', () => { select(otherKey, false); buttons.get(otherKey).focus({preventScroll:true}); });
        list.append(button);
      }
      inspector.append(list);
    } else inspector.append(element('p', '本卷展示范围内暂无已确认关联，保留独立显示。', 'kn-evidence'));
    draw();
  }
  buttons.forEach((button, key) => button.addEventListener('click', () => select(key)));
  const initial = data.nodes.find(node => node.kind === 'topic' && edgesFor(node.key).some(edge => edge.same_part_question_count > 0));
  if (initial) select(initial.key, false);
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(draw).observe(root);
  else window.addEventListener('resize', draw);
  document.fonts?.ready.then(draw);
})();
