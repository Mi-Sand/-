"""
Общие средства вёрстки бланков.

Все документы печатаются на А4 и должны на него помещаться: бухгалтерия
их подписывает и подшивает, а форма, съезжающая на второй лист из-за
одной лишней колонки, каждый раз требует ручной подгонки перед печатью.
Поэтому ширины столбцов, поля и масштаб задаются явно, а не оставляются
на усмотрение Excel.
"""
from decimal import Decimal

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# Шрифт с засечками — как в типовых бухгалтерских бланках
FONT_NAME = 'Times New Roman'

FONT_REGULAR = Font(name=FONT_NAME, size=9)
FONT_SMALL = Font(name=FONT_NAME, size=7)
FONT_BOLD = Font(name=FONT_NAME, size=9, bold=True)
FONT_TITLE = Font(name=FONT_NAME, size=14, bold=True)
FONT_SUBTITLE = Font(name=FONT_NAME, size=11, bold=True)

THIN = Side(style='thin', color='000000')
MEDIUM = Side(style='medium', color='000000')

BORDER_ALL = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BORDER_BOX = Border(left=MEDIUM, right=MEDIUM, top=MEDIUM, bottom=MEDIUM)
BORDER_BOTTOM = Border(bottom=THIN)

ALIGN_LEFT = Alignment(horizontal='left', vertical='center', wrap_text=True)
ALIGN_CENTER = Alignment(horizontal='center', vertical='center',
                         wrap_text=True)
ALIGN_RIGHT = Alignment(horizontal='right', vertical='center')
ALIGN_TOP_LEFT = Alignment(horizontal='left', vertical='top', wrap_text=True)

FILL_HEADER = PatternFill('solid', fgColor='F2F2F2')

# Формат денежных клеток: разряды пробелами, всегда две цифры после
# запятой. Без явного формата Excel показал бы 1234.5 вместо 1 234,50.
MONEY_FORMAT = '# ##0.00'
QUANTITY_FORMAT = '# ##0.###'


def setup_page(worksheet, landscape=False, fit_width=1):
    """Настроить лист под печать на А4.

    fit_width=1 заставляет Excel ужать таблицу по ширине в одну страницу —
    иначе широкие бланки вроде накладной разъезжаются на несколько
    листов, и подписи оказываются отдельно от таблицы.
    """
    worksheet.page_setup.paperSize = worksheet.PAPERSIZE_A4
    worksheet.page_setup.orientation = (
        'landscape' if landscape else 'portrait')
    worksheet.page_setup.fitToWidth = fit_width
    worksheet.page_setup.fitToHeight = 0
    worksheet.sheet_properties.pageSetUpPr.fitToPage = True

    worksheet.page_margins.left = 0.4
    worksheet.page_margins.right = 0.3
    worksheet.page_margins.top = 0.4
    worksheet.page_margins.bottom = 0.4


def set_widths(worksheet, widths):
    """Задать ширины столбцов: {'A': 5, 'B': 40, ...} либо список."""
    if isinstance(widths, dict):
        pairs = widths.items()
    else:
        pairs = ((get_column_letter(index), width)
                 for index, width in enumerate(widths, start=1))
    for letter, width in pairs:
        worksheet.column_dimensions[letter].width = width


def write_cell(worksheet, row, column, value, font=None, align=None,
               border=None, number_format=None, fill=None):
    """Записать значение в клетку и оформить её."""
    cell = worksheet.cell(row=row, column=column, value=value)
    cell.font = font or FONT_REGULAR
    cell.alignment = align or ALIGN_LEFT
    if border is not None:
        cell.border = border
    if number_format:
        cell.number_format = number_format
    if fill is not None:
        cell.fill = fill
    return cell


def merge_and_write(worksheet, cell_range, value, font=None, align=None,
                    border=None, number_format=None, fill=None):
    """Объединить клетки и записать значение в получившуюся.

    Оформление применяется ко всем клеткам диапазона, а не только к
    первой: иначе рамка объединённой клетки будет нарисована лишь у
    левого края, а остальное останется без границ.
    """
    worksheet.merge_cells(cell_range)
    first = cell_range.split(':')[0]
    cell = worksheet[first]
    cell.value = value
    cell.font = font or FONT_REGULAR
    cell.alignment = align or ALIGN_LEFT
    if number_format:
        cell.number_format = number_format

    if border is not None or fill is not None:
        for row in worksheet[cell_range]:
            for item in row:
                if border is not None:
                    item.border = border
                if fill is not None:
                    item.fill = fill
    return cell


def money(value):
    """Привести денежную величину к типу, понятному Excel.

    Decimal openpyxl записывает как текст, и такие клетки не суммируются
    формулами — бухгалтер не сможет проверить итог, выделив столбец.
    """
    return float(Decimal(value))


def signature_line(worksheet, row, column_range, title, name):
    """Подпись с расшифровкой: линия, под ней должность и фамилия."""
    merge_and_write(worksheet, column_range, '', border=BORDER_BOTTOM)
    first_column = column_range.split(':')[0]
    letters = ''.join(ch for ch in first_column if ch.isalpha())
    write_cell(worksheet, row + 1,
               worksheet[f'{letters}1'].column,
               f'{title}   {name}',
               font=FONT_SMALL, align=ALIGN_LEFT)
