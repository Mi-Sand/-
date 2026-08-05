"""WSGI-конфигурация проекта warehouse_config."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'warehouse_config.settings')

application = get_wsgi_application()
