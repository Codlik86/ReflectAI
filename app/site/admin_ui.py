from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
import os

router = APIRouter(prefix="/admin", tags=["admin-ui"])

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

PAGE = """
<!doctype html>
<meta charset="utf-8"/>
<title>Admin · Pomni</title>
<style>
 body{font:14px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial;}
 table{border-collapse:collapse;margin:16px 0;width:100%}
 th,td{border:1px solid #ddd;padding:6px 8px}
 th{background:#fafafa;text-align:left}
 code{background:#f6f8fa;padding:2px 4px;border-radius:4px}
 .wrap{max-width:1100px;margin:24px auto;padding:0 16px}
 .muted{color:#777}
 .row{display:flex;gap:12px;align-items:center}
 input{padding:6px 8px;border:1px solid #ddd;border-radius:6px}
 button{padding:6px 10px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer}
</style>
<div class="wrap">
  <h2>Admin</h2>
  <p class="muted">Токен передан как <code>?token=...</code>. На этой странице мы дергаем JSON-эндпоинты и показываем таблицы.</p>

  <div class="row">
    <input id="tg" placeholder="tg_id" />
    <button onclick="loadUser()">Загрузить пользователя</button>
  </div>
  <pre id="user" class="muted">user: —</pre>

  <h3>Последние платежи</h3>
  <table id="payments"><thead><tr>
    <th>id</th><th>user_id</th><th>provider_payment_id</th><th>amount</th><th>status</th><th>created_at</th>
  </tr></thead><tbody></tbody></table>

  <h3>Подписки</h3>
  <table id="subs"><thead><tr>
    <th>id</th><th>user_id</th><th>plan</th><th>status</th><th>until</th><th>updated</th>
  </tr></thead><tbody></tbody></table>

  <p>
    Экспорт: <a id="csv_users">users.csv</a> · <a id="csv_pay">payments.csv</a> · <a id="csv_subs">subscriptions.csv</a>
  </p>
</div>
<script>
 const token = new URLSearchParams(location.search).get('token') || '';
 const base = location.origin;

 function auth(url){ return [url, {headers:{'Authorization':'Bearer '+token}}]; }

 function fmt(x){ return x==null?'':String(x); }

 async function loadUser(){
   const tg = document.getElementById('tg').value.trim();
   if(!tg) return;
   const [uurl, uopts] = auth(`${base}/api/admin/users/${tg}/full`);
   const r = await fetch(uurl, uopts);
   const j = await r.json();
   document.getElementById('user').textContent = JSON.stringify(j.user || j, null, 2);
 }

 async function fillPayments(){
   const [url, opts] = auth(`${base}/api/admin/payments?limit=50`);
   const r = await fetch(url, opts); const j = await r.json();
   const tb = document.querySelector('#payments tbody'); tb.innerHTML='';
   for(const p of (j.items||[])){
     const tr = document.createElement('tr');
     tr.innerHTML = `<td>${fmt(p.id)}</td><td>${fmt(p.user_id)}</td><td>${fmt(p.provider_payment_id)}</td>
                     <td>${fmt(p.amount)} ${fmt(p.currency||'')}</td><td>${fmt(p.status)}</td><td>${fmt(p.created_at)}</td>`;
     tb.appendChild(tr);
   }
 }

 async function fillSubs(){
   const [url, opts] = auth(`${base}/api/admin/subscriptions?limit=50`);
   const r = await fetch(url, opts); const j = await r.json();
   const tb = document.querySelector('#subs tbody'); tb.innerHTML='';
   for(const s of (j.items||[])){
     const tr = document.createElement('tr');
     tr.innerHTML = `<td>${fmt(s.id)}</td><td>${fmt(s.user_id)}</td><td>${fmt(s.plan)}</td>
                     <td>${fmt(s.status)}</td><td>${fmt(s.subscription_until)}</td><td>${fmt(s.updated_at)}</td>`;
     tb.appendChild(tr);
   }
 }

 function wireCSV(){
   document.getElementById('csv_users').href = `${base}/api/admin/export/users.csv?token=${token}`;
   document.getElementById('csv_pay').href   = `${base}/api/admin/export/payments.csv?token=${token}`;
   document.getElementById('csv_subs').href  = `${base}/api/admin/export/subscriptions.csv?token=${token}`;
 }

 // небольшой прокси для query token -> header Bearer
 (function installTokenProxy(){
   const origFetch = window.fetch;
   window.fetch = function(url, opts){
     const u = new URL(url, location.origin);
     const t = u.searchParams.get('token');
     if(t){
       u.searchParams.delete('token');
       opts = opts||{};
       opts.headers = Object.assign({}, opts.headers||{}, {'Authorization':'Bearer '+t});
       return origFetch(u.toString(), opts);
     }
     return origFetch(url, opts);
   }
 })();

 fillPayments(); fillSubs(); wireCSV();
</script>
"""

@router.get("/", response_class=HTMLResponse)
async def admin_page(request: Request):
    # страничка сама не проверяет токен — токен передаём в ссылках и JS прокидывает его как Bearer
    if not ADMIN_TOKEN:
        return HTMLResponse("<h3>ADMIN_TOKEN is not set</h3>", status_code=500)
    return HTMLResponse(PAGE)
