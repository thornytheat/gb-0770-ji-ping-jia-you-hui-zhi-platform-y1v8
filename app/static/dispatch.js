const status = document.querySelector('#dispatch-message');

async function post(path,body) {
  const response = await fetch(path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || '交班提交失败');
  return data;
}

function bind(selector, submit) {
  const form = document.querySelector(selector);
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    status.textContent = '正在核量…';
    try { await submit(form); }
    catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
}

bind('#archive', async form => {
  const files = Object.fromEntries(['tickets','receipts','stock'].map(id => [id,document.getElementById(id).value]));
  const values = Object.values(files);
  if (values.some(Boolean) && !values.every(Boolean)) throw new Error('请填全三份 CSV');
  const sample = form.dataset.sample;
  const ack = await post('/api/shifts', values.every(Boolean) ? files : {sample});
  location.assign('/ops/sign?' + new URLSearchParams({sample,batch:ack.batch}));
});

const pick = document.querySelector('#batch');
const hash = document.querySelector('#checksum');
const exportLink = document.querySelector('#export-slip');
const late = document.querySelector('#late-receipts');
const supplementResult = document.querySelector('#supplement-result');
const recheckForm = document.querySelector('#recheck');
const recheckHash = document.querySelector('#recheck-checksum');
const recheckLink = document.querySelector('#export-recheck');
let supplementId = null;
pick?.addEventListener('change', () => {
  hash.value = ''; exportLink.hidden = true;
  supplementId = null; supplementResult.value = '';
  recheckForm.hidden = true; recheckHash.value = ''; recheckLink.hidden = true;
});
bind('#signature', async () => {
  const batch = pick.value;
  const result = await post(`/api/shifts/${batch}/sign`,{});
  if (pick.value !== batch) return;
  hash.value = result.checksum;
  exportLink.href = `/api/shifts/${batch}/export`;
  exportLink.hidden = false;
  status.textContent = `证据已签发 · 待复核 ${result.open_items} 票`;
});

bind('#supplement', async () => {
  const batch = pick.value;
  const text = late.value.trim();
  const result = await post(`/api/shifts/${batch}/supplements`, text ? {receipts:text} : {sample:'late'});
  if (pick.value !== batch) return;
  supplementId = result.supplement;
  supplementResult.value = `补件 ${result.supplement.slice(0,15)} · 重算 ${result.conclusions.length} 票 · 争议 ${result.disputed.length} 票 · 仍缺签 ${result.still_missing.length} 票${result.reused ? ' · 重试未重复核销' : ''}`;
  recheckForm.hidden = false;
  recheckHash.value = '';
  recheckLink.hidden = true;
  status.textContent = '补件已入原班次对账';
});

bind('#recheck', async () => {
  const batch = pick.value;
  const result = await post(`/api/shifts/${batch}/supplements/${supplementId}/sign`,{});
  if (pick.value !== batch) return;
  recheckHash.value = result.checksum;
  recheckLink.href = `/api/shifts/${batch}/supplements/${supplementId}/export`;
  recheckLink.hidden = false;
  status.textContent = `补核已签发 · 原证据单 ${result.original.slice(0,15)} 可复验`;
});
