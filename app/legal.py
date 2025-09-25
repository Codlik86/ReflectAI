# app/legal.py
from fastapi import APIRouter, Response

router = APIRouter(tags=["legal"])

REKV_HTML = """
<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Реквизиты</title>
<body style="font:16px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial">
<h1>Реквизиты самозанятого</h1>
<p><b>ФИО:</b> Сафронов Елисей Егорович</p>
<p><b>Статус:</b> физическое лицо, самозанятый (НПД)</p>
<p><b>ИНН:</b> 772917893200</p>
<p><b>Email:</b> selflect@proton.me</p>
<p><b>Телефон:</b> +7 999 979-66-25</p>
<p><b>Сервис:</b> «Помни» — телеграм-бот эмоциональной поддержки.</p>
<p><b>Прием платежей:</b> ЮKassa (ООО «ЮMoney»), чек высылается автоматически.</p>
<p><b>Возвраты:</b> в течение 7 дней с момента списания по обращению на e-mail, если услуга не была оказана. Подписка может быть отменена в боте.</p>
<p>Политика и оферта: <a href="/legal/offer">/legal/offer</a></p>
</body></html>
"""

OFFER_HTML = """
<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Пользовательское соглашение (оферта)</title>
<body style="font:16px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial">
<h1>Пользовательское соглашение (оферта) сервиса «Помни»</h1>
<p>1. Исполнитель: Сафронов Елисей Егорович, самозанятый (НПД), ИНН 772917893200.</p>
<p>2. Сервис: доступ к функциям бота (Разобрать, Поговорить, Медитации). Пробный период 5 дней, далее подписка по тарифу.</p>
<p>3. Оплата: через ЮKassa банковскими картами/СПБ. Чек направляется автоматически.</p>
<p>4. Автопродление: включается при подключении тарифа, можно отключить в боте.</p>
<p>5. Отмена и возврат: подписку можно отменить в любое время; возврат в течение 7 дней, если не было оказания услуги, по обращению на selflect@proton.me.</p>
<p>6. Обработка данных: по Политике конфиденциальности.</p>
<p>7. Реквизиты и контакты: см. <a href="/legal/requisites">/legal/requisites</a>.</p>
</body></html>
"""

@router.get("/legal/requisites")
async def legal_requisites():
    return Response(content=REKV_HTML, media_type="text/html; charset=utf-8")

@router.get("/legal/offer")
async def legal_offer():
    return Response(content=OFFER_HTML, media_type="text/html; charset=utf-8")
