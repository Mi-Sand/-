"""
Товарная накладная ТОРГ-12.

Форма унифицированная (постановление Госкомстата № 132). С 2013 года она
не обязательна — организация вправе применять свою, — но ТОРГ-12 остаётся
самой привычной, и покупатели чаще всего ждут именно её.

Бланк широкий: пятнадцать граф, часть из которых в рознице не заполняется
(вид упаковки, масса брутто, места). Пустыми их оставлять нельзя —
ставится прочерк, иначе документ выглядит недооформленным.

Печатается в альбомной ориентации: в книжную таблица не помещается,
а перенос части граф на второй лист сделал бы накладную нечитаемой.
"""
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from ..calc import buyer_details, order_document_data
from ..money import amount_to_words, number_to_words, plural_form
from .common import (ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT, BORDER_ALL,
                     BORDER_BOTTOM, FILL_HEADER, FONT_BOLD, FONT_REGULAR,
                     FONT_SMALL, FONT_SUBTITLE, MONEY_FORMAT,
                     QUANTITY_FORMAT, merge_and_write, money, set_widths,
                     setup_page, write_cell)

# 13 граф: номер, наименование, код, единица, код по ОКЕИ, вид упаковки,
# мест, масса брутто, масса нетто, цена, сумма без НДС, ставка НДС,
# сумма НДС, сумма с НДС
COLUMN_WIDTHS = [4, 34, 8, 7, 7, 9, 7, 9, 9, 11, 13, 7, 11, 13]
LAST_COLUMN = 'N'

HEADERS = (
    '№\nп/п',
    'Товар\n(наименование, характеристика, сорт, артикул)',
    'Код',
    'Единица измерения\nнаимено-\nвание',
    'код по\nОКЕИ',
    'Вид упа-\nковки',
    'Количество\nв одном\nместе',
    'Коли-\nчество\nмест',
    'Масса\nбрутто',
    'Цена,\nруб. коп.',
    'Сумма без учёта\nНДС, руб. коп.',
    'НДС\nставка,\n%',
    'Сумма\nНДС,\nруб. коп.',
    'Сумма с учётом\nНДС, руб. коп.',
)


def build_torg12(order, requisites):
    """Собрать книгу Excel с товарной накладной ТОРГ-12."""
    if requisites is None:
        raise ValueError(
            'Не заполнены реквизиты организации. Без них накладную '
            'оформить нельзя. Заполните их в админке, раздел «Реквизиты '
            'организации».')

    lines, totals = order_document_data(order, requisites)
    buyer = buyer_details(order)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'ТОРГ-12'
    setup_page(sheet, landscape=True)
    set_widths(sheet, COLUMN_WIDTHS)

    row = 1

    # --- Шапка со сторонами -------------------------------------------------
    supplier_text = ', '.join(filter(None, [
        requisites.full_name,
        f'ИНН {requisites.inn}',
        f'КПП {requisites.kpp}' if requisites.kpp else '',
        requisites.legal_address,
        f'тел. {requisites.phone}' if requisites.phone else '',
    ]))
    row = _party_row(sheet, row, 'Грузоотправитель, организация',
                     supplier_text)
    row = _party_row(sheet, row, 'Структурное подразделение', '—')

    buyer_text = ', '.join(filter(None, [
        buyer['name'], buyer['requisites'], buyer['address'],
        f'тел. {buyer["phone"]}' if buyer['phone'] else '',
    ]))
    row = _party_row(sheet, row, 'Грузополучатель', buyer_text)
    row = _party_row(sheet, row, 'Поставщик', supplier_text)
    row = _party_row(sheet, row, 'Плательщик', buyer_text)
    row = _party_row(sheet, row, 'Основание',
                     f'Заказ № {order.number} '
                     f'от {order.created_at.strftime("%d.%m.%Y")}')
    row += 1

    # --- Заголовок ----------------------------------------------------------
    shipped_at = _shipment_date(order)
    merge_and_write(
        sheet, f'A{row}:{LAST_COLUMN}{row}',
        f'ТОВАРНАЯ НАКЛАДНАЯ № {order.number} от {shipped_at}',
        font=FONT_SUBTITLE, align=ALIGN_CENTER)
    sheet.row_dimensions[row].height = 22
    row += 2

    # --- Таблица ------------------------------------------------------------
    header_row = row
    for index, header in enumerate(HEADERS, start=1):
        write_cell(sheet, row, index, header, font=FONT_SMALL,
                   align=ALIGN_CENTER, border=BORDER_ALL, fill=FILL_HEADER)
    sheet.row_dimensions[row].height = 46
    row += 1

    # Нумерация граф — обязательная строка унифицированной формы: на неё
    # ссылаются при сверке и при заполнении от руки.
    for index in range(1, len(HEADERS) + 1):
        write_cell(sheet, row, index, index, font=FONT_SMALL,
                   align=ALIGN_CENTER, border=BORDER_ALL)
    sheet.row_dimensions[row].height = 14
    row += 1

    vat_label = (requisites.vat_rate
                 if requisites.vat_percent is not None else 'без НДС')

    for line in lines:
        name = line.name
        if line.article:
            name = f'{name} (арт. {line.article})'

        values = (
            (line.number, ALIGN_CENTER, None),
            (name, ALIGN_LEFT, None),
            ('—', ALIGN_CENTER, None),
            (line.unit, ALIGN_CENTER, None),
            ('796', ALIGN_CENTER, None),          # код «штука» по ОКЕИ
            ('—', ALIGN_CENTER, None),
            ('—', ALIGN_CENTER, None),
            ('—', ALIGN_CENTER, None),
            ('—', ALIGN_CENTER, None),
            (money(line.price_without_vat), ALIGN_RIGHT, MONEY_FORMAT),
            (money(line.net), ALIGN_RIGHT, MONEY_FORMAT),
            (vat_label, ALIGN_CENTER, None),
            (money(line.vat), ALIGN_RIGHT, MONEY_FORMAT),
            (money(line.total), ALIGN_RIGHT, MONEY_FORMAT),
        )
        for index, (value, align, fmt) in enumerate(values, start=1):
            write_cell(sheet, row, index, value, align=align,
                       border=BORDER_ALL, number_format=fmt)

        # Количество отдельной графой: в унифицированной форме оно
        # находится в графе «Количество мест», которую в рознице
        # используют как обычное количество товара.
        write_cell(sheet, row, 8, float(line.quantity), align=ALIGN_CENTER,
                   border=BORDER_ALL, number_format=QUANTITY_FORMAT)
        row += 1

    # --- Итог таблицы -------------------------------------------------------
    merge_and_write(sheet, f'A{row}:G{row}', 'Итого', font=FONT_BOLD,
                    align=ALIGN_RIGHT, border=BORDER_ALL)
    write_cell(sheet, row, 8, float(totals.quantity), font=FONT_BOLD,
               align=ALIGN_CENTER, border=BORDER_ALL,
               number_format=QUANTITY_FORMAT)
    for index in (9, 10, 12):
        write_cell(sheet, row, index, '', border=BORDER_ALL)
    write_cell(sheet, row, 11, money(totals.net), font=FONT_BOLD,
               align=ALIGN_RIGHT, border=BORDER_ALL,
               number_format=MONEY_FORMAT)
    write_cell(sheet, row, 13, money(totals.vat), font=FONT_BOLD,
               align=ALIGN_RIGHT, border=BORDER_ALL,
               number_format=MONEY_FORMAT)
    write_cell(sheet, row, 14, money(totals.total), font=FONT_BOLD,
               align=ALIGN_RIGHT, border=BORDER_ALL,
               number_format=MONEY_FORMAT)
    row += 2

    # --- Итоги прописью -----------------------------------------------------
    row = _summary_row(
        sheet, row, 'Товарная накладная имеет приложение на',
        '___ листах')
    record_forms = ('порядковый номер записи', 'порядковых номера записей',
                    'порядковых номеров записей')
    row = _summary_row(
        sheet, row, 'и содержит',
        f'{totals.line_count} ({_words(totals.line_count)}) '
        f'{plural_form(totals.line_count, record_forms)}')
    row = _summary_row(sheet, row, 'Всего мест',
                       _plain_number(totals.quantity))
    row = _summary_row(sheet, row, 'Всего отпущено на сумму',
                       amount_to_words(totals.total))
    row += 1

    # --- Подписи ------------------------------------------------------------
    row = _signature_pair(
        sheet, row,
        left=('Отпуск груза разрешил', requisites.director_position,
              requisites.director_name),
        right=('Груз принял', '', ''))
    row = _signature_pair(
        sheet, row,
        left=('Главный бухгалтер', '',
              requisites.accountant_name or requisites.director_name),
        right=('Груз получил грузополучатель', '', ''))
    row += 1

    merge_and_write(sheet, f'A{row}:G{row}', 'М.П.', font=FONT_SMALL,
                    align=ALIGN_LEFT)
    merge_and_write(sheet, f'H{row}:{LAST_COLUMN}{row}', 'М.П.',
                    font=FONT_SMALL, align=ALIGN_LEFT)

    sheet.freeze_panes = sheet.cell(row=header_row + 2, column=1)

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def _shipment_date(order):
    """Дата накладной: день отгрузки, а если её ещё не было — день заказа."""
    document = order.outbound_document
    if document and document.doc_date:
        return document.doc_date.strftime('%d.%m.%Y')
    return order.created_at.strftime('%d.%m.%Y')


def _words(number):
    """Число словами — для строки «содержит N (…) записей»."""
    return number_to_words(number)


def _plain_number(value):
    """Количество без лишних нулей: 1 вместо 1.00, но 1.5 сохраняется."""
    normalized = Decimal(value).normalize()
    if normalized == normalized.to_integral_value():
        return str(int(normalized))
    return format(normalized, 'f')


def _party_row(sheet, row, label, text):
    merge_and_write(sheet, f'A{row}:C{row}', label, font=FONT_SMALL,
                    align=ALIGN_LEFT)
    merge_and_write(sheet, f'D{row}:{LAST_COLUMN}{row}', text,
                    font=FONT_REGULAR, align=ALIGN_LEFT,
                    border=BORDER_BOTTOM)
    sheet.row_dimensions[row].height = 22
    return row + 1


def _summary_row(sheet, row, label, value):
    merge_and_write(sheet, f'A{row}:D{row}', label, font=FONT_REGULAR,
                    align=ALIGN_LEFT)
    merge_and_write(sheet, f'E{row}:{LAST_COLUMN}{row}', value,
                    font=FONT_BOLD, align=ALIGN_LEFT, border=BORDER_BOTTOM)
    sheet.row_dimensions[row].height = 20
    return row + 1


def _signature_pair(sheet, row, left, right):
    """Две подписи в строке: слева поставщик, справа покупатель."""
    left_title, left_position, left_name = left
    right_title, right_position, right_name = right

    write_cell(sheet, row, 1, left_title, font=FONT_SMALL, align=ALIGN_LEFT)
    merge_and_write(sheet, f'C{row}:D{row}', left_position or '',
                    font=FONT_REGULAR, align=ALIGN_CENTER,
                    border=BORDER_BOTTOM)
    merge_and_write(sheet, f'E{row}:G{row}', left_name,
                    font=FONT_REGULAR, align=ALIGN_CENTER,
                    border=BORDER_BOTTOM)

    write_cell(sheet, row, 8, right_title, font=FONT_SMALL, align=ALIGN_LEFT)
    merge_and_write(sheet, f'J{row}:K{row}', right_position or '',
                    font=FONT_REGULAR, align=ALIGN_CENTER,
                    border=BORDER_BOTTOM)
    merge_and_write(sheet, f'L{row}:{LAST_COLUMN}{row}', right_name,
                    font=FONT_REGULAR, align=ALIGN_CENTER,
                    border=BORDER_BOTTOM)
    sheet.row_dimensions[row].height = 24
    return row + 2
