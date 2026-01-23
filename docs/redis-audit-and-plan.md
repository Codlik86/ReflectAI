# Redis Audit and Integration Plan (ReflectAI “Помни”)

## 1) Executive summary (1 экран)
**Что Redis реально может решить**
- Снижение latency и нагрузки на Neon в самых горячих путях (история диалогов и access_state) за счёт коротких TTL‑кэшей.
- Устойчивость при росте нагрузки и мульти‑инстансах: общий cache/locks вместо in‑memory, стабильные дебаунс‑буферы между инстансами.
- Дешёвое хранение краткой истории для LLM, чтобы не дергать `bot_messages` каждый запрос.
- Координация фоновых задач (advisory locks сейчас в Postgres; Redis может дать более гибкие lock’и и видимость состояния).

**Что Redis НЕ решает**
- Качество ответов LLM/понимание коротких реплик — это prompt/aggregation/heuristics (см. `app/bot.py` short‑reply и debounce). Redis не улучшит интерпретацию смысла.
- Плохой RAG/embeddings/429 — решается rate‑limit ретраями и оптимизацией embedding‑политик, а не самим Redis.

---

## 2) Текущая архитектура “памяти и контекста” (as‑is)
Ниже карта по репо: файл → функции → что делает → частота/важность.

### 2.1 Сбор LLM‑контекста (messages[])
- `app/bot.py` → `_answer_with_llm()`
  - Формирует `messages[]` для LLM: system prompt, rag_ctx (optional), long‑memory summaries, history, user_text.
  - Источник short‑memory: `_load_history_from_db()` (БД `bot_messages`) с fallback на `get_recent_messages()` (in‑memory `RECENT_BUFFER`).
  - Частота: **каждое сообщение пользователя** в режимах talk/reflection.

### 2.2 Short‑memory (последние сообщения)
- `app/bot.py` → `_load_history_from_db(tg_id)`
  - SELECT из `bot_messages` за последние N часов.
  - Частота: **каждый LLM вызов**.
- `app/bot.py` → `RECENT_BUFFER` + `_buf_push()`
  - Эфемерный буфер, используется когда `privacy_level = none` (чтобы не писать в БД).
  - Частота: **каждый входящий/исходящий бот‑месседж**.
- `app/memory.py`
  - `get_recent_messages()`, `save_user_message()`, `save_bot_message()` — альтернативные/старые пути работы с историей.

### 2.3 RAG / Qdrant
- `app/rag_qdrant.py`
  - `search()` + `embed()` → поиск в Qdrant для «краткого контекста».
  - Используется в `app/bot.py` (rag_ctx)
  - Частота: **каждый LLM вызов** (если rag_search доступен).
- `app/rag_summaries.py`
  - `upsert_summary_point()` — запись summary в Qdrant.
  - `search_summaries()` — поисковое извлечение long‑memory summary по пользователю.
  - Частота: `search_summaries()` — **каждый LLM вызов**, `upsert_summary_point()` — **каждый daily/weekly/monthly**.

### 2.4 Long‑memory summaries
- `app/memory_summarizer.py`
  - `make_daily()` → собирает дневные summary: читает `bot_messages` → LLM → пишет `dialog_summaries` → `upsert_summary_point()` (Qdrant)
  - `rollup_weekly()` / `rollup_monthly()` — свёртка из daily.
  - Частота: **cron**.
- `app/site/summaries_api.py`
  - `/api/admin/summaries/daily` → фоновая задача (job queue, advisory lock, batch).
  - `/weekly`, `/monthly` — синхронные батчи.
- `app/api/admin.py` (bridge)
  - `/api/admin/summaries/daily` → тоже запускает фоновую задачу (подбирает user_ids по `bot_messages`).

### 2.5 Debounce/aggregation
- `app/bot.py` → `_enqueue_talk_message()`, `_flush_talk_buffer()`
  - In‑memory буфер (`TALK_DEBOUNCE_BUFFER`) с задержкой 0.9–1.1s, склейка сообщений перед LLM.
  - Частота: **каждый текстовый апдейт** в режиме talk/reflection.

### 2.6 Locks / anti‑spam / coordination
- `app/site/summaries_api.py`
  - `pg_try_advisory_lock(hashtext('summaries_daily'))` → lock daily job
  - Используется для предотвращения параллельных запусков daily.
- `app/api/nudges.py`
  - Nudge логика работает без явного lock, но с dedupe‑проверкой `_was_sent_recently()`.

### 2.7 Access state (SSOT)
- `app/services/access_state.py` → `get_access_state()`
  - Query user + subscriptions; returns has_access, reason, trial_until, etc.
- Часто вызывается в:
  - `app/billing/service.py` (`check_access()`, `is_trial_active()`)
  - `app/bot.py` GateMiddleware и trial автозапуск
  - `app/api/access.py` (miniapp access)

---

## 3) Карта горячих путей (top‑10)
1) `app/bot.py::_answer_with_llm()` → `_load_history_from_db()` → **SELECT bot_messages** (каждый LLM запрос).
2) `app/bot.py::_answer_with_llm()` → `rag_search()` → **Qdrant search + embeddings** (каждый LLM запрос).
3) `app/bot.py::_answer_with_llm()` → `search_summaries()` → **Qdrant + embeddings** (каждый LLM запрос).
4) `app/bot.py::_answer_with_llm()` → `_ensure_user_id()` → **SELECT/INSERT users** (часто при первом сообщении).
5) `app/bot.py::GateMiddleware` → `get_access_state()` → **SELECT users/subscriptions** (каждый апдейт).
6) `app/api/access.py` (miniapp access) → `get_access_status()` → **SELECT users/subscriptions** (каждый miniapp запуск).
7) `app/memory_summarizer.py::make_daily()` → `_fetch_raw_messages()` → **SELECT bot_messages** (cron).
8) `app/memory_summarizer.py::make_daily()` → `_llm_summarize()` → **LLM** (cron).
9) `app/rag_summaries.py::upsert_summary_point()` → **Qdrant upsert + embeddings** (cron).
10) `app/api/nudges.py::_pick_targets()` → **SELECT users/bot_messages/nudges** (cron, но массово).

Почему горячо: пункты 1–6 запускаются **на каждое пользовательское действие**, 7–10 — **тяжёлые батчи**, создающие нагрузку на Neon/Qdrant.

---

## 4) Где сейчас у нас есть “кэш/состояние” и риск дублей
- `RECENT_BUFFER` (app/bot.py)
  - In‑memory deque per user. Используется при privacy=none как “квазиистория”.
  - TTL/очистка: нет явной; очищается при purge/history и при переполнении deque.
  - Риск дублей при Redis: если вводим Redis history, нужно решить — оставить только для privacy=none или унифицировать.

- `TALK_DEBOUNCE_BUFFER` (app/bot.py)
  - In‑memory buffer per user, TTL implicit (timer), не shared между инстансами.
  - Риск: при multi‑instance сообщения одного пользователя могут попадать в разные буферы → потеря merge.

- `DAILY_JOBS` (app/site/summaries_api.py)
  - In‑memory status registry; при рестарте теряется.
  - Redis может заменить для job visibility.

- Miniapp (front): `miniapp/src` хранит client‑side состояние (localStorage/session?) — **гипотеза** (нужна проверка файлов `miniapp/src/lib/*` при необходимости).

- Qdrant client: `app/qdrant_client.py` держит singleton `_client` in‑memory.

**Риск дублей**: Redis history + существующий `RECENT_BUFFER` / DB history — надо решить кто главный. Предпочтение: Redis для быстрого read, DB как источник истины/архив, RECENT_BUFFER оставить только для privacy=none.

---

## 5) Redis “минимальный слой” (to‑be)
Конкретные ключи/TTL/назначение (привязка к текущим функциям):

1) `access_state:{user_id}`
- TTL: 30–60s
- Источник: `app/services/access_state.py::get_access_state()`
- Цель: снизить SELECT users/subscriptions на каждом апдейте.

2) `recent_history:{user_id}`
- TTL: 10–30 мин
- Структура: list/stream коротких сообщений (role+text+ts)
- Источник: `bot_messages` и `RECENT_BUFFER`
- Цель: ускорить `_load_history_from_db()` в `_answer_with_llm()`.

3) `talkbuf:{user_id}` (опционально)
- TTL: 20–30s
- Структура: list текстов + last_ts
- Цель: кросс‑инстанс debounce вместо `TALK_DEBOUNCE_BUFFER`.

4) `rag_cache:{hash}`
- TTL: 2–10 мин
- Ключ: hash(user_text + params)
- Цель: кэшировать Qdrant query/embeddings для `rag_search()`

5) `summaries_job:{job_id}`
- TTL: 1–2 часа
- Содержимое: status, counters, timestamps
- Цель: замена in‑memory `DAILY_JOBS` для observability.

6) `locks:{name}`
- TTL: 15–30 мин
- Цель: альтернативный lock (если уйдём от `pg_try_advisory_lock`).

---

## 6) План внедрения по этапам (минимальный, без ломания)
**Этап 0: подготовка**
- Добавить ENV `REDIS_URL`, `REDIS_TTL_*`.
- Подключить лёгкий Redis client (aioredis/redis‑py async). **Без изменения логики.**
- Риски: конфигурация/сеть, fallback при недоступности.

**Этап 1: Redis cache для access_state**
- Что меняется: `get_access_state()` сначала читает Redis, на miss — БД.
- Что оставляем: SSOT в БД (users/subscriptions) — **не трогаем**.
- Риски: устаревание статуса → TTL 30–60s, invalidation на оплате.
- Откат: выключить через ENV, fallback на БД.

**Этап 2: Redis cache для recent_history**
- Что меняется: `_load_history_from_db()` сначала читает Redis list; при miss — БД и write‑back в Redis.
- Что оставить/убрать: `RECENT_BUFFER` оставить только для privacy=none; иначе можно оставить как fallback in‑memory.
- Риски: частичная история/рассинхрон → TTL 10–30 мин, строгий fallback на DB.
- Откат: отключить Redis read; продолжить DB.

**Этап 3: Redis для debounce buffer (multi‑instance)**
- Что меняется: `TALK_DEBOUNCE_BUFFER` → Redis list+timestamp, один “leader” делает flush.
- Что убрать: in‑memory buffer в talk‑mode при включенном Redis.
- Риски: сложность race conditions, требуется lock per user.
- Откат: выключить Redis debounce и вернуть local buffer.

**Этап 4: Redis cache для RAG**
- Что меняется: кэшировать `rag_search()` результат (контекст + top_k) и embeddings (hash by text).
- Что оставить: Qdrant как SSOT; Redis только cache.
- Риски: stale context → TTL 2–10 мин.
- Откат: bypass cache.

---

## 7) Инфраструктура и деплой
**Вариант A: Render Redis**
- Плюсы: близко к приложению, низкая латентность, managed.
- Минусы: цена, ограничения тарифа.

**Вариант B: Upstash Redis**
- Плюсы: serverless, easy scaling, pay‑as‑you‑go.
- Минусы: lat/throughput может быть выше, есть ограничения по соединениям.

**ENV переменные**
- `REDIS_URL` (основная)
- `REDIS_TTL_ACCESS_STATE`, `REDIS_TTL_RECENT_HISTORY`, `REDIS_TTL_RAG`, `REDIS_TTL_TALKBUF`

**TTL/лимиты**
- Access state: 30–60s
- Recent history: 10–30 мин
- RAG cache: 2–10 мин
- Talk buffer: 20–30с

**Безопасность**
- Не хранить приватные/сырые данные без необходимости; recent_history хранить только для режима, где история допустима (privacy != none).
- Не хранить платёжные данные, токены, PII beyond what already in DB.

---

## 8) Простейшие проверки (чеклист)
- [ ] Redis доступен: test ping + лог в startup.
- [ ] access_state cache hit/miss логируется (без PII).
- [ ] history cache hit/miss логируется, fallback на DB работает.
- [ ] privacy=none: Redis history **не пишется**.
- [ ] Redis недоступен → бот продолжает работать на Postgres.
- [ ] Debounce в multi‑instance не создаёт дублей сообщений.
- [ ] RAG cache не ломает контекст (TTL короткий).

---

## Top 5 quick wins
1) Кэш `access_state:{user_id}` с TTL 30–60s (самый быстрый win и минимальный риск).
2) Кэш `recent_history:{user_id}` (сильная разгрузка `bot_messages` на каждом LLM запросе).
3) Redis‑job status для daily summaries (visibility + диагностика, вместо in‑memory `DAILY_JOBS`).
4) RAG‑кэш на короткий TTL (снижение Qdrant+embeddings cost).
5) Redis‑debounce (только при multi‑instance, чтобы не терять склейки).

## Что НЕ трогаем
- SSOT для доступа: `app/services/access_state.py` и подписки в Postgres.
- GateMiddleware / onboarding / delayed trial logic.
- Основной LLM prompt pipeline (кроме кэширования источников).
- Политику privacy=none (не писать историю туда, где запрещено).
- Qdrant как источник truth для векторов (Redis только cache).
