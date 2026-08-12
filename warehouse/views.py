"""
Обработчики REST API (фрагмент 12 отчёта).

Реализованы в виде ViewSet-классов со стандартными операциями CRUD и
дополнительными действиями: список материалов с остатком ниже минимального,
проведение приходного/расходного документа. Доступ ко всем методам требует
аутентификации. Для списков документов применён select_related — устранение
проблемы N+1 запросов (фрагмент 17).
"""
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from accounts.permissions import CanEditDocuments, CanManageCatalog
from rest_framework.response import Response

from .models import (InboundDocument, Material, OutboundDocument, Product,
                     ProductPhoto, Stock, StockMovement, Supplier, Unit,
                     Warehouse)
from .params import contains_any_case, read_date
from .serializers import (InboundDocumentSerializer, MaterialSerializer,
                          OutboundDocumentSerializer, ProductPhotoSerializer,
                          ProductSerializer, StockMovementSerializer,
                          StockSerializer, SupplierSerializer, UnitSerializer,
                          WarehouseSerializer)
from .services import (InsufficientStockError, process_inbound_document,
                       process_outbound_document, produce_product,
                       unprocess_inbound_document,
                       unprocess_outbound_document)
from .trash import KINDS as TRASH_KINDS
from .trash import items as trash_items
from .trash import mark_deleted, restore


class CatalogDeleteGuardMixin:
    """Не даёт удалить справочную запись, за которой числится история.

    Остатки и движения связаны со справочником каскадом: удаление
    материала уносило с собой и остаток по нему, и всю историю приходов
    и расходов. Пятьсот килограммов кожи и три года движений исчезали по
    одному нажатию с вопросом «Удалить материал?» — и вернуть их было
    неоткуда, кроме резервной копии.

    Запись без остатка и без движений удаляется как прежде: заведённую
    по ошибке позицию убрать можно. Дальше действует то же правило, что
    и для проведённых документов, — списать остаток, а позицию убрать
    из обращения (у продукции для этого есть состояние «Снят с
    производства»).
    """

    #: Чем закончить фразу «Нельзя удалить …»
    guard_subject = 'запись'
    #: Как поступить вместо удаления
    guard_advice = ''

    def _usage(self, obj):
        """Сколько остатков и движений держит запись."""
        field = self.guard_field
        stock = (Stock.objects.filter(**{field: obj})
                 .exclude(quantity=0).count())
        moves = StockMovement.objects.filter(**{field: obj}).count()
        return stock, moves

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        stock, moves = self._usage(obj)
        if stock or moves:
            parts = []
            if stock:
                parts.append(f'числится остаток на складах ({stock})')
            if moves:
                parts.append(f'есть движения по складу ({moves})')
            return Response(
                {'error': f'Нельзя удалить {self.guard_subject} «{obj}»: '
                          + ' и '.join(parts) + '. '
                          + self.guard_advice},
                status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)


class TextSearchMixin:
    """Поиск по строке — свой, вместо встроенного в DRF.

    Встроенный SearchFilter приводит к одному регистру только латиницу:
    в SQLite «кожа» не находило «Кожа хромовая», и человек делал вывод,
    что записи нет. Здесь тот же параметр `?search=`, но с разбором
    регистра, годным для русских названий (см. contains_any_case).

    Поле называется `text_search_fields`, а не `search_fields`: под
    вторым именем его подхватывает SearchFilter и молча применяет свой
    отбор поверх нашего — с тем же изъяном, ради которого всё и
    затевалось.
    """

    #: Поля, по которым идёт поиск. Задаёт наследник.
    text_search_fields = ()

    def get_queryset(self):
        records = super().get_queryset()
        search = (self.request.query_params.get('search') or '').strip()
        if not (search and self.text_search_fields):
            return records

        condition = Q()
        for field in self.text_search_fields:
            condition |= contains_any_case(field, search)
        # Поиск идёт и по строкам документа, а строк несколько: без
        # distinct документ с двумя подходящими позициями показался бы
        # дважды.
        return records.filter(condition).distinct()


class DocumentSearchMixin(TextSearchMixin):
    """То же самое плюс отбор по датам документа.

    Раньше искать можно было только по номеру, да и то в браузере:
    страница забирала все документы и отсеивала лишние сама. Кладовщик
    же помнит не номер, а поставщика, товар или хотя бы неделю, когда
    это было. При тысяче документов забирать всё, чтобы показать три, —
    расточительство, которое сначала незаметно, а потом внезапно.
    """

    def get_queryset(self):
        documents = super().get_queryset()
        params = self.request.query_params

        since = read_date(params.get('since'))
        if since:
            documents = documents.filter(doc_date__gte=since)
        until = read_date(params.get('until'))
        if until:
            documents = documents.filter(doc_date__lte=until)

        return documents


class MaterialViewSet(TextSearchMixin, CatalogDeleteGuardMixin,
                      viewsets.ModelViewSet):
    queryset = Material.objects.all()
    serializer_class = MaterialSerializer
    permission_classes = [CanManageCatalog]
    filterset_fields = ['category', 'unit']
    # Штрихкод в поиске: со сканером его вводят в то же поле, что и
    # название, — прибор просто «печатает» цифры и жмёт ввод.
    text_search_fields = ('name', 'description', 'article_number', 'barcode')
    ordering_fields = ['name', 'category', 'reorder_point']
    guard_field = 'material'
    guard_subject = 'материал'
    guard_advice = ('Сначала спишите остаток расходным документом — '
                    'история движений должна остаться в учёте.')

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        """Материалы, чей суммарный остаток ниже минимального."""
        qs = (Material.objects
              .annotate(total=Coalesce(
                  Sum('stock__quantity'),
                  Value(0, output_field=DecimalField())))
              .filter(total__lt=F('reorder_point')))
        data = [{
            **MaterialSerializer(m).data,
            'current_stock': m.total,
        } for m in qs]
        return Response(data)


class UnitViewSet(viewsets.ModelViewSet):
    """Единицы измерения — свои, а не пять на всех."""

    queryset = Unit.objects.all()
    serializer_class = UnitSerializer
    permission_classes = [CanManageCatalog]
    # Список короткий: полсотни единиц — уже много. Постраничная
    # выдача здесь только мешала бы выбору в формах.
    pagination_class = None

    def destroy(self, request, *args, **kwargs):
        """Единицу, которой пользуются, удалять нельзя.

        Иначе у материалов остался бы код, которому ничего не
        соответствует: в накладной вместо «кг» оказался бы «kg», и это
        заметили бы не сразу. Встроенные не удаляются вовсе — на них
        стоит вся заведённая номенклатура.
        """
        unit = self.get_object()
        if unit.builtin:
            return Response(
                {'error': f'Единица «{unit.name}» встроенная, её удалить '
                          f'нельзя. Можно изменить обозначение.'},
                status=status.HTTP_400_BAD_REQUEST)

        used = Material.objects.filter(unit=unit.code).count()
        if used:
            return Response(
                {'error': f'Единицу «{unit.name}» нельзя удалить: по ней '
                          f'ведётся учёт материалов ({used}). Переведите '
                          f'их на другую единицу, потом удаляйте.'},
                status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """Код менять нельзя — на него ссылаются материалы.

        Обозначение и полное название править можно: они только для
        показа. А смена кода тихо оторвала бы от единицы все материалы,
        которые на неё ссылаются.
        """
        unit = self.get_object()
        new_code = request.data.get('code')
        if new_code and new_code != unit.code:
            used = Material.objects.filter(unit=unit.code).count()
            if used or unit.builtin:
                return Response(
                    {'error': 'Код единицы менять нельзя: на него ссылаются '
                              'материалы. Обозначение и название измените, '
                              'а для другого кода заведите новую единицу.'},
                    status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)


class ProductViewSet(TextSearchMixin, CatalogDeleteGuardMixin,
                     viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [CanManageCatalog]
    filterset_fields = ['category', 'size', 'color', 'status']
    text_search_fields = ('name', 'article_number', 'barcode', 'color')
    ordering_fields = ['name', 'article_number', 'selling_price']
    guard_field = 'product'
    guard_subject = 'продукцию'
    guard_advice = ('Чтобы убрать позицию из обращения, поставьте ей '
                    'состояние «Снят с производства» — она пропадёт '
                    'с витрины, а история отгрузок сохранится.')

    #: Больше одной фотографии на позицию витрине мало, но и без предела
    #: нельзя: страница товара с полусотней снимков грузится дольше, чем
    #: покупатель готов ждать.
    MAX_PHOTOS = 8

    @action(detail=True, methods=['get', 'post'],
            parser_classes=[MultiPartParser, FormParser])
    def photos(self, request, pk=None):
        """Дополнительные фотографии товара."""
        product = self.get_object()

        if request.method == 'GET':
            return Response(ProductPhotoSerializer(
                product.photos.all(), many=True).data)

        files = request.FILES.getlist('image') or request.FILES.getlist('images')
        if not files:
            return Response({'error': 'Не приложен файл фотографии.'},
                            status=status.HTTP_400_BAD_REQUEST)

        already = product.photos.count()
        if already + len(files) > self.MAX_PHOTOS:
            return Response(
                {'error': f'Больше {self.MAX_PHOTOS} дополнительных '
                          f'фотографий на позицию не бывает. Сейчас '
                          f'{already}.'},
                status=status.HTTP_400_BAD_REQUEST)

        made = []
        for number, uploaded in enumerate(files):
            made.append(ProductPhoto.objects.create(
                product=product, image=uploaded,
                sort_order=already + number))
        return Response(ProductPhotoSerializer(made, many=True).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'],
            url_path=r'photos/(?P<photo_id>\d+)')
    def delete_photo(self, request, pk=None, photo_id=None):
        """Убрать одну фотографию."""
        product = self.get_object()
        photo = product.photos.filter(pk=photo_id).first()
        if not photo:
            return Response({'error': 'Такой фотографии у товара нет.'},
                            status=status.HTTP_404_NOT_FOUND)
        # Файл с диска убираем тоже: иначе папка media растёт от каждой
        # замены снимка, и на складском компьютере это однажды заметят
        # по свободному месту.
        photo.image.delete(save=False)
        photo.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WarehouseViewSet(CatalogDeleteGuardMixin, viewsets.ModelViewSet):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer
    permission_classes = [CanManageCatalog]
    guard_field = 'warehouse'
    guard_subject = 'склад'
    guard_advice = ('Сначала переместите или спишите то, что на нём '
                    'лежит: вместе со складом пропала бы и история '
                    'движений по нему.')


class SupplierViewSet(TextSearchMixin, viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [CanManageCatalog]
    text_search_fields = ('name', 'inn', 'contact_person')


class InboundDocumentViewSet(DocumentSearchMixin, viewsets.ModelViewSet):
    # select_related — один запрос с JOIN вместо N+1 (фрагмент 17)
    queryset = (InboundDocument.objects
                .select_related('supplier', 'warehouse', 'created_by')
                .prefetch_related('items'))
    serializer_class = InboundDocumentSerializer
    permission_classes = [CanEditDocuments]
    filterset_fields = ['warehouse', 'supplier', 'processed']
    ordering_fields = ['doc_date', 'doc_number']
    text_search_fields = ('doc_number', 'supplier__name',
                          'items__material__name', 'items__product__name')

    @action(detail=True, methods=['post'])
    def process(self, request, pk=None):
        """Провести приходный документ и обновить остатки."""
        try:
            process_inbound_document(pk, user=request.user)
            return Response({'status': 'ok',
                             'detail': 'Документ проведён'})
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        """Удалять можно только непроведённые документы.

        Проведённый документ уже изменил остатки на складе. Его удаление
        оставило бы товар на складе без документа-основания и нарушило бы
        целостность учёта.

        Непроведённый не пропадает совсем: он уходит в корзину, откуда
        его можно вернуть. Удаляют обычно второпях и не тот документ.
        """
        doc = self.get_object()
        if doc.processed:
            return Response(
                {'error': 'Нельзя удалить проведённый документ. '
                          'Сначала отмените проведение (сторно).'},
                status=status.HTTP_400_BAD_REQUEST)
        mark_deleted(doc, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def update(self, request, *args, **kwargs):
        """Проведённый документ нельзя редактировать."""
        doc = self.get_object()
        if doc.processed:
            return Response(
                {'error': 'Нельзя изменить проведённый документ. '
                          'Сначала отмените проведение (сторно).'},
                status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def unprocess(self, request, pk=None):
        """Отменить проведение документа (сторно): откатить остатки."""
        try:
            unprocess_inbound_document(pk, user=request.user)
            return Response({'status': 'ok',
                             'detail': 'Проведение отменено, остатки откачены'})
        except InsufficientStockError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)


class OutboundDocumentViewSet(DocumentSearchMixin, viewsets.ModelViewSet):
    queryset = (OutboundDocument.objects
                .select_related('warehouse', 'created_by')
                .prefetch_related('items'))
    serializer_class = OutboundDocumentSerializer
    permission_classes = [CanEditDocuments]
    filterset_fields = ['warehouse', 'purpose', 'processed']
    ordering_fields = ['doc_date', 'doc_number']
    # Поставщика у расхода нет, зато есть производственный заказ —
    # по нему и ищут, когда разбираются, куда ушёл материал.
    text_search_fields = ('doc_number', 'production_order',
                          'items__material__name', 'items__product__name')

    @action(detail=True, methods=['post'])
    def process(self, request, pk=None):
        """Провести расходный документ с проверкой остатков."""
        try:
            process_outbound_document(pk, user=request.user)
            return Response({'status': 'ok',
                             'detail': 'Документ проведён'})
        except InsufficientStockError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        """Удалять можно только непроведённые документы.

        Удалённый уходит в корзину — см. пояснение у прихода.
        """
        doc = self.get_object()
        if doc.processed:
            return Response(
                {'error': 'Нельзя удалить проведённый документ. '
                          'Сначала отмените проведение (сторно).'},
                status=status.HTTP_400_BAD_REQUEST)
        mark_deleted(doc, user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def update(self, request, *args, **kwargs):
        """Проведённый документ нельзя редактировать."""
        doc = self.get_object()
        if doc.processed:
            return Response(
                {'error': 'Нельзя изменить проведённый документ. '
                          'Сначала отмените проведение (сторно).'},
                status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def unprocess(self, request, pk=None):
        """Отменить проведение расхода: вернуть товар на склад."""
        try:
            unprocess_outbound_document(pk, user=request.user)
            return Response({'status': 'ok',
                             'detail': 'Проведение отменено, товар возвращён'})
        except ValueError as e:
            return Response({'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)


class StockViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (Stock.objects
                .select_related('warehouse', 'material', 'product')
                .filter(quantity__gt=0))
    serializer_class = StockSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['warehouse', 'material', 'product']


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (StockMovement.objects
                .select_related('warehouse', 'material', 'product', 'user'))
    serializer_class = StockMovementSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['warehouse', 'movement_type', 'material', 'product']
    ordering_fields = ['created_at']


# --- Сводка для главной панели ----------------------------------------------
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dashboard_summary(request):
    """Данные для главной панели: стоимость запасов, дефицит, операции."""
    money = DecimalField(max_digits=16, decimal_places=2)

    # Стоимость материалов = остаток × средняя себестоимость недоступна,
    # поэтому берём по последней закупочной цене из истории. Для простоты
    # оцениваем продукцию по себестоимости, материалы — по reorder-независимо.
    materials_value = (Stock.objects
                       .filter(material__isnull=False)
                       .aggregate(v=Coalesce(
                           Sum(F('quantity')), Value(0),
                           output_field=money))['v'])

    products_value = (Stock.objects
                      .filter(product__isnull=False)
                      .aggregate(v=Coalesce(
                          Sum(F('quantity') * F('product__cost')),
                          Value(0), output_field=money))['v'])

    low_stock_count = 0
    low_items = []
    for m in Material.objects.annotate(
            total=Coalesce(Sum('stock__quantity'),
                           Value(0, output_field=money))):
        if m.total < m.reorder_point:
            low_stock_count += 1
            low_items.append({
                'name': m.name,
                'current': float(m.total),
                'reorder_point': float(m.reorder_point),
                'unit': m.get_unit_display(),
            })

    recent = StockMovement.objects.select_related(
        'warehouse', 'material', 'product', 'user')[:10]

    # Динамика движения — для графика на главной. Показывает, в какие дни
    # склад принимал, а в какие отгружал: по этой картине сразу виден ритм
    # работы и провалы в поставках.
    from datetime import timedelta
    from django.utils import timezone as dj_timezone

    try:
        period = int(request.query_params.get('days', 14))
    except (TypeError, ValueError):
        period = 14
    period = max(7, min(period, 90))   # разумные границы

    today = dj_timezone.localdate()
    start = today - timedelta(days=period - 1)

    daily = {}
    movements = (StockMovement.objects
                 .filter(created_at__date__gte=start)
                 .values('created_at__date', 'movement_type')
                 .annotate(total=Coalesce(Sum('quantity'),
                                          Value(0, output_field=money))))
    for row in movements:
        day = row['created_at__date']
        bucket = daily.setdefault(day, {'in': 0.0, 'out': 0.0})
        if row['movement_type'] == 'in':
            bucket['in'] += float(row['total'])
        elif row['movement_type'] == 'out':
            bucket['out'] += float(row['total'])

    chart = []
    for offset in range(period):
        day = start + timedelta(days=offset)
        bucket = daily.get(day, {'in': 0.0, 'out': 0.0})
        chart.append({
            'date': day.strftime('%d.%m'),
            'full_date': day.strftime('%d.%m.%Y'),
            'in': round(bucket['in'], 2),
            'out': round(bucket['out'], 2),
        })

    return Response({
        'materials_positions': Stock.objects.filter(
            material__isnull=False, quantity__gt=0).count(),
        'products_positions': Stock.objects.filter(
            product__isnull=False, quantity__gt=0).count(),
        'products_value': float(products_value or 0),
        'materials_total_qty': float(materials_value or 0),
        'low_stock_count': low_stock_count,
        'low_stock_items': low_items[:10],
        'recent_movements': StockMovementSerializer(recent, many=True).data,
        'chart': chart,
        # Партии с истекающим сроком годности: данные о сроках вводились
        # при приходе, но до сих пор нигде не использовались
        'expiry': _expiry_block(),
    })


def _expiry_block():
    """Сводка по срокам годности для главной панели."""
    from .expiry import expiry_summary

    summary = expiry_summary()
    return {
        'expired_count': summary['expired_count'],
        'expiring_count': summary['expiring_count'],
        'items': [{
            'name': row['item_name'],
            'batch': row['batch_number'],
            'expiry_date': row['expiry_date'].strftime('%d.%m.%Y'),
            'days_left': row['days_left'],
            'expired': row['expired'],
            'warehouse': row['warehouse'],
        } for row in summary['items']],
    }


@api_view(['POST'])
@permission_classes([CanEditDocuments])
def produce_view(request):
    """Произвести продукцию из материалов.

    Ожидает JSON:
    {
        "product": 1,
        "quantity": 10,
        "product_warehouse": 2,
        "material_warehouse": 1,
        "materials": [{"material": 5, "quantity": 15}, ...]
    }
    """
    data = request.data
    try:
        run = produce_product(
            product_id=data['product'],
            quantity=data['quantity'],
            product_warehouse_id=data['product_warehouse'],
            materials=data.get('materials', []),
            material_warehouse_id=data['material_warehouse'],
            user=request.user if request.user.is_authenticated else None)

        # Себестоимость возвращается сразу: кладовщик видит, во что
        # обошёлся выпуск, не переходя в отчёты
        return Response({
            'status': 'ok',
            'product': run.product.name,
            'quantity': str(run.quantity),
            'run_number': run.number,
            'material_cost': float(run.material_cost),
            'unit_cost': float(run.unit_cost),
            'planned_unit_cost': float(run.planned_unit_cost),
            'cost_deviation': float(run.cost_deviation),
            'pricing_complete': run.pricing_complete,
        })
    except InsufficientStockError as e:
        return Response({'error': str(e)},
                        status=status.HTTP_400_BAD_REQUEST)
    except (KeyError, ValueError) as e:
        return Response({'error': f'Некорректные данные: {e}'},
                        status=status.HTTP_400_BAD_REQUEST)


# --- Корзина удалённых документов -------------------------------------------
@api_view(['GET'])
@permission_classes([CanEditDocuments])
def trash_list(request):
    """Что лежит в корзине.

    Смотреть корзину незачем тому, кто и удалять не вправе, поэтому
    право здесь то же самое, что на правку документов.
    """
    rows = trash_items(kind=request.query_params.get('kind') or None)
    return Response({'count': len(rows), 'results': rows})


@api_view(['POST'])
@permission_classes([CanEditDocuments])
def trash_restore(request, kind, pk):
    """Вернуть документ из корзины."""
    entry = TRASH_KINDS.get(kind)
    if not entry:
        return Response({'error': f'Неизвестный вид документа: {kind}'},
                        status=status.HTTP_400_BAD_REQUEST)

    model = entry[0]
    document = model.all_objects.filter(pk=pk,
                                        deleted_at__isnull=False).first()
    if not document:
        return Response({'error': 'Документ в корзине не найден. '
                                  'Возможно, его уже вернули или '
                                  'вычистили по сроку.'},
                        status=status.HTTP_404_NOT_FOUND)
    try:
        restore(document)
    except ValueError as error:
        return Response({'error': str(error)},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response({'status': 'ok',
                     'detail': f'Документ {document.doc_number} возвращён'})
