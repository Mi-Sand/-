"""
Счёт на оплату.

Утверждённой формы у счёта нет — есть сложившаяся практика, которой
следуют бухгалтерские программы и которую ожидает увидеть банк
плательщика. Главное в нём — банковские реквизиты получателя: по ним
покупатель заполняет платёжное поручение, и ошибка здесь означает, что
деньги уйдут не туда.

Поэтому блок с банком идёт первым и обведён рамкой, как в типовых
бланках, а не спрятан среди прочего текста.
"""
from io import BytesIO

from openpyxl import Workbook

from ..calc import buyer_details, order_document_data
from ..money import amount_to_words
from .common import (ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT, BORDER_ALL,
                     BORDER_BOTTOM, BORDER_BOX, FILL_HEADER, FONT_BOLD,
                     FONT_REGULAR, FONT_SMALL, FONT_TITLE, MONEY_FORMAT,
                     QUANTITY_FORMAT, merge_and_write, money, set_widths,
                     setup_page, write_cell)

# Ширины подобраны так, чтобы длинные наименования товаров помещались,
# а таблица оставалась в пределах листа А4 книжной ориентации.
COLUMN_WIDTHS = {'A': 5, 'B': 44, 'C': 9, 'D': 7, 'E': 13, 'F': 15}
LAST_COLUMN = 'F'


def build_invoice(order, requisites):
    """Собрать книгу Excel со счётом на оплату.

    Возвращает BytesIO с готовым файлом.
    """
    if requisites is None:
        raise ValueError(
            'Не заполнены реквизиты организации. Без них счёт выставить '
            'нельзя: покупателю неизвестно, на какой счёт платить. '
            'Заполните их в админке, раздел «Реквизиты организации».')

    lines, totals = order_document_data(order, requisites)
    buyer = buyer_details(order)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Счёт на оплату'
    setup_page(sheet)
    set_widths(sheet, COLUMN_WIDTHS)

    row = 1

    # --- Банковские реквизиты получателя ------------------------------------
    merge_and_write(sheet, f'A{row}:C{row}', requisites.bank_name,
                    font=FONT_REGULAR, align=ALIGN_LEFT, border=BORDER_ALL)
    write_cell(sheet, row, 4, 'БИК', font=FONT_SMALL, align=ALIGN_LEFT,
               border=BORDER_ALL)
    merge_and_write(sheet, f'E{row}:F{row}', requisites.bank_bik,
                    font=FONT_BOLD, align=ALIGN_LEFT, border=BORDER_ALL)
    sheet.row_dimensions[row].height = 28
    row += 1

    merge_and_write(sheet, f'A{row}:C{row}', 'Банк получателя',
                    font=FONT_SMALL, align=ALIGN_LEFT, border=BORDER_ALL)
    write_cell(sheet, row, 4, 'Сч. №', font=FONT_SMALL, align=ALIGN_LEFT,
               border=BORDER_ALL)
    merge_and_write(sheet, f'E{row}:F{row}',
                    requisites.correspondent_account or '',
                    font=FONT_REGULAR, align=ALIGN_LEFT, border=BORDER_ALL)
    row += 1

    inn_kpp = f'ИНН {requisites.inn}'
    if requisites.kpp:
        inn_kpp += f'    КПП {requisites.kpp}'
    merge_and_write(sheet, f'A{row}:C{row}', inn_kpp,
                    font=FONT_REGULAR, align=ALIGN_LEFT, border=BORDER_ALL)
    write_cell(sheet, row, 4, 'Сч. №', font=FONT_SMALL, align=ALIGN_LEFT,
               border=BORDER_ALL)
    merge_and_write(sheet, f'E{row}:F{row}', requisites.settlement_account,
                    font=FONT_BOLD, align=ALIGN_LEFT, border=BORDER_ALL)
    row += 1

    merge_and_write(sheet, f'A{row}:C{row}', requisites.short_name,
                    font=FONT_REGULAR, align=ALIGN_LEFT, border=BORDER_ALL)
    merge_and_write(sheet, f'D{row}:F{row}', '',
                    font=FONT_REGULAR, align=ALIGN_LEFT, border=BORDER_ALL)
    sheet.row_dimensions[row].height = 28
    row += 1

    merge_and_write(sheet, f'A{row}:C{row}', 'Получатель',
                    font=FONT_SMALL, align=ALIGN_LEFT, border=BORDER_ALL)
    merge_and_write(sheet, f'D{row}:F{row}', '', border=BORDER_ALL)
    row += 2

    # --- Заголовок ----------------------------------------------------------
    title = (f'Счёт на оплату № {order.number} '
             f'от {order.created_at.strftime("%d.%m.%Y")}')
    merge_and_write(sheet, f'A{row}:{LAST_COLUMN}{row}', title,
                    font=FONT_TITLE, align=ALIGN_LEFT)
    sheet.row_dimensions[row].height = 24
    row += 1

    merge_and_write(sheet, f'A{row}:{LAST_COLUMN}{row}', '',
                    border=BORDER_BOTTOM)
    row += 2

    # --- Стороны ------------------------------------------------------------
    supplier_text = ', '.join(filter(None, [
        requisites.full_name,
        f'ИНН {requisites.inn}',
        f'КПП {requisites.kpp}' if requisites.kpp else '',
        requisites.legal_address,
        f'тел. {requisites.phone}' if requisites.phone else '',
    ]))
    row = _party_row(sheet, row, 'Поставщик:', supplier_text)

    buyer_text = ', '.join(filter(None, [
        buyer['name'],
        buyer['requisites'],
        buyer['address'],
        f'тел. {buyer["phone"]}' if buyer['phone'] else '',
    ]))
    row = _party_row(sheet, row, 'Покупатель:', buyer_text)
    row += 1

    # --- Таблица позиций ----------------------------------------------------
    headers = ('№', 'Товары (работы, услуги)', 'Кол-во', 'Ед.', 'Цена',
               'Сумма')
    for index, header in enumerate(headers, start=1):
        write_cell(sheet, row, index, header, font=FONT_BOLD,
                   align=ALIGN_CENTER, border=BORDER_ALL, fill=FILL_HEADER)
    sheet.row_dimensions[row].height = 26
    row += 1

    for line in lines:
        name = line.name
        if line.article:
            name = f'{name} (арт. {line.article})'

        write_cell(sheet, row, 1, line.number, align=ALIGN_CENTER,
                   border=BORDER_ALL)
        write_cell(sheet, row, 2, name, align=ALIGN_LEFT, border=BORDER_ALL)
        write_cell(sheet, row, 3, float(line.quantity), align=ALIGN_CENTER,
                   border=BORDER_ALL, number_format=QUANTITY_FORMAT)
        write_cell(sheet, row, 4, line.unit, align=ALIGN_CENTER,
                   border=BORDER_ALL)
        write_cell(sheet, row, 5, money(line.price), align=ALIGN_RIGHT,
                   border=BORDER_ALL, number_format=MONEY_FORMAT)
        write_cell(sheet, row, 6, money(line.total), align=ALIGN_RIGHT,
                   border=BORDER_ALL, number_format=MONEY_FORMAT)
        row += 1

    # --- Итоги --------------------------------------------------------------
    row = _total_row(sheet, row, 'Итого:', totals.total)

    if requisites.vat_percent is None:
        row = _total_row(sheet, row, 'Без налога (НДС):', None,
                         text='—')
    else:
        label = f'В том числе НДС {requisites.vat_display}:'
        if not requisites.vat_included_in_price:
            label = f'Сумма НДС {requisites.vat_display}:'
        row = _total_row(sheet, row, label, totals.vat)

    row = _total_row(sheet, row, 'Всего к оплате:', totals.total,
                     bold=True)
    row += 1

    # --- Сумма прописью -----------------------------------------------------
    # Разряды отделяем пробелами, но только в числе: replace по всей
    # строке съел бы и запятую после количества наименований.
    amount_text = f'{money(totals.total):,.2f}'.replace(',', ' ')
    summary = (f'Всего наименований {totals.line_count}, '
               f'на сумму {amount_text} руб.')
    merge_and_write(sheet, f'A{row}:{LAST_COLUMN}{row}', summary,
                    font=FONT_REGULAR, align=ALIGN_LEFT)
    row += 1

    merge_and_write(sheet, f'A{row}:{LAST_COLUMN}{row}',
                    amount_to_words(totals.total),
                    font=FONT_BOLD, align=ALIGN_LEFT)
    sheet.row_dimensions[row].height = 22
    row += 1

    merge_and_write(sheet, f'A{row}:{LAST_COLUMN}{row}', '',
                    border=BORDER_BOTTOM)
    row += 2

    # --- Условия и подписи --------------------------------------------------
    merge_and_write(
        sheet, f'A{row}:{LAST_COLUMN}{row}',
        'Оплата данного счёта означает согласие с условиями поставки. '
        'Товар отпускается по факту прихода денег на расчётный счёт '
        'поставщика.',
        font=FONT_SMALL, align=ALIGN_LEFT)
    sheet.row_dimensions[row].height = 24
    row += 2

    row = _signature(sheet, row, requisites.director_position,
                     requisites.director_name)
    if requisites.accountant_name:
        row = _signature(sheet, row, 'Главный бухгалтер',
                         requisites.accountant_name)

    # Рамка вокруг банковского блока — как в типовых бланках
    for line_row in sheet['A1:F5']:
        for cell in line_row:
            cell.border = BORDER_ALL
    for cell in sheet['A1:F1'][0]:
        cell.border = BORDER_BOX

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def _party_row(sheet, row, label, text):
    """Строка «Поставщик:» или «Покупатель:» с подчёркиванием."""
    write_cell(sheet, row, 1, label, font=FONT_BOLD, align=ALIGN_LEFT)
    merge_and_write(sheet, f'B{row}:{LAST_COLUMN}{row}', text,
                    font=FONT_REGULAR, align=ALIGN_LEFT,
                    border=BORDER_BOTTOM)
    sheet.row_dimensions[row].height = 30
    return row + 1


def _total_row(sheet, row, label, value, bold=False, text=None):
    """Строка итога справа под таблицей."""
    font = FONT_BOLD if bold else FONT_REGULAR
    merge_and_write(sheet, f'D{row}:E{row}', label, font=font,
                    align=ALIGN_RIGHT)
    if text is not None:
        write_cell(sheet, row, 6, text, font=font, align=ALIGN_RIGHT)
    else:
        write_cell(sheet, row, 6, money(value), font=font,
                   align=ALIGN_RIGHT, number_format=MONEY_FORMAT)
    return row + 1


def _signature(sheet, row, title, name):
    """Подпись: должность, линия для росчерка, расшифровка."""
    write_cell(sheet, row, 1, title, font=FONT_REGULAR, align=ALIGN_LEFT)
    merge_and_write(sheet, f'C{row}:D{row}', '', border=BORDER_BOTTOM)
    merge_and_write(sheet, f'E{row}:F{row}', name, font=FONT_REGULAR,
                    align=ALIGN_CENTER, border=BORDER_BOTTOM)
    sheet.row_dimensions[row].height = 24
    write_cell(sheet, row + 1, 3, '(подпись)', font=FONT_SMALL,
               align=ALIGN_CENTER)
    write_cell(sheet, row + 1, 5, '(расшифровка)', font=FONT_SMALL,
               align=ALIGN_CENTER)
    return row + 3
