"""Server-rendered interface translations. Entity values remain as stored."""
import re
from .i18n_catalog import EN

TRANSLATIONS = {
    'Панель':'Dashboard','Отчеты':'Reports','Аналитика':'Analytics','Смартфон · просмотр':'Mobile · read only',
    'Клиенты':'Clients','Компании':'Companies','Воронка':'Pipeline','Проекты':'Projects','Обращения':'Support',
    'Задачи':'Tasks','Коммерция':'Commerce','Счета':'Invoices','Расходы':'Expenses','Продвижение / SEO':'Growth / SEO',
    'Рассылки':'Broadcasts','Боты и параметры':'Bots & settings','Интеграции':'Integrations','Аудит':'Audit',
    'РАБОЧЕЕ ПРОСТРАНСТВО':'WORKSPACE','КЛИЕНТЫ И ПРОЕКТЫ':'CLIENTS & PROJECTS',
    'КОММЕРЦИЯ И РОСТ':'COMMERCE & GROWTH','СИСТЕМЫ':'SYSTEMS','ОБЗОР':'OVERVIEW',
    '☾ Темная':'☾ Dark','☼ Светлая':'☼ Light','Выйти':'Sign out',
}
TRANSLATIONS.update(EN)
NODE = re.compile(r'(?<=>)([^<>]+)(?=<)')


def localize_response(response, locale):
    if locale != 'en':
        return response
    html = response.body.decode('utf-8')
    def replace(match):
        content = match.group(1)
        stripped = content.strip().strip(' ↗↓')
        if stripped in TRANSLATIONS:
            return content.replace(stripped, TRANSLATIONS[stripped])
        for source in ('Выйти', 'БОТЫ', 'Обзор', 'Отчеты', 'Счета', 'Коммерция', 'Продвижение', 'Интеграции'):
            if content.lstrip().startswith(source + ' ') or content.lstrip().startswith(source + ' ·'):
                content = content.replace(source, TRANSLATIONS.get(source, source), 1)
        for source, target in TRANSLATIONS.items():
            if len(source) >= 18 and source in content:
                content = content.replace(source, target)
        content = re.sub(r'\bдней\b', 'days', content)
        content = re.sub(r'\bза период\b', 'in period', content)
        content = re.sub(r'\bканалов\b', 'channels', content)
        return content
    html = NODE.sub(replace, html)
    response.body = html.encode('utf-8')
    response.headers['Content-Length'] = str(len(response.body))
    response.headers['Content-Language'] = 'en'
    return response
