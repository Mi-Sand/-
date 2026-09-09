"""Страница отказа при неудачной проверке подлинности запроса (CSRF).

Django на такой отказ отвечает одной строкой: «Ошибка проверки CSRF.
Запрос отклонён». Строка честная, но по ней нельзя понять ничего —
ни что случилось, ни что делать. А случается это почти всегда по одной
из двух причин, и обе распознаются прямо в запросе.

Причина первая, самая злая. Перед системой стоит nginx с шифрованием,
сотрудник открывает страницу по https — а системе об этом не сказали
(USE_HTTPS выключен). Django считает соединение обычным, браузер
присылает пометку «я пришёл с https», они не совпадают, и запрос
отклоняется. Страница входа при этом открывается прекрасно: проверка
идёт только на отправку формы. Со стороны выглядит как «пароль не
подходит», хотя до пароля дело не доходит вовсе.

Причина вторая. Браузер не прислал куку. Так бывает после выключения
HTTPS: выданные при нём куки помечены «только по защищённому
соединению», по обычному http браузер их не шлёт и заменить не даёт.

Обе распознаются и обе объясняются здесь — вместе с тем, что вписать
в .env. Ничего тайного страница не раскрывает: только то, что и так
пришло в самом запросе.
"""
from django.http import HttpResponseForbidden
from django.utils.html import escape

# Django просит именно такую подпись: (request, reason). Свой шаблон не
# используем — страница должна открыться даже тогда, когда с оформлением
# что-то не так.
TEMPLATE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Запрос отклонён</title>
<style>
 body {{ font-family: system-ui, "Segoe UI", sans-serif; margin: 0;
        background: #f5f5f5; color: #222; }}
 .box {{ max-width: 44rem; margin: 3rem auto; background: #fff;
         border-radius: 8px; padding: 1.5rem 2rem;
         box-shadow: 0 1px 4px rgba(0,0,0,.15); }}
 h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
 .code {{ color: #888; font-weight: normal; font-size: 1rem; }}
 .why {{ background: #fff8e1; border-left: 4px solid #f0b429;
         padding: .75rem 1rem; margin: 1.25rem 0; }}
 pre {{ background: #f0f0f0; padding: .75rem 1rem; border-radius: 4px;
        overflow-x: auto; }}
 .quiet {{ color: #666; font-size: .9rem; }}
</style></head><body><div class="box">
<h1>Запрос отклонён <span class="code">(403)</span></h1>
<p>Система не приняла отправленную форму: не сошлась проверка
подлинности запроса.</p>
{explanation}
<p class="quiet">Если ничего из этого не подходит, выполните в папке
программы <code>python manage.py checksetup</code> — проверка назовёт
несогласованные настройки.</p>
</div></body></html>
"""

PROXY_MISMATCH = """
<div class="why">
<p><b>Похоже, дело в настройках шифрования, а не в вас.</b></p>
<p>Страницу вы открыли по <b>https</b>, а система считает соединение
обычным. Так бывает, когда перед ней стоит nginx с сертификатом, но
самой системе об этом не сказали. Страница входа при этом открывается
как ни в чём не бывало — проверка срабатывает только на отправку формы,
до пароля дело не доходит.</p>
<p>Что вписать в файл <code>.env</code> в папке программы:</p>
<pre>USE_HTTPS=True
CSRF_TRUSTED_ORIGINS={origin}</pre>
<p>После этого перезапустите систему. Порядок целиком — в HTTPS.md.</p>
<p class="quiet">Если шифрование вам не нужно, есть и обратный путь:
остановить nginx и открывать систему напрямую, по обычному http.</p>
</div>
"""

NO_COOKIE = """
<div class="why">
<p><b>Браузер не прислал служебную куку.</b></p>
<p>Чаще всего так бывает после выключения HTTPS: куки, выданные при
нём, помечены «только по защищённому соединению». По обычному http
браузер их не отправляет и заменить не разрешает — получается тупик,
который переживает и перезапуск, и очистку кэша.</p>
<p>Удалите в браузере куки для адреса системы: <b>Ctrl+Shift+Delete</b>,
пункт «Файлы cookie и другие данные сайтов». Проверить догадку быстрее
всего в окне InPrivate — там старых кук нет.</p>
<p class="quiet">Реже причина проще: куки запрещены в настройках
браузера, либо форма пролежала открытой больше суток — тогда достаточно
обновить страницу.</p>
</div>
"""

GENERIC = """
<div class="why">
<p>Чаще всего достаточно обновить страницу и повторить: форма могла
пролежать открытой слишком долго.</p>
<p>Если повторяется — проверьте, что в браузере разрешены куки, а
система открыта по тому же адресу, что указан в настройках.</p>
</div>
"""


def _origin_scheme_mismatch(request):
    """Браузер пришёл по https, а система считает соединение обычным.

    Смотрим на две пометки. Origin браузер шлёт сам, его не подделать
    страницей со стороны. X-Forwarded-Proto добавляет nginx — она есть
    и тогда, когда Origin браузер не прислал.
    """
    if request.is_secure():
        return False
    origin = request.META.get('HTTP_ORIGIN', '')
    if origin.startswith('https://'):
        return True
    forwarded = request.META.get('HTTP_X_FORWARDED_PROTO', '')
    return forwarded.split(',')[0].strip().lower() == 'https'


def csrf_failure(request, reason=''):
    """Отказ с объяснением вместо отказа с одной строкой."""
    from django.conf import settings

    if _origin_scheme_mismatch(request):
        host = request.get_host()
        explanation = PROXY_MISMATCH.format(origin=escape(f'https://{host}'))
    elif not request.COOKIES.get(settings.CSRF_COOKIE_NAME):
        explanation = NO_COOKIE
    else:
        explanation = GENERIC

    return HttpResponseForbidden(
        TEMPLATE.format(explanation=explanation),
        content_type='text/html; charset=utf-8')
