"""
Корневая конфигурация маршрутов проекта.

Собирает вместе:
- административную панель Django;
- REST API складских операций, инвентаризации и отчётов;
- страницы веб-интерфейса (вход, главная панель, справочники и т. д.).
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from rest_framework.routers import DefaultRouter

from inventory.views import InventoryViewSet
from reports import views as report_views
from warehouse import views as wh_views
from warehouse import pages
from warehouse import shop
from warehouse.order_views import OrderViewSet
from accounts.views import (UserViewSet, chat_messages, chat_contacts,
                            chat_unread, chat_mark_read)

# --- Маршрутизатор REST API (фрагмент 13 отчёта) ----------------------------
router = DefaultRouter()
router.register(r'materials', wh_views.MaterialViewSet)
router.register(r'products', wh_views.ProductViewSet)
router.register(r'warehouses', wh_views.WarehouseViewSet)
router.register(r'suppliers', wh_views.SupplierViewSet)
router.register(r'inbound-documents', wh_views.InboundDocumentViewSet)
router.register(r'outbound-documents', wh_views.OutboundDocumentViewSet)
router.register(r'stock', wh_views.StockViewSet)
router.register(r'movements', wh_views.StockMovementViewSet)
router.register(r'users', UserViewSet)
router.register(r'orders', OrderViewSet)
router.register(r'inventories', InventoryViewSet)

api_urlpatterns = [
    path('api/', include(router.urls)),
    path('api/reports/stock/', report_views.stock_report,
         name='report-stock'),
    path('api/reports/movement/', report_views.movement_report,
         name='report-movement'),
    path('api/reports/reorder/', report_views.reorder_report,
         name='report-reorder'),
    path('api/reports/inventory/', report_views.inventory_report,
         name='report-inventory'),
    path('api/reports/expiry/', report_views.expiry_report,
         name='report-expiry'),
    path('api/reports/production-cost/',
         report_views.production_cost_report, name='report-production-cost'),
    path('api/reports/stock/export/', report_views.stock_report_export,
         name='report-stock-export'),
    path('api/dashboard/', wh_views.dashboard_summary,
         name='dashboard-summary'),
    path('api/produce/', wh_views.produce_view, name='produce'),
    path('api/chat/messages/', chat_messages, name='chat-messages'),
    path('api/chat/contacts/', chat_contacts, name='chat-contacts'),
    path('api/chat/unread/', chat_unread, name='chat-unread'),
    path('api/chat/read/', chat_mark_read, name='chat-read'),
    path('api/shop/products/', shop.shop_products, name='shop-products'),
    path('api/shop/orders/', shop.shop_create_order, name='shop-order'),
    # Публичные страницы магазина — без входа в систему
    path('shop/', shop.shop_page, name='shop-page'),
    path('shop/about/', shop.shop_about_page, name='shop-about'),
    path('shop/delivery/', shop.shop_delivery_page, name='shop-delivery'),
    path('shop/contacts/', shop.shop_contacts_page, name='shop-contacts'),
    path('api-auth/', include('rest_framework.urls')),
]

# --- Страницы веб-интерфейса ------------------------------------------------
page_urlpatterns = [
    path('login/', auth_views.LoginView.as_view(
        template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', pages.dashboard_page, name='dashboard'),
    path('materials/', pages.materials_page, name='materials-page'),
    path('products/', pages.products_page, name='products-page'),
    path('inbound/', pages.inbound_page, name='inbound-page'),
    path('outbound/', pages.outbound_page, name='outbound-page'),
    path('inventory/', pages.inventory_page, name='inventory-page'),
    path('reports/', pages.reports_page, name='reports-page'),
    path('catalog/', pages.catalog_page, name='catalog-page'),
    path('production/', pages.production_page, name='production-page'),
    path('employees/', pages.employees_page, name='employees-page'),
    path('orders/', pages.orders_page, name='orders-page'),
]

urlpatterns = [
    path('admin/', admin.site.urls),
] + api_urlpatterns + page_urlpatterns

# Раздача загруженных фотографий и видео товаров.
#
# В режиме разработки этим занимается Django. В боевом — тоже, но только
# если так задано настройкой SERVE_MEDIA_FILES: при запуске без веб-сервера
# впереди (на офисном компьютере) без этого маршрута витрина осталась бы
# без картинок. Когда впереди стоит nginx, переменную выключают, и файлы
# отдаёт он.
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve as static_serve

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL,
                          document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA_FILES:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', static_serve,
                {'document_root': settings.MEDIA_ROOT}),
    ]
