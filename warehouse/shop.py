"""
Публичная витрина интернет-магазина.

Здесь — показ товаров покупателям и оформление заказов. В отличие от
складского API, эти эндпоинты открыты без авторизации (покупатели не входят
в систему), но отдают строго ограниченный набор данных: наименование,
категорию, размер, цвет, отпускную цену и наличие. Себестоимость и прочая
внутренняя информация покупателям не передаётся.

Наличие показывается с учётом резерва: товар, обещанный другим заказам,
недоступен к заказу, хотя физически ещё лежит на складе.
"""
from django.conf import settings
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import render
from rest_framework import status as http_status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from warehouse.models import Product
from warehouse.order_services import (TooManyOrdersError, check_order_rate,
                                      create_order, get_reserved_quantities)
from warehouse.services import InsufficientStockError


# --- Публичные страницы -----------------------------------------------------
#
# Витрина, «О производстве», «Доставка и оплата» и «Контакты» открыты без
# входа в систему. Общая шапка, меню и подвал лежат в shop_base.html;
# каждая страница передаёт только своё имя — по нему меню подсвечивает
# текущий раздел. Сведения о предприятии приходят из shop_info.py через
# процессор контекста, руками их передавать не нужно.


def shop_page(request):
    """Публичная страница-витрина (доступна без входа)."""
    return render(request, 'shop.html', {'page': 'shop'})


def shop_about_page(request):
    """О производстве: что делаем и для кого."""
    return render(request, 'shop_about.html', {'page': 'about'})


def shop_delivery_page(request):
    """Доставка, оплата и документы для бухгалтерии."""
    return render(request, 'shop_delivery.html', {'page': 'delivery'})


def shop_contacts_page(request):
    """Контакты, схема проезда и реквизиты."""
    return render(request, 'shop_contacts.html', {'page': 'contacts'})


def client_address(request):
    """Адрес, с которого пришёл запрос.

    Когда перед системой стоит nginx, все запросы приходят с его
    адреса, а настоящий передаётся в заголовке X-Forwarded-For. Берём
    первый адрес из списка — остальные дописывают промежуточные узлы.

    Заголовок подделывается кем угодно, поэтому доверяем ему только
    при явном разрешении в настройках: без веб-сервера впереди
    подделанный заголовок обошёл бы ограничение частоты в одну строку.
    """
    if getattr(settings, 'TRUST_FORWARDED_FOR', False):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()[:45] or None
    return request.META.get('REMOTE_ADDR') or None


@api_view(['GET'])
@permission_classes([AllowAny])
def shop_products(request):
    """Список товаров для витрины: активная продукция с остатком.

    Отдаёт только то, что можно показать покупателю. Наличие — сумма
    остатков по всем складам. По умолчанию показываются все активные
    товары; параметр ?in_stock=1 оставляет только те, что есть в наличии.
    """
    category = request.query_params.get('category')
    search = request.query_params.get('search')
    in_stock_only = request.query_params.get('in_stock') == '1'

    qs = (Product.objects
          .filter(status='active')
          # Фотографии забираем одним запросом на все товары: без этого
          # витрина делала бы отдельный запрос на каждую позицию.
          .prefetch_related('photos')
          .annotate(stock_qty=Coalesce(
              Sum('stock__quantity'),
              Value(0, output_field=DecimalField()))))

    if category:
        qs = qs.filter(category=category)
    if search:
        qs = qs.filter(name__icontains=search)
    if in_stock_only:
        qs = qs.filter(stock_qty__gt=0)

    # Резерв по активным заказам: этот товар обещан другим покупателям
    reserved_map = get_reserved_quantities()

    # Группируем товары по названию. Внутри карточки — выбор цвета, а у
    # каждого цвета свои размеры (варианты). Каждый вариант несёт свои
    # фото/видео/описание/цену/наличие.
    groups = {}
    for p in qs:
        name_key = p.name.strip().lower()
        if name_key not in groups:
            groups[name_key] = {
                'name': p.name,
                'category': p.get_category_display(),
                'colors': {},  # цвет -> список вариантов размеров
            }
        g = groups[name_key]
        color_key = p.color.strip().lower()
        if color_key not in g['colors']:
            g['colors'][color_key] = {'color': p.color, 'variants': []}
        # Доступно к заказу = на складе минус зарезервировано
        available_qty = float(p.stock_qty) - float(reserved_map.get(p.id, 0))
        if available_qty < 0:
            available_qty = 0
        g['colors'][color_key]['variants'].append({
            'id': p.id,
            'article': p.article_number,
            'size': p.size,
            'price': float(p.selling_price),
            'in_stock': available_qty,
            'available': available_qty > 0,
            'description': p.description,
            'photo': p.photo.url if p.photo else None,
            # Список — для галереи на карточке товара. Поле photo
            # остаётся: на него опираются превью в списке и старые
            # закладки покупателей.
            'photos': p.photo_urls(),
            'video': p.video.url if p.video else None,
        })

    # Собираем итоговые карточки. Для каждой — список цветов, у каждого цвета
    # отсортированные размеры. Для превью берём первое доступное фото.
    products = []
    for g in groups.values():
        colors = []
        all_prices = []
        cover_photo = None
        any_available = False
        for c in g['colors'].values():
            variants = sorted(c['variants'], key=lambda v: v['size'])
            colors.append({'color': c['color'], 'variants': variants})
            for v in variants:
                all_prices.append(v['price'])
                if v['available']:
                    any_available = True
                if not cover_photo and v['photo']:
                    cover_photo = v['photo']
        products.append({
            'name': g['name'],
            'category': g['category'],
            'photo': cover_photo,
            'colors': colors,
            'price_min': min(all_prices),
            'price_max': max(all_prices),
            'available': any_available,
        })

    return Response({'count': len(products), 'products': products})


@api_view(['POST'])
@permission_classes([AllowAny])
def shop_create_order(request):
    """Оформить заказ из корзины (публичный, без авторизации).

    Ожидает JSON:
    {
        "customer_name": "Иван Петров",
        "customer_phone": "+7 999 123-45-67",
        "customer_email": "ivan@example.com",
        "address": "г. Дмитров, ул. Ленина, 1",
        "comment": "Позвонить после 18:00",
        "items": [{"product": 5, "quantity": 2}, ...]
    }

    Проверка доступности выполняется на сервере, в транзакции — данные
    из браузера покупателя не считаются доверенными.
    """
    data = request.data
    source_ip = client_address(request)
    try:
        # Предел частоты проверяем до создания заказа: смысл в том,
        # чтобы товар не ушёл в резерв, а не в том, чтобы отменить это
        # потом.
        check_order_rate(source_ip=source_ip,
                         phone=str(data.get('customer_phone', '')).strip())
        order = create_order(
            customer_name=data.get('customer_name', ''),
            customer_phone=data.get('customer_phone', ''),
            customer_email=data.get('customer_email', ''),
            address=data.get('address', ''),
            comment=data.get('comment', ''),
            items=data.get('items', []),
            source_ip=source_ip)
        return Response({
            'status': 'ok',
            'order_number': order.number,
            'total': float(order.total),
        }, status=http_status.HTTP_201_CREATED)
    except TooManyOrdersError as e:
        # 429 — «слишком много обращений». Отдельный код, а не общий
        # отказ: по журналу сервера сразу видно, что заказ не приняли
        # из-за частоты, а не из-за ошибки в данных.
        return Response({'error': str(e)},
                        status=http_status.HTTP_429_TOO_MANY_REQUESTS)
    except InsufficientStockError as e:
        return Response({'error': str(e)},
                        status=http_status.HTTP_400_BAD_REQUEST)
    except ValueError as e:
        return Response({'error': str(e)},
                        status=http_status.HTTP_400_BAD_REQUEST)
