import { api } from '../api.js';

export async function renderSettings(el) {
  const [data, run] = await Promise.all([
    api('/api/settings'),
    api('/api/rework_run/dash').catch(() => null),
  ]);
  const gateText = run
    ? [
        `开跑芯片 ${run.chip}`,
        `回炉中余数 ${run.remainder}`,
        `开跑落点 ${run.landing ? run.landing.length : 0} 行`,
        `读轨 ${run.active_read}`,
        `指纹 ${run.cutover_fp ? run.cutover_fp.slice(0, 12) + '…' : '空'}`,
        `拒回刷 ${run.reject_refresh ? '是' : '否'}`,
        `入库闸门 ${run.gate_allows ? '开' : '关'}`,
      ].join(' · ')
    : '开跑钉定暂不可读';
  el.innerHTML = `
    <div class="card">
      <h2>台参数</h2>
      <table>
        <thead><tr><th>项</th><th>说明</th><th>值</th><th></th></tr></thead>
        <tbody>
          ${(data.items || []).map((s) => `
            <tr>
              <td>${s.key}</td>
              <td>${s.label}</td>
              <td><input data-key="${s.key}" value="${s.value}" /></td>
              <td><button data-save="${s.key}">保存</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="card">
      <h3>开跑钉定口径</h3>
      <p class="muted">只读：开跑由回炉队列逐单落定，参数不在此处改判。</p>
      <p class="muted" id="runReadout">${gateText}</p>
    </div>`;
  el.querySelectorAll('[data-save]').forEach((btn) => {
    btn.onclick = async () => {
      const input = el.querySelector(`input[data-key="${btn.dataset.save}"]`);
      await api('/api/settings/update', {
        method: 'POST',
        body: JSON.stringify({ key: btn.dataset.save, value: input.value }),
      });
      await renderSettings(el);
    };
  });
}
