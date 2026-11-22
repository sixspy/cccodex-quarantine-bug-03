import { api } from '../api.js';
import { navigate } from '../nav.js';

function landingTable(landing) {
  if (!landing || !landing.length) {
    return '<tr><td colspan="4" class="muted empty">开跑落点尚未钉定</td></tr>';
  }
  return landing.map((l) => `
    <tr>
      <td>${l.job_code}</td>
      <td>${l.item_key}</td>
      <td><code title="${l.fingerprint}">${l.fingerprint.slice(0, 12)}…</code></td>
      <td>${l.settled_at}</td>
    </tr>`).join('');
}

export async function renderRework(el) {
  const [data, run] = await Promise.all([
    api('/api/rework/jobs'),
    api('/api/rework_run/dash'),
  ]);
  const chipClass = run.chip === run.ok_chip ? 'ok' : (run.chip === run.waiting_chip ? 'bad' : '');
  el.innerHTML = `
    <div class="card">
      <h2>回炉队列</h2>
      <div class="stat-strip">
        <div class="stat" id="runChipWrap">
          <span class="muted">开跑芯片</span>
          <b><span class="chip ${chipClass}" id="runChip">${run.chip}</span></b>
        </div>
        <div class="stat">
          <span class="muted">回炉中余数</span>
          <b id="reworkRemainder">${run.remainder}</b>
        </div>
        <div class="stat">
          <span class="muted">开跑落点</span>
          <b id="runLandingCount">${run.landing ? run.landing.length : 0}</b>
        </div>
      </div>
      <p class="muted" id="runDetail">
        读轨 ${run.active_read} · 指纹 ${run.cutover_fp ? run.cutover_fp.slice(0, 12) + '…' : '空'}
        · 拒回刷 ${run.reject_refresh ? '是' : '否'} · 落点对齐 ${run.fingerprint_ok ? '是' : '否'}
        · 计数对齐 ${run.count_ok ? '是' : '否'} · 入库闸门 ${run.gate_allows ? '开' : '关'}
      </p>
      <div id="runMsg" class="muted"></div>
    </div>
    <div class="card">
      <h3>开跑落点</h3>
      <table id="landingTable">
        <thead><tr><th>回炉单</th><th>条目键</th><th>指纹</th><th>落定时刻</th></tr></thead>
        <tbody>${landingTable(run.landing)}</tbody>
      </table>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>任务</th><th>条目</th><th>抓取目录</th><th>舱</th><th>状态</th><th>次数</th><th>操作</th></tr></thead>
        <tbody>
          ${(data.items || []).map((j) => {
            const pinned = (run.landing || []).find((l) => l.job_id === j.id);
            return `
            <tr>
              <td>${j.job_code}</td>
              <td>${j.item_key}</td>
              <td>
                <button class="ghost" data-batch="${j.batch_id}">${j.batch_code}</button>
                ${pinned ? '<span class="chip ok">已开跑</span>' : '<span class="chip bad">未开跑</span>'}
              </td>
              <td>${j.bin_code}</td>
              <td><span class="chip">${j.status}</span></td>
              <td>${j.attempt}</td>
              <td class="row">
                ${['排队','失败'].includes(j.status) ? `
                  <button data-ok="${j.id}">执行成功</button>
                  <button data-bad="${j.id}">执行失败</button>
                ` : (j.message || '')}
              </td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>`;

  const msg = el.querySelector('#runMsg');
  const runOnce = async (id, succeed) => {
    msg.textContent = '';
    try {
      await api(`/api/rework/jobs/${id}/run`, {
        method: 'POST', body: JSON.stringify({ succeed }),
      });
    } catch (e) {
      msg.textContent = `执行失败：${e.message}`;
    }
    await renderRework(el);
  };
  el.querySelectorAll('[data-ok]').forEach((btn) => {
    btn.onclick = () => runOnce(btn.dataset.ok, true);
  });
  el.querySelectorAll('[data-bad]').forEach((btn) => {
    btn.onclick = () => runOnce(btn.dataset.bad, false);
  });
  el.querySelector('#runChipWrap').onclick = () => navigate('settings');
  el.querySelectorAll('[data-batch]').forEach((btn) => {
    btn.onclick = () => navigate('captures');
  });
}
