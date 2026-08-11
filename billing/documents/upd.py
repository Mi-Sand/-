"""
Универсальный передаточный документ (УПД).

Форма рекомендована письмом ФНС № ММВ-20-3/96@. Смысл её в том, чтобы
одной бумагой закрыть и передачу товара, и счёт-фактуру: вместо
накладной со счётом-фактурой выписывается один документ.

Ключевой реквизит — статус в левом верхнем углу:

* 1 — документ заменяет и счёт-фактуру, и накладную. Покупатель заявляет
  по нему вычет НДС;
* 2 — только передаточный документ, без счёта-фактуры. Применяется теми,
  кто НДС не платит.

Статус выставляется автоматически по ставке налога в реквизитах: при
работе без НДС счёт-фактуру выписывать не с чего, и статус 1 сделал бы
документ недостоверным.

Форма регламентирована строже накладной: ошибки в реквизитах счёта-фактуры
лишают покупателя вычета. Перед первой отправкой контрагенту бланк стоит
показать своему бухгалтеру.
"""
from io import BytesIO

from openpyxl import Workbook

from ..calc import buyer_details, order_document_data
from ..money import amount_to_words
from .common import (ALIGN_CENTER, ALIGN_LEFT, ALIGN_RIGHT, BORDER_ALL,
                     BORDER_BOTTOM, BORDER_BOX, FILL_HEADER, FONT_BOLD,
                     FONT_REGULAR, FONT_SMALL, FONT_SUBTITLE, MONEY_FORMAT,
                     QUANTITY_FORMAT, merge_and_write, money, set_widths,
                     setup_page, write_cell)

COLUMN_WIDTHS = [5, 34, 8, 8, 8, 10, 12, 13, 8, 8, 12, 14, 10, 12]
LAST_COLUMN = 'N'

HEADERS = (
    '№\nп/п',
    'Наименование товара\n(описание работ, услуг),\nимущественного права',
    'Код\nвида\nтовара',
    'Единица измерения\nкод',
    'условное\nобозна-\nчение',
    'Количество\n(объём)',
    'Цена (тариф)\nза единицу\nизмерения',
    'Стоимость товаров\n(работ, услуг) без\nналога — всего',
    'В том\nчисле\nакциз',
    'Нало-\nговая\nставка',
    'Сумма налога,\nпредъявляемая\nпокупателю',
    'Стоимость товаров\n(работ, услуг) с\nналогом — всего',
    'Страна\nпроис-\nхождения',
    'Номер\nдекларации\nна товары',
)


def build_upd(order, requisites):
    """Собрать книгу Excel с универсальным передаточным документом."""
    if requisites is None:
        raise ValueError(
            'Не заполнены реквизиты организации. Без них УПД оформить '
            'нельзя. Заполните их в админке, раздел «Реквизиты '
            'организации».')

    lines, totals = order_document_data(order, requisites)
    buyer = buyer_details(order)

    # Без НДС счёт-фактуру выписывать не с чего — только передаточный
    # документ. Статус 1 в этом случае сделал бы документ недостоверным.
    status = '1' if requisites.vat_percent is not None else '2'

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'УПД'
    setup_page(sheet, landscape=True)
    set_widths(sheet, COLUMN_WIDTHS)

    row = 1

    # --- Статус -------------------------------------------------------------
    merge_and_write(sheet, f'A{row}:B{row}',
                    'Универсальный\nпередаточный\nдокумент',
                    font=FONT_BOLD, align=ALIGN_CENTER, border=BORDER_BOX)
    merge_and_write(sheet, f'C{row}:C{row}', 'Статус',
                    font=FONT_SMALL, align=ALIGN_CENTER, border=BORDER_BOX)
    merge_and_write(sheet, f'D{row}:D{row}', status,
                    font=FONT_BOLD, align=ALIGN_CENTER, border=BORDER_BOX)
    sheet.row_dimensions[row].height = 44
    row += 1

    hint = ('1 — счёт-фактура и передаточный документ'
            if status == '1'
            else '2 — передаточный документ (без счёта-фактуры)')
    merge_and_write(sheet, f'A{row}:{LAST_COLUMN}{row}', hint,
                    font=FONT_SMALL, align=ALIGN_LEFT)
    row += 2

    # --- Заголовок ----------------------------------------------------------
    issued_at = _document_date(order)
    merge_and_write(
        sheet, f'A{row}:{LAST_COLUMN}{row}',
        f'Счёт-фактура № {order.number} от {issued_at}'
        if status == '1'
        else f'Передаточный документ № {order.number} от {issued_at}',
        font=FONT_SUBTITLE, align=ALIGN_LEFT)
    sheet.row_dimensions[row].height = 22
    row += 2

    # --- Стороны ------------------------------------------------------------
    supplier_full = ', '.join(filter(None, [
        requisites.full_name, requisites.legal_address,
    ]))
    supplier_inn = f'ИНН/КПП {requisites.inn}'
    if requisites.kpp:
        supplier_inn += f'/{requisites.kpp}'

    row = _field(sheet, row, 'Продавец:', supplier_full)
    row = _field(sheet, row, 'Адрес:', requisites.legal_address)
    row = _field(sheet, row, 'ИНН/КПП продавца:', supplier_inn)
    row = _field(sheet, row, 'Грузоотправитель и его адрес:',
                 f'{requisites.short_name}, {requisites.legal_address}')

    buyer_full = ', '.join(filter(None, [buyer['name'], buyer['address']]))
    row = _field(sheet, row, 'Грузополучатель и его адрес:', buyer_full)
    row = _field(sheet, row, 'Покупатель:', buyer['name'])
    row = _field(sheet, row, 'Адрес:', buyer['address'] or '—')
    row = _field(sheet, row, 'ИНН/КПП покупателя:',
                 buyer['requisites'] or '—')
    row = _field(sheet, row, 'Валюта: наименование, код:',
                 'Российский рубль, 643')
    row = _field(sheet, row, 'Документ об отгрузке:',
                 f'Заказ № {order.number} '
                 f'от {order.created_at.strftime("%d.%m.%Y")}')
    row += 1

    # --- Таблица ------------------------------------------------------------
    for index, header in enumerate(HEADERS, start=1):
        write_cell(sheet, row, index, header, font=FONT_SMALL,
                   align=ALIGN_CENTER, border=BORDER_ALL, fill=FILL_HEADER)
    sheet.row_dimensions[row].height = 52
    row += 1

    for index in range(1, len(HEADERS) + 1):
        write_cell(sheet, row, index, index if index > 1 else 'А',
                   font=FONT_SMALL, align=ALIGN_CENTER, border=BORDER_ALL)
    sheet.row_dimensions[row].height = 14
    row += 1

    vat_label = (f'{requisites.vat_rate}%'
                 if requisites.vat_percent is not None
                 else 'без НДС')

    for line in lines:
        name = line.name
        if line.article:
            name = f'{name} (арт. {line.article})'

        values = (
            (line.number, ALIGN_CENTER, None),
            (name, ALIGN_LEFT, None),
            ('—', ALIGN_CENTER, None),
            ('796', ALIGN_CENTER, None),           # код «штука» по ОКЕИ
            (line.unit, ALIGN_CENTER, None),
            (float(line.quantity), ALIGN_CENTER, QUANTITY_FORMAT),
            (money(line.price_without_vat), ALIGN_RIGHT, MONEY_FORMAT),
            (money(line.net), ALIGN_RIGHT, MONEY_FORMAT),
            ('без акциза', ALIGN_CENTER, None),
            (vat_label, ALIGN_CENTER, None),
            (money(line.vat), ALIGN_RIGHT, MONEY_FORMAT),
            (money(line.total), ALIGN_RIGHT, MONEY_FORMAT),
            ('—', ALIGN_CENTER, None),
            ('—', ALIGN_CENTER, None),
        )
        for index, (value, align, fmt) in enumerate(values, start=1):
            write_cell(sheet, row, index, value, align=align,
                       border=BORDER_ALL, number_format=fmt)
        row += 1

    # --- Итог ---------------------------------------------------------------
    merge_and_write(sheet, f'A{row}:G{row}', 'Всего к оплате',
                    font=FONT_BOLD, align=ALIGN_RIGHT, border=BORDER_ALL)
    write_cell(sheet, row, 8, money(totals.net), font=FONT_BOLD,
               align=ALIGN_RIGHT, border=BORDER_ALL,
               number_format=MONEY_FORMAT)
    for index in (9, 10):
        write_cell(sheet, row, index, '', border=BORDER_ALL)
    write_cell(sheet, row, 11, money(totals.vat), font=FONT_BOLD,
               align=ALIGN_RIGHT, border=BORDER_ALL,
               number_format=MONEY_FORMAT)
    write_cell(sheet, row, 12, money(totals.total), font=FONT_BOLD,
               align=ALIGN_RIGHT, border=BORDER_ALL,
               number_format=MONEY_FORMAT)
    for index in (13, 14):
        write_cell(sheet, row, index, '', border=BORDER_ALL)
    row += 2

    merge_and_write(sheet, f'A{row}:D{row}', 'Всего к оплате, прописью:',
                    font=FONT_REGULAR, align=ALIGN_LEFT)
    merge_and_write(sheet, f'E{row}:{LAST_COLUMN}{row}',
                    amount_to_words(totals.total),
                    font=FONT_BOLD, align=ALIGN_LEFT, border=BORDER_BOTTOM)
    sheet.row_dimensions[row].height = 22
    row += 2

    # --- Подписи по счёту-фактуре -------------------------------------------
    if status == '1':
        row = _signature(sheet, row, requisites.director_position,
                         requisites.director_name,
                         'Руководитель организации или иное '
                         'уполномоченное лицо')
        row = _signature(sheet, row, 'Главный бухгалтер',
                         requisites.accountant_name
                         or requisites.director_name,
                         'Главный бухгалтер или иное уполномоченное лицо')
        row += 1

    # --- Передача товара ----------------------------------------------------
    merge_and_write(sheet, f'A{row}:{LAST_COLUMN}{row}',
                    'Товар (груз) передал / услуги, результаты работ, '
                    'права сдал', font=FONT_SMALL, align=ALIGN_LEFT)
    row += 1
    row = _signature(sheet, row, requisites.director_position,
                     requisites.director_name, '')

    merge_and_write(sheet, f'A{row}:{LAST_COLUMN}{row}',
                    'Товар (груз) получил / услуги, результаты работ, '
                    'права принял', font=FONT_SMALL, align=ALIGN_LEFT)
    row += 1
    row = _signature(sheet, row, '', '', '')

    row += 1
    merge_and_write(sheet, f'A{row}:D{row}', 'Дата отгрузки, передачи:',
                    font=FONT_REGULAR, align=ALIGN_LEFT)
    merge_and_write(sheet, f'E{row}:G{row}', issued_at,
                    font=FONT_BOLD, align=ALIGN_LEFT, border=BORDER_BOTTOM)
    merge_and_write(sheet, f'H{row}:J{row}', 'М.П.', font=FONT_SMALL,
                    align=ALIGN_LEFT)

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream


def _document_date(order):
    document = order.outbound_document
    if document and document.doc_date:
        return document.doc_date.strftime('%d.%m.%Y')
    return order.created_at.strftime('%d.%m.%Y')


def _field(sheet, row, label, value):
    merge_and_write(sheet, f'A{row}:C{row}', label, font=FONT_SMALL,
                    align=ALIGN_LEFT)
    merge_and_write(sheet, f'D{row}:{LAST_COLUMN}{row}', value,
                    font=FONT_REGULAR, align=ALIGN_LEFT,
                    border=BORDER_BOTTOM)
    sheet.row_dimensions[row].height = 20
    return row + 1


def _signature(sheet, row, position, name, caption):
    merge_and_write(sheet, f'A{row}:C{row}', position, font=FONT_REGULAR,
                    align=ALIGN_LEFT, border=BORDER_BOTTOM)
    merge_and_write(sheet, f'D{row}:F{row}', '', border=BORDER_BOTTOM)
    merge_and_write(sheet, f'G{row}:J{row}', name, font=FONT_REGULAR,
                    align=ALIGN_CENTER, border=BORDER_BOTTOM)
    sheet.row_dimensions[row].height = 22

    merge_and_write(sheet, f'A{row + 1}:C{row + 1}', '(должность)',
                    font=FONT_SMALL, align=ALIGN_CENTER)
    merge_and_write(sheet, f'D{row + 1}:F{row + 1}', '(подпись)',
                    font=FONT_SMALL, align=ALIGN_CENTER)
    merge_and_write(sheet, f'G{row + 1}:J{row + 1}', '(ф. и. о.)',
                    font=FONT_SMALL, align=ALIGN_CENTER)
    if caption:
        merge_and_write(sheet, f'K{row + 1}:{LAST_COLUMN}{row + 1}',
                        caption, font=FONT_SMALL, align=ALIGN_LEFT)
    return row + 2
