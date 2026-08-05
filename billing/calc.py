"""
Подготовка строк документа из заказа.

Все четыре бланка — счёт, накладная, УПД и акт сверки — показывают одни и
те же позиции, отличаясь только оформлением. Считать их в каждом бланке
заново значило бы получить четыре расходящихся набора сумм: где-то
округлили раньше, где-то позже, и документы перестали бы сходиться между
собой. Поэтому расчёт собран здесь и выполняется один раз.

Порядок округления выбран так же, как в бухгалтерских программах: сумма
по строке округляется до копеек, а итог документа складывается уже из
округлённых строк. Если сначала сложить, а потом округлить, итог порой
расходится со суммой строк на копейку — и документ выглядит ошибочным.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import List

from .money import round_money, split_amount


@dataclass
class DocumentLine:
    """Одна позиция документа со всеми суммами, нужными бланкам."""

    number: int
    name: str
    article: str
    unit: str
    quantity: Decimal
    price: Decimal          # цена за единицу, как в заказе
    net: Decimal            # стоимость без налога
    vat: Decimal            # сумма налога
    total: Decimal          # стоимость с налогом

    @property
    def price_without_vat(self):
        """Цена за единицу без налога — нужна накладной и УПД."""
        if not self.quantity:
            return Decimal('0.00')
        return round_money(self.net / self.quantity)


@dataclass
class DocumentTotals:
    """Итоги по документу."""

    net: Decimal
    vat: Decimal
    total: Decimal
    line_count: int
    quantity: Decimal


def build_lines(order, requisites) -> List[DocumentLine]:
    """Разложить позиции заказа по строкам документа.

    Цена берётся из заказа, а не из карточки товара: в заказе она
    зафиксирована на момент оформления, и документ должен показывать
    именно ту сумму, которую видел покупатель.
    """
    rate = requisites.vat_percent if requisites else None
    included = requisites.vat_included_in_price if requisites else True

    lines = []
    for index, item in enumerate(order.items.all(), start=1):
        gross = round_money(item.quantity * item.price)
        net, vat, total = split_amount(gross, rate, included)

        product = item.product
        name_parts = [product.name]
        if product.size:
            name_parts.append(f'размер {product.size}')
        if product.color:
            name_parts.append(product.color)

        lines.append(DocumentLine(
            number=index,
            name=', '.join(name_parts),
            article=product.article_number,
            unit='шт.',
            quantity=item.quantity,
            price=item.price,
            net=net,
            vat=vat,
            total=total,
        ))
    return lines


def summarize(lines: List[DocumentLine]) -> DocumentTotals:
    """Сложить итоги из уже округлённых строк."""
    return DocumentTotals(
        net=sum((line.net for line in lines), Decimal('0.00')),
        vat=sum((line.vat for line in lines), Decimal('0.00')),
        total=sum((line.total for line in lines), Decimal('0.00')),
        line_count=len(lines),
        quantity=sum((line.quantity for line in lines), Decimal('0')),
    )


def order_document_data(order, requisites):
    """Строки и итоги заказа — то, что нужно любому из бланков."""
    lines = build_lines(order, requisites)
    return lines, summarize(lines)


def buyer_details(order):
    """Как покупатель печатается в документе.

    У организации берутся реквизиты из карточки контрагента, у частного
    лица — имя, телефон и адрес доставки: ИНН у него запрашивать незачем,
    а для счёта эти сведения достаточны.
    """
    if order.counterparty:
        party = order.counterparty
        requisite_parts = [f'ИНН {party.inn}']
        if party.kpp:
            requisite_parts.append(f'КПП {party.kpp}')
        return {
            'name': party.short_name,
            'requisites': ', '.join(requisite_parts),
            'address': party.legal_address,
            'phone': party.phone,
            'is_organization': True,
        }

    address = order.address or ''
    return {
        'name': order.customer_name,
        'requisites': '',
        'address': address,
        'phone': order.customer_phone,
        'is_organization': False,
    }
