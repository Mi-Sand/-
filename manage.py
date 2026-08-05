#!/usr/bin/env python
"""Утилита управления проектом Django."""
import os
import sys


def main():
    """Запуск административной утилиты Django."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE',
                          'warehouse_config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Не удалось импортировать Django. Убедитесь, что Django "
            "установлен и в PYTHONPATH."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
