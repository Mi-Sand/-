"""
Автоматическая обработка загружаемых фотографий товаров.

Зачем: пользователи загружают снимки прямо с телефона или из нейросети —
это файлы по 2–5 МБ. На витрине такие фото грузятся медленно, особенно
по сети с других компьютеров. Просить каждый раз готовить картинку вручную
неудобно и ненадёжно.

Решение: при сохранении товара фото автоматически приводится к разумному
размеру — вписывается в 1200×1200 px и пересжимается в JPEG с качеством 85.
Обычно это уменьшает файл в 5–15 раз без заметной потери качества.

Сигнал pre_save срабатывает до записи в базу, поэтому в файловой системе
сразу оказывается уже сжатая версия.
"""
import io
import os

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db.models.signals import pre_save
from django.dispatch import receiver

from .models import Product

# Максимальная сторона изображения после обработки
MAX_SIDE = 1200
# Качество JPEG: 85 — хороший баланс «вес / визуальное качество»
JPEG_QUALITY = 85


def compress_image(django_file):
    """Сжать изображение: вписать в MAX_SIDE и пересохранить в JPEG.

    Возвращает новый файл для сохранения либо None, если обработка
    невозможна (нет Pillow, битый файл, неподдерживаемый формат) — в этом
    случае оригинал сохраняется как есть, без ошибки для пользователя.
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        django_file.seek(0)
        img = Image.open(django_file)

        # Прозрачность и палитровые режимы переводим в RGB — иначе JPEG
        # не сохранится. Прозрачный фон становится белым.
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            converted = img.convert('RGBA')
            background.paste(converted, mask=converted.split()[-1])
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Уменьшаем, только если изображение больше лимита.
        # thumbnail сохраняет пропорции — товар не исказится.
        if img.width > MAX_SIDE or img.height > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=JPEG_QUALITY, optimize=True)
        buffer.seek(0)

        base = os.path.splitext(os.path.basename(django_file.name))[0]
        return InMemoryUploadedFile(
            buffer, 'ImageField', f'{base}.jpg', 'image/jpeg',
            buffer.getbuffer().nbytes, None)
    except Exception:
        # Любая проблема с обработкой не должна ломать сохранение товара
        return None


@receiver(pre_save, sender=Product)
def compress_product_photo(sender, instance, **kwargs):
    """Сжать фото товара перед сохранением, если оно только что загружено."""
    if not instance.photo:
        return

    # Обрабатываем только свежезагруженные файлы. У уже сохранённых в базе
    # атрибута `file` с исходным содержимым нет, и повторно сжимать их не
    # нужно — иначе качество будет падать при каждом редактировании.
    photo_file = getattr(instance.photo, 'file', None)
    if not isinstance(photo_file, (InMemoryUploadedFile,)) and \
            not hasattr(photo_file, 'temporary_file_path'):
        return

    compressed = compress_image(instance.photo)
    if compressed is not None:
        instance.photo = compressed
