import { api } from '../api.js';
import { navigate } from '../nav.js';

export async function renderBins(el) {
  const [bins, rows, run] = await Promise.all([
    api('/api/bins'),
    api('/api/rows'),
    api('/api/rework_run/dash'),
  ]);
  el.innerHTML = `
    <div class="card">
      <div class="row">
        <h2>隔离舱</h2>
        <span class="chip ${run.chip === run.ok_chip ? 'ok' : 'bad'}" title="开跑芯片">开跑 ${run.chip}</span>
        <span class="chip ${run.remainder ? 'bad' : 'ok'}" title="回炉中余数">回炉中余数 ${run.remainder}</span>
        <button class="ghost" id="toPolicies" title="前往入舱规则">策略页</button>
      </div>
      <table>
        <thead><tr><th>舱号</th><th>标题</th><th>状态</th><th>占用/容量</th><th>待复核</th><th>回炉中</th><th>策略</th></tr></thead>
        <tbody>
          ${(bins.items || []).map((b) => `
            <tr>
              <td>${b.bin_code}</td>
              <td>${b.title}</td>
              <td><span class="chip">${b.status}</span></td>
              <td>${b.row_count}/${b.capacity}</td>
              <td>${b.pending_count}</td>
              <td>${b.rework_count || 0}</td>
              <td><button class="ghost" data-policy="${b.bin_code}">策略页</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="card">
      <h2>舱内行</h2>
      <table>
        <thead><tr><th>舱</th><th>条目键</th><th>质量</th><th>原因</th><th>状态</th></tr></thead>
        <tbody>
          ${(rows.items || []).map((r) => `
            <tr>
              <td>${r.bin_code}</td>
              <td>${r.item_key}</td>
              <td>${r.quality}</td>
              <td>${r.reason}</td>
              <td><span class="chip">${r.status}</span></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
  const toPolicy = () => navigate('policies');
  el.querySelector('#toPolicies').onclick = toPolicy;
  el.querySelectorAll('[data-policy]').forEach((btn) => {
    btn.onclick = toPolicy;
  });
}
