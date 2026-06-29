// 设置默认日期为今天
function todayStr() {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

async function init() {
  document.getElementById('scanStart').value = todayStr();
  document.getElementById('scanEnd').value = todayStr();

  // 状态
  try {
    const r = await fetch('/api/status');
    const s = await r.json();
    const el = document.getElementById('status');
    el.innerHTML = (s.ocr_ready ? '<span class="ok">✓ OCR 就绪</span>' : '<span class="warn">✗ OCR 未就绪</span>')
      + ' · ' + (s.exists ? '<span class="ok">✓ 搜索路径有效</span>' : '<span class="warn">✗ 搜索路径无效</span>');
  } catch (e) {}

  // 配置
  try {
    const r = await fetch('/api/config');
    const c = await r.json();
    document.getElementById('wxPath').value = c.wx_path || '';
    const info = document.getElementById('detectedInfo');
    info.innerHTML = `当前搜索路径: <code>${c.search_base}</code> ${c.exists ? '✓' : '✗ 不存在'}<br>`
      + `默认微信根目录: <code>${c.default_root}</code><br>`
      + `<span style="color:#888">支持填任意大路径，会递归搜索其下所有子目录（图片/PDF/压缩包/Word）</span>`;
  } catch (e) {}

  loadInvoices();
}

async function saveConfig() {
  const wxPath = document.getElementById('wxPath').value.trim();
  const r = await fetch('/api/config', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ wx_path: wxPath })
  });
  const d = await r.json();
  alert(d.ok ? '已保存' : '保存失败');
  location.reload();
}

async function runScan() {
  const start = document.getElementById('scanStart').value;
  const end = document.getElementById('scanEnd').value;
  const force = document.getElementById('forceScan').checked;
  if (!start) { alert('请选择开始日期'); return; }
  const btn = document.getElementById('scanBtn');
  btn.disabled = true;
  const prog = document.getElementById('scanProgress');
  const res = document.getElementById('scanResult');
  prog.style.display = 'block';
  prog.textContent = '扫描中，正在 OCR 识别图片，请稍候...';
  res.textContent = '';
  try {
    const r = await fetch('/api/scan', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ start, end: end || start, force })
    });
    const d = await r.json();
    prog.style.display = 'none';
    if (d.error) {
      res.innerHTML = `<span style="color:#c62828">${d.error}</span>`;
    } else {
      res.innerHTML = `扫描完成：处理 ${d.scanned} 个文件，新增发票 <b class="new">${d.invoices}</b> 张，跳过缓存 ${d.skipped}，错误 ${d.errors}`;
      if (d.new_invoices && d.new_invoices.length) {
        res.innerHTML += '<ul>' + d.new_invoices.map(i =>
          `<li>${i.seller || '?'} · 号码 ${i.invoice_no || '?'} · ¥${i.total_amount || '?'}</li>`
        ).join('') + '</ul>';
      }
      loadInvoices();
    }
  } catch (e) {
    prog.style.display = 'none';
    res.innerHTML = `<span style="color:#c62828">扫描请求失败: ${e}</span>`;
  }
  btn.disabled = false;
}

async function loadInvoices() {
  const dateField = document.getElementById('dateField').value;
  const start = document.getElementById('filterStart').value;
  const end = document.getElementById('filterEnd').value;
  const q = document.getElementById('searchQ').value.trim();
  const params = new URLSearchParams({ date_field: dateField });
  if (start) params.set('start', start);
  if (end) params.set('end', end);
  if (q) params.set('q', q);
  const r = await fetch('/api/invoices?' + params);
  const d = await r.json();
  const tbody = document.querySelector('#invTable tbody');
  tbody.innerHTML = '';
  document.getElementById('listCount').textContent = `共 ${d.count} 条 (库内总计 ${d.total})`;
  d.items.forEach(it => {
    const tr = document.createElement('tr');
    tr.onclick = () => showDetail(it.id);
    tr.innerHTML = `
      <td>${it.thumb_path ? `<img class="thumb" src="/api/thumb/${it.id}">` : '—'}</td>
      <td>${it.invoice_no || '—'}</td>
      <td>${it.invoice_code || '—'}</td>
      <td>${it.invoice_date || '—'}</td>
      <td>${esc(it.seller) || '—'}</td>
      <td>${esc(it.buyer) || '—'}</td>
      <td class="amount">${it.total_amount ? '¥' + it.total_amount : '—'}</td>
      <td>${fmtTime(it.wechat_time)}</td>
      <td>${esc(it.invoice_type) || '—'}</td>
      <td><button class="del-btn" onclick="event.stopPropagation();deleteInv(${it.id}, this)">删除</button></td>`;
    tbody.appendChild(tr);
  });
  if (!d.items.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#999;padding:24px">暂无数据，请先按日期扫描</td></tr>';
  }
}

async function deleteInv(id, btn) {
  if (!confirm('确认删除这条发票记录？')) return;
  try {
    const r = await fetch('/api/invoice/' + id, { method: 'DELETE' });
    if (r.ok) {
      loadInvoices();
    } else {
      alert('删除失败: HTTP ' + r.status);
    }
  } catch (e) {
    alert('删除失败: ' + e);
  }
}

function clearFilter() {
  document.getElementById('filterStart').value = '';
  document.getElementById('filterEnd').value = '';
  document.getElementById('searchQ').value = '';
  loadInvoices();
}

async function showDetail(id) {
  const r = await fetch('/api/invoice/' + id);
  const it = await r.json();
  const c = document.getElementById('modalContent');
  c.innerHTML = `
    <img src="/api/image/${it.id}" onerror="this.style.display='none'">
    <div class="info">
      <dl>
        <dt>发票类型</dt><dd>${esc(it.invoice_type) || '—'}</dd>
        <dt>发票代码</dt><dd>${it.invoice_code || '—'}</dd>
        <dt>发票号码</dt><dd>${it.invoice_no || '—'}</dd>
        <dt>开票日期</dt><dd>${it.invoice_date || '—'}</dd>
        <dt>购买方</dt><dd>${esc(it.buyer) || '—'}</dd>
        <dt>销售方</dt><dd>${esc(it.seller) || '—'}</dd>
        <dt>金额</dt><dd>${it.amount || '—'}</dd>
        <dt>价税合计</dt><dd>${it.total_amount ? '¥' + it.total_amount : '—'}</dd>
        <dt>校验码</dt><dd>${it.check_code || '—'}</dd>
        <dt>机器编号</dt><dd>${it.machine_no || '—'}</dd>
        <dt>微信接收时间</dt><dd>${fmtTime(it.wechat_time)}</dd>
        <dt>来源文件</dt><dd style="word-break:break-all">${esc(it.source_path) || '—'}</dd>
      </dl>
    </div>
    <div class="ocr"><dt style="font-weight:600;color:#444">OCR 原文</dt>
      <pre>${esc(it.ocr_text) || '(空)'}</pre></div>
    <div style="width:100%;text-align:right;margin-top:8px">
      <button class="del-btn" onclick="deleteInv(${it.id});closeModal()">删除此条</button>
    </div>`;
  document.getElementById('modal').classList.add('show');
}
function closeModal() { document.getElementById('modal').classList.remove('show'); }

function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function fmtTime(s) { if (!s) return '—'; return s.replace('T', ' ').slice(0, 16); }

init();
