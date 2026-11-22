import { api } from '../api.js';
import { navigate } from '../nav.js';

export async function renderCaptures(el) {
  const [data, bins, run] = await Promise.all([
    api('/api/batches'),
    api('/api/bins'),
    api('/api/rework_run/dash'),
  ]);
  const openBin = (bins.items || []).find((b) => b.status === '开放');
  el.innerHTML = `
    <div class="card">
      <h2>抓取批次</h2>
      <div class="row">
        <span class="chip ${run.chip === run.ok_chip ? 'ok' : 'bad'}" id="runChip"
              title="开跑芯片">开跑 ${run.chip}</span>
        <span class="chip ${run.remainder ? 'bad' : 'ok'}" id="reworkRemainder"
              title="回炉中余数">回炉中余数 ${run.remainder}</span>
        <span class="chip ${run.gate_allows ? 'ok' : 'bad'}" id="ingestGate"
              title="清洗入库闸门">入库闸门 ${run.gate_allows ? '开' : '关'}</span>
      </div>
      <div class="row">
        <input id="src" placeholder="来源名" />
        <input id="note" placeholder="备注" />
        <button id="create">新建批次</button>
      </div>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>批号</th><th>来源</th><th>状态</th><th>条目/坏件</th><th>操作</th></tr></thead>
        <tbody>
          ${(data.items || []).map((b) => `
            <tr>
              <td>${b.batch_code}</td>
              <td>${b.source_name}</td>
              <td><span class="chip">${b.status}</span></td>
              <td>${b.item_count} / <span class="chip bad">${b.bad_count}</span></td>
              <td class="row">
                <button data-open="${b.id}">详情</button>
                <button data-close="${b.id}">关闭</button>
                ${openBin ? `<button data-auto="${b.id}">坏件入舱</button>` : ''}
                ${openBin ? `<button data-ingest="${b.id}">清洗入库</button>` : ''}
              </td>
            </tr>`).join('')}
        </tbody>
      </table>
      <div id="detail" class="grid" style="margin-top:12px"></div>
      <div id="ingestMsg" class="muted"></div>
    </div>`;
  el.querySelector('#create').onclick = async () => {
    await api('/api/batches', {
      method: 'POST',
      body: JSON.stringify({
        source_name: el.querySelector('#src').value,
        note: el.querySelector('#note').value,
      }),
    });
    await renderCaptures(el);
  };
  el.querySelectorAll('[data-close]').forEach((btn) => {
    btn.onclick = async () => {
      await api(`/api/batches/${btn.dataset.close}/close`, { method: 'POST', body: '{}' });
      await renderCaptures(el);
    };
  });
  el.querySelectorAll('[data-auto]').forEach((btn) => {
    btn.onclick = async () => {
      if (!openBin) return;
      await api('/api/quarantine/auto-enter', {
        method: 'POST',
        body: JSON.stringify({ batch_id: Number(btn.dataset.auto), bin_id: openBin.id }),
      });
      await renderCaptures(el);
    };
  });
  el.querySelectorAll('[data-ingest]').forEach((btn) => {
    btn.onclick = async () => {
      const msg = el.querySelector('#ingestMsg');
      msg.textContent = '';
      try {
        const d = await api(`/api/batches/${btn.dataset.ingest}`);
        const good = (d.items || []).find((i) => i.quality === '合格' && i.status === '待检');
        if (!good) {
          msg.textContent = '该批没有待入库的合格件';
          return;
        }
        await api('/api/captures/ingest', {
          method: 'POST',
          body: JSON.stringify({ item_id: good.id }),
        });
        msg.textContent = `已清洗入库：${good.item_key}`;
        await renderCaptures(el);
        el.querySelector('#ingestMsg').textContent = `已清洗入库：${good.item_key}`;
      } catch (e) {
        msg.textContent = `入库被拒：${e.message}`;
      }
    };
  });
  el.querySelector('#runChip').onclick = () => navigate('rework');
  el.querySelector('#reworkRemainder').onclick = () => navigate('rework');
  el.querySelectorAll('[data-open]').forEach((btn) => {
    btn.onclick = async () => {
      const d = await api(`/api/batches/${btn.dataset.open}`);
      const box = el.querySelector('#detail');
      box.innerHTML = `
        <h3>${d.batch_code} 条目</h3>
        <div class="row">
          <input id="ikey" placeholder="条目键" />
          <select id="iq">
            <option>合格</option><option>缺字段</option><option>编码空</option><option>重复键</option>
          </select>
          <button id="addItem">追加条目</button>
        </div>
        <table>
          <thead><tr><th>键</th><th>质量</th><th>状态</th></tr></thead>
          <tbody>
            ${(d.items || []).map((i) => `
              <tr>
                <td>${i.item_key}</td>
                <td><span class="chip ${i.quality === '合格' ? 'ok' : 'bad'}">${i.quality}</span></td>
                <td>${i.status}</td>
              </tr>`).join('')}
          </tbody>
        </table>`;
      box.querySelector('#addItem').onclick = async () => {
        await api(`/api/batches/${d.id}/items`, {
          method: 'POST',
          body: JSON.stringify({
            item_key: box.querySelector('#ikey').value,
            quality: box.querySelector('#iq').value,
            payload: '{}',
          }),
        });
        btn.click();
      };
    };
  });
}
