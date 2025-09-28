# app/site/templates/admin_ui.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
import os

router = APIRouter(prefix="/admin", tags=["admin-ui"])

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

PAGE = r"""
<!doctype html>
<meta charset="utf-8"/>
<title>Admin · Pomni</title>
<style>
  :root{
    --fg:#111; --muted:#777; --bd:#e6e6e6; --bg:#fff; --bg2:#fafafa; --ok:#0a7; --err:#d33;
  }
  html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial}
  .wrap{max-width:1100px;margin:24px auto;padding:0 16px}
  h1,h2,h3{margin:16px 0 8px}
  .muted{color:var(--muted)}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  input,select{padding:7px 9px;border:1px solid var(--bd);border-radius:8px;background:#fff;min-width:140px}
  input.small{min-width:80px}
  button{padding:7px 10px;border:1px solid var(--bd);border-radius:8px;background:#fff;cursor:pointer}
  button.primary{background:#111;color:#fff;border-color:#111}
  button.warn{background:#fff2f2;border-color:#f2caca}
  button:disabled{opacity:.5;cursor:not-allowed}
  code{background:#f6f8fa;padding:2px 4px;border-radius:4px}
  pre{background:var(--bg2);border:1px solid var(--bd);border-radius:8px;padding:10px;overflow:auto}
  table{border-collapse:collapse;margin:12px 0 18px;width:100%}
  th,td{border:1px solid var(--bd);padding:6px 8px}
  th{background:var(--bg2);text-align:left}
  .pill{display:inline-block;padding:2px 6px;border-radius:999px;border:1px solid var(--bd);background:#fff}
  .pill.ok{border-color:#bfeee0;background:#f2fffb}
  .pill.err{border-color:#f6c9c9;background:#fff6f6}
  .toolbar{display:flex;gap:10px;align-items:center;justify-content:space-between;margin:8px 0 18px}
  .right{margin-left:auto}
  .hint{font-size:12px;color:var(--muted)}
  .hr{height:1px;background:var(--bd);margin:16px 0}
  a{color:#0645ad;text-decoration:none}
  a:hover{text-decoration:underline}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media(max-width:860px){.grid2{grid-template-columns:1fr}}
</style>

<div class="wrap">
  <h2>Admin</h2>
  <p class="muted">Передайте токен как <code>?token=...</code>. JS на странице положит его в <code>Authorization: Bearer ...</code> для всех запросов.</p>

  <div class="grid2">
    <!-- Блок пользователя -->
    <section>
      <h3>Пользователь</h3>
      <div class="row">
        <input id="tg" placeholder="tg_id" />
        <button class="primary" onclick="loadUser()">Загрузить</button>
        <span class="hint">Покажем профиль, платежи и подписки</span>
      </div>
      <pre id="user" class="muted" style="min-height:72px">user: —</pre>

      <div class="row">
        <button onclick="trialStart()">Trial start</button>
        <input id="trialDays" class="small" type="number" value="5" min="1" style="width:74px">
        <button onclick="trialEnd()">Trial end</button>
        <span class="hr" style="flex:1 0 12px;background:none"></span>
        <button onclick="subFlag('activate')">User subscription flag: activate</button>
        <button onclick="subFlag('deactivate')">deactivate</button>
      </div>
    </section>

    <!-- Блок управления подпиской -->
    <section>
      <h3>Subscription actions</h3>
      <div class="row">
      <button id="btnCharge" onclick="chargeDue()">Charge due (24h)</button>
        <input id="uid" placeholder="user_id" class="small"/>
        <select id="plan">
          <option value="month">month</option>
          <option value="week">week</option>
          <option value="quarter">quarter</option>
          <option value="year">year</option>
        </select>
        <button onclick="reactivate()">Reactivate / Prolong</button>
        <button class="warn" onclick="cancel()">Cancel</button>
        <span class="right"></span>
        <button class="warn" onclick="expireAll()">Expire all overdue</button>
      </div>
      <p class="hint">Reactivate продляет с учётом текущей даты/остатка; Cancel ставит статус <code>canceled</code>; Expire отмечает все активные просроченные как <code>expired</code>.</p>
    </section>
  </div>

  <div class="hr"></div>

  <div class="toolbar">
    <h3>Последние платежи</h3>
    <span class="right"></span>
  </div>
  <table id="payments"><thead><tr>
    <th>id</th><th>user_id</th><th>yk_payment_id</th><th>amount</th><th>status</th><th>created_at</th>
  </tr></thead><tbody></tbody></table>

  <div class="toolbar">
    <h3>Подписки</h3>
    <span class="right"></span>
  </div>
  <table id="subs"><thead><tr>
    <th>id</th><th>user_id</th><th>plan</th><th>status</th><th>until</th><th>auto</th><th>updated</th>
  </tr></thead><tbody></tbody></table>

  <p>
    Экспорт:
    <a id="csv_users">users.csv</a> ·
    <a id="csv_pay">payments.csv</a> ·
    <a id="csv_subs">subscriptions.csv</a>
  </p>
</div>

<script>
  // ----- token proxy: ?token=... -> Authorization header
  (function installTokenProxy(){
    const orig = window.fetch;
    window.fetch = function(url, opts){
      const u = new URL(url, location.origin);
      const t = (u.searchParams.get('token') || new URLSearchParams(location.search).get('token') || '').trim();
      if(t){
        u.searchParams.delete('token');
        opts = opts || {};
        opts.headers = Object.assign({}, opts.headers || {}, {'Authorization':'Bearer '+t});
        return orig(u.toString(), opts);
      }
      return orig(url, opts);
    }
  })();

  const token = new URLSearchParams(location.search).get('token') || '';
  const base = location.origin;

  function fmt(x){ return (x==null||x===undefined)?'':String(x); }
  function asMoney(cents, cur){ if(cents==null) return ''; const rub = Number(cents)/100; return rub.toFixed(2)+' '+(cur||'RUB'); }
  function pill(v, ok='ok', err='err'){ const cls = (String(v).toLowerCase()==='active'||v===true)?ok:err; return `<span class="pill ${cls}">${fmt(v)}</span>`; }

  async function chargeDue(){
  const [url, opts] = auth(`${base}/api/admin/maintenance/charge_due?hours=24&dry_run=0`);
  const r = await fetch(url, { ...opts, method:'POST' });
  const j = await r.json();
  alert('Charge due:\n' + JSON.stringify(j, null, 2));
}

  async function GET(path){
    const r = await fetch(`${base}${path}`, {headers:{'Authorization':'Bearer '+token}});
    if(!r.ok){ const t=await r.text(); throw new Error(t||r.statusText); }
    return r.json();
  }
  async function POST(path, body){
    const r = await fetch(`${base}${path}`, {
      method:'POST',
      headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},
      body: body?JSON.stringify(body):'{}'
    });
    if(!r.ok){ const t=await r.text(); throw new Error(t||r.statusText); }
    return r.json();
  }

  async function loadUser(){
    const tg = document.getElementById('tg').value.trim();
    if(!tg){ alert('Введите tg_id'); return; }
    try{
      const j = await GET(`/api/admin/users/${tg}/full`);
      document.getElementById('user').textContent = JSON.stringify(j.user || j, null, 2);
      // проставим user_id в блоке подписок
      if(j.user && j.user.id){ document.getElementById('uid').value = j.user.id; }
      await fillPayments();
      await fillSubs();
    }catch(e){ alert('Ошибка: '+e.message); }
  }

  async function fillPayments(){
    const j = await GET(`/api/admin/payments?limit=50`);
    const tb = document.querySelector('#payments tbody'); tb.innerHTML='';
    for(const p of (j.items||[])){
      const yk = p.yk_payment_id || p.provider_payment_id;
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${fmt(p.id)}</td>
        <td>${fmt(p.user_id)}</td>
        <td>${fmt(yk)}</td>
        <td>${asMoney(p.amount_rub ?? p.amount, p.currency)}</td>
        <td>${fmt(p.status)}</td>
        <td>${fmt(p.created_at)}</td>`;
      tb.appendChild(tr);
    }
  }

  async function fillSubs(){
    const j = await GET(`/api/admin/subscriptions?limit=50`);
    const tb = document.querySelector('#subs tbody'); tb.innerHTML='';
    for(const s of (j.items||[])){
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${fmt(s.id)}</td>
        <td>${fmt(s.user_id)}</td>
        <td>${fmt(s.plan)}</td>
        <td>${pill(s.status)}</td>
        <td>${fmt(s.subscription_until)}</td>
        <td>${fmt(s.is_auto_renew)}</td>
        <td>${fmt(s.updated_at)}</td>`;
      tb.appendChild(tr);
    }
  }

  async function trialStart(){
    const tg = document.getElementById('tg').value.trim();
    const days = Number(document.getElementById('trialDays').value || 5);
    if(!tg) return alert('tg_id');
    try{
      await POST(`/api/admin/users/${tg}/trial/start?days=${days}`);
      await loadUser();
      alert('Trial started');
    }catch(e){ alert('Ошибка: '+e.message); }
  }
  async function trialEnd(){
    const tg = document.getElementById('tg').value.trim();
    if(!tg) return alert('tg_id');
    try{
      await POST(`/api/admin/users/${tg}/trial/end`);
      await loadUser();
      alert('Trial ended');
    }catch(e){ alert('Ошибка: '+e.message); }
  }
  async function subFlag(kind){
    const tg = document.getElementById('tg').value.trim();
    if(!tg) return alert('tg_id');
    try{
      await POST(`/api/admin/users/${tg}/subscription/${kind}`);
      await loadUser();
      alert('OK');
    }catch(e){ alert('Ошибка: '+e.message); }
  }

  async function reactivate(){
    const uid = Number(document.getElementById('uid').value||0);
    const plan = document.getElementById('plan').value || 'month';
    if(!uid) return alert('user_id');
    try{
      await POST(`/api/admin/subscriptions/reactivate`, {user_id: uid, plan});
      await fillSubs(); await loadUser();
      alert('Reactivated');
    }catch(e){ alert('Ошибка: '+e.message); }
  }
  async function cancel(){
    const uid = Number(document.getElementById('uid').value||0);
    if(!uid) return alert('user_id');
    try{
      await POST(`/api/admin/subscriptions/cancel`, {user_id: uid});
      await fillSubs(); await loadUser();
      alert('Canceled');
    }catch(e){ alert('Ошибка: '+e.message); }
  }
  async function expireAll(){
    try{
      await POST(`/api/admin/subscriptions/expire`, {});
      await fillSubs();
      alert('Expired overdue subscriptions');
    }catch(e){ alert('Ошибка: '+e.message); }
  }

  function wireCSV(){
    document.getElementById('csv_users').href = `${base}/api/admin/export/users.csv?token=${encodeURIComponent(token)}`;
    document.getElementById('csv_pay').href   = `${base}/api/admin/export/payments.csv?token=${encodeURIComponent(token)}`;
    document.getElementById('csv_subs').href  = `${base}/api/admin/export/subscriptions.csv?token=${encodeURIComponent(token)}`;
  }

  // initial load
  wireCSV(); fillPayments(); fillSubs();
</script>
"""

@router.get("/", response_class=HTMLResponse)
async def admin_page(_: Request):
    if not ADMIN_TOKEN:
        return HTMLResponse("<h3>ADMIN_TOKEN is not set</h3>", status_code=500)
    return HTMLResponse(PAGE)
