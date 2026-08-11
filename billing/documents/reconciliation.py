"""
Акт сверки взаимных расчётов.

Документ показывает движение по расчётам с покупателем за период: слева
обороты по данным продавца, справа — место для данных покупателя, внизу
итоговое сальдо. Подписанный обеими сторонами акт подтверждает долг и
прерывает течение срока исковой давности.

Что считается оборотом:

* отгрузка — увеличивает долг покупателя (дебет);
* оплата — уменьшает его (кредит).

Заказ попадает в акт по дате отгрузки, а не оформления: до отгрузки
обязательство ещё не возникло, и включать такой заказ в расчёты рано.
Отменённые заказы не учитываются вовсе.
"""
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from ..money import amount_to_words, plural_form, round_money
from .common import (ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT, BORDER_ALL,
                     BORDER_BOTTOM, FILL_HEADER, FONT_BOLD, FONT_REGULAR,
                     FONT_SMALL, FONT_SUBTITLE, MONEY_FORMAT,
                     merge_and_write, money, set_widths, setup_page,
                     write_cell)

COLUMN_WIDTHS = [12, 34, 14, 14, 12, 34, 14, 14]
LAST_COLUMN = 'H'

DEBT_FORMS = ('рубль', 'рубля', 'рублей')


def collect_movements(orders, payments):
    """Собрать обороты за период в хронологическом порядке.

    Возвращает список словарей: дата, содержание, дебет, кредит.
    """
    movements = []

    for order in orders:
        document = order.outbound_document
        date = document.doc_date if document else order.created_at.date()
        number = document.doc_number if document else order.number
        movements.append({
            'date': date,
            'text': f'Отгрузка по накладной № {number}',
            'debit': round_money(order.total),
            'credit': Decimal('0.00'),
        })

    for payment in payments:
        text = f'Оплата ({payment.get_method_display().lower()}'
        if payment.document_number:
            text += f', док. № {payment.document_number}'
        text += ')'
        movements.append({
            'date': payment.paid_at,
            'text': text,
            'debit': Decimal('0.00'),
            'credit': round_money(payment.amount),
        })

    movements.sort(key=lambda item: item['date'])
    return movements


def build_reconciliation(counterparty_name, movements, period_start,
                         period_end, requisites, opening_balance=None):
    """Собрать книгу Excel с актом сверки.

    opening_balance — сальдо на начало периода: сколько покупатель был
    должен до первой операции в акте. Без него акт за произвольный
    период показывал бы только обороты внутри него и расходился бы с
    реальным долгом.
    """
    if requisites is None:
        raise ValueError(
            'Не заполнены реквизиты организации. Заполните их в админке, '
            'раздел «Реквизиты организации».')

    opening_balance = Decimal(opening_balance or 0)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Акт сверки'
    setup_page(sheet, landscape=True)
    set_widths(sheet, COLUMN_WIDTHS)

    row = 1

    # --- Заголовок ----------------------------------------------------------
    merge_and_write(
        sheet, f'A{row}:{LAST_COLUMN}{row}',
        f'АКТ СВЕРКИ ВЗАИМНЫХ РАСЧЁТОВ '
        f'за период с {period_start.strftime("%d.%m.%Y")} '
        f'по {period_end.strftime("%d.%m.%Y")}',
        font=FONT_SUBTITLE, align=ALIGN_CENTER)
    sheet.row_dimensions[row].height = 24
    row += 1

    merge_and_write(
        sheet, f'A{row}:{LAST_COLUMN}{row}',
        f'между {requisites.short_name} и {counterparty_name}',
        font=FONT_REGULAR, align=ALIGN_CENTER)
    row += 2

    merge_and_write(
        sheet, f'A{row}:{LAST_COLUMN}{row}',
        f'Мы, нижеподписавшиеся, представитель '
        f'{requisites.short_name}, с одной стороны, и представитель '
        f'{counterparty_name}, с другой стороны, составили настоящий акт '
        f'о том, что состояние взаимных расчётов по данным учёта '
        f'следующее:',
        font=FONT_REGULAR, align=ALIGN_LEFT)
    sheet.row_dimensions[row].height = 30
    row += 2

    # --- Шапка таблицы ------------------------------------------------------
    merge_and_write(sheet, f'A{row}:D{row}',
                    f'По данным {requisites.short_name}',
                    font=FONT_BOLD, align=ALIGN_CENTER, border=BORDER_ALL,
                    fill=FILL_HEADER)
    merge_and_write(sheet, f'E{row}:{LAST_COLUMN}{row}',
                    f'По данным {counterparty_name}',
                    font=FONT_BOLD, align=ALIGN_CENTER, border=BORDER_ALL,
                    fill=FILL_HEADER)
    row += 1

    headers = ('Дата', 'Документ', 'Дебет', 'Кредит',
               'Дата', 'Документ', 'Дебет', 'Кредит')
    for index, header in enumerate(headers, start=1):
        write_cell(sheet, row, index, header, font=FONT_SMALL,
                   align=ALIGN_CENTER, border=BORDER_ALL, fill=FILL_HEADER)
    row += 1

    # --- Сальдо на начало ---------------------------------------------------
    row = _balance_row(sheet, row, 'Сальдо начальное', opening_balance)

    # --- Обороты ------------------------------------------------------------
    debit_total = Decimal('0.00')
    credit_total = Decimal('0.00')

    for movement in movements:
        debit_total += movement['debit']
        credit_total += movement['credit']

        write_cell(sheet, row, 1, movement['date'].strftime('%d.%m.%Y'),
                   align=ALIGN_CENTER, border=BORDER_ALL)
        write_cell(sheet, row, 2, movement['text'], align=ALIGN_LEFT,
                   border=BORDER_ALL)
        write_cell(sheet, row, 3,
                   money(movement['debit']) if movement['debit'] else '',
                   align=ALIGN_RIGHT, border=BORDER_ALL,
                   number_format=MONEY_FORMAT)
        write_cell(sheet, row, 4,
                   money(movement['credit']) if movement['credit'] else '',
                   align=ALIGN_RIGHT, border=BORDER_ALL,
                   number_format=MONEY_FORMAT)
        # Правая половина остаётся пустой: её заполняет покупатель,
        # сверяя со своим учётом. В этом и смысл акта.
        for index in range(5, 9):
            write_cell(sheet, row, index, '', border=BORDER_ALL)
        row += 1

    # --- Обороты за период --------------------------------------------------
    merge_and_write(sheet, f'A{row}:B{row}', 'Обороты за период',
                    font=FONT_BOLD, align=ALIGN_RIGHT, border=BORDER_ALL)
    write_cell(sheet, row, 3, money(debit_total), font=FONT_BOLD,
               align=ALIGN_RIGHT, border=BORDER_ALL,
               number_format=MONEY_FORMAT)
    write_cell(sheet, row, 4, money(credit_total), font=FONT_BOLD,
               align=ALIGN_RIGHT, border=BORDER_ALL,
               number_format=MONEY_FORMAT)
    merge_and_write(sheet, f'E{row}:F{row}', 'Обороты за период',
                    font=FONT_BOLD, align=ALIGN_RIGHT, border=BORDER_ALL)
    for index in (7, 8):
        write_cell(sheet, row, index, '', border=BORDER_ALL)
    row += 1

    # --- Сальдо на конец ----------------------------------------------------
    closing = opening_balance + debit_total - credit_total
    row = _balance_row(sheet, row, 'Сальдо конечное', closing)
    row += 1

    # --- Итог словами -------------------------------------------------------
    if closing > 0:
        verdict = (f'На {period_end.strftime("%d.%m.%Y")} задолженность '
                   f'{counterparty_name} в пользу {requisites.short_name} '
                   f'составляет {money(closing):.2f} '
                   f'{plural_form(int(closing), DEBT_FORMS)}')
    elif closing < 0:
        verdict = (f'На {period_end.strftime("%d.%m.%Y")} задолженность '
                   f'{requisites.short_name} в пользу {counterparty_name} '
                   f'составляет {money(abs(closing)):.2f} '
                   f'{plural_form(int(abs(closing)), DEBT_FORMS)}')
    else:
        verdict = (f'На {period_end.strftime("%d.%m.%Y")} задолженность '
                   f'сторон друг перед другом отсутствует')

    merge_and_write(sheet, f'A{row}:{LAST_COLUMN}{row}', verdict,
                    font=FONT_BOLD, align=ALIGN_LEFT)
    sheet.row_dimensions[row].height = 22
    row += 1

    if closing != 0:
        merge_and_write(sheet, f'A{row}:{LAST_COLUMN}{row}',
                        f'({amount_to_words(abs(closing))})',
                        font=FONT_REGULAR, align=ALIGN_LEFT)
        row += 1
    row += 1

    # --- Подписи ------------------------------------------------------------
    merge_and_write(sheet, f'A{row}:D{row}',
                    f'От {requisites.short_name}', font=FONT_BOLD,
                    align=ALIGN_LEFT)
    merge_and_write(sheet, f'E{row}:{LAST_COLUMN}{row}',
                    f'От {counterparty_name}', font=FONT_BOLD,
                    align=ALIGN_LEFT)
    row += 2

    merge_and_write(sheet, f'A{row}:B{row}',
                    requisites.director_position, font=FONT_REGULAR,
                    align=ALIGN_LEFT, border=BORDER_BOTTOM)
    merge_and_write(sheet, f'C{row}:D{row}', requisites.director_name,
                    font=FONT_REGULAR, align=ALIGN_CENTER,
                    border=BORDER_BOTTOM)
    merge_and_write(sheet, f'E{row}:F{row}', '', border=BORDER_BOTTOM)
    merge_and_write(sheet, f'G{row}:{LAST_COLUMN}{row}', '',
                    border=BORDER_BOTTOM)
    sheet.row_dimensions[row].height = 24
    row += 1

    for start, end in (('A', 'B'), ('C', 'D'), ('E', 'F'), ('G', LAST_COLUMN)):
        caption = '(должность)' if start in ('A', 'E') else '(подпись, ф. и. о.)'
        merge_and_write(sheet, f'{start}{row}:{end}{row}', caption,
                        font=FONT_SMALL, align=ALIGN_CENTER)
    row += 2

    merge_and_write(sheet, f'A{row}:D{row}', 'М.П.', font=FONT_SMALL,
                    align=ALIGN_LEFT)
    merge_and_write(sheet, f'E{row}:{LAST_COLUMN}{row}', 'М.П.',
                    font=FONT_SMALL, align=ALIGN_LEFT)

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def _balance_row(sheet, row, label, balance):
    """Строка сальдо: долг покупателя в дебет, переплата в кредит."""
    merge_and_write(sheet, f'A{row}:B{row}', label, font=FONT_BOLD,
                    align=ALIGN_RIGHT, border=BORDER_ALL)
    write_cell(sheet, row, 3, money(balance) if balance > 0 else '',
               font=FONT_BOLD, align=ALIGN_RIGHT, border=BORDER_ALL,
               number_format=MONEY_FORMAT)
    write_cell(sheet, row, 4, money(-balance) if balance < 0 else '',
               font=FONT_BOLD, align=ALIGN_RIGHT, border=BORDER_ALL,
               number_format=MONEY_FORMAT)
    merge_and_write(sheet, f'E{row}:F{row}', label, font=FONT_BOLD,
                    align=ALIGN_RIGHT, border=BORDER_ALL)
    for index in (7, 8):
        write_cell(sheet, row, index, '', border=BORDER_ALL)
    return row + 1
