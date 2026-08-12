"""Несколько фотографий на товар.

Фотография была одна. Обувь смотрят с нескольких сторон, у инвентаря
важны крепления и швы — покупатель, который не разглядел товар, либо не
заказывает, либо возвращает. И то и другое дороже второго снимка.

Обложка осталась в самом товаре: на неё опираются списки, печатные
формы и превью витрины. Остальные снимки лежат рядом.
"""
import io
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from .models import Product, ProductPhoto, Stock, Warehouse

User = get_user_model()

MEDIA = tempfile.mkdtemp(prefix='проверка-фото-')


def picture(color='red', size=(40, 30)):
    """Настоящий файл изображения: сжатие работает только с ними."""
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='JPEG')
    buffer.seek(0)
    return SimpleUploadedFile('снимок.jpg', buffer.read(),
                              content_type='image/jpeg')


@override_settings(MEDIA_ROOT=MEDIA)
class PhotoTestBase(TestCase):
    """Файлы пишутся во временную папку, а не в media проекта.

    Иначе проверки засоряют рабочую папку снимками, и однажды их
    принимают за настоящие.
    """

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username='glava', password='x', role='admin')
        self.client.force_login(self.user)
        self.product = Product.objects.create(
            article_number='МЯ-1', name='Мяч футбольный', category='equipment',
            size='5', color='белый', cost=Decimal('300'),
            selling_price=Decimal('1500'))

    def add_photo(self, files=None):
        return self.client.post(
            f'/api/products/{self.product.pk}/photos/',
            {'image': files or picture()}, format='multipart')


class UploadTest(PhotoTestBase):
    def test_photo_is_added(self):
        answer = self.add_photo()
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(self.product.photos.count(), 1)

    def test_several_at_once(self):
        """Снимки делают пачкой, по одному их прикладывать мучительно."""
        answer = self.client.post(
            f'/api/products/{self.product.pk}/photos/',
            {'image': [picture('red'), picture('blue'), picture('green')]},
            format='multipart')
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(self.product.photos.count(), 3)

    def test_empty_upload_is_explained(self):
        answer = self.client.post(f'/api/products/{self.product.pk}/photos/',
                                  {}, format='multipart')
        self.assertEqual(answer.status_code, 400)
        self.assertIn('файл', answer.json()['error'])

    def test_there_is_a_limit(self):
        """Страница товара с полусотней снимков грузится дольше, чем
        покупатель готов ждать."""
        for _ in range(8):
            self.add_photo()
        answer = self.add_photo()
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(self.product.photos.count(), 8)

    def test_photo_is_compressed(self):
        """Снимок с телефона — это мегабайты. Восемь снимков на позицию
        и сотня позиций кончаются свободным местом на диске."""
        big = picture(size=(3000, 2000))
        self.add_photo(big)
        photo = self.product.photos.first()
        with Image.open(photo.image.path) as image:
            self.assertLessEqual(max(image.size), 2000)

    def test_stranger_cannot_upload(self):
        self.client.logout()
        answer = self.add_photo()
        self.assertIn(answer.status_code, (401, 403))


class ListingTest(PhotoTestBase):
    def test_product_answer_carries_the_photos(self):
        self.add_photo()
        row = self.client.get(f'/api/products/{self.product.pk}/').json()
        self.assertEqual(len(row['extra_photos']), 1)
        self.assertIn('image', row['extra_photos'][0])

    def test_cover_goes_first(self):
        """Обложка — та, что в самом товаре. Иначе витрина и списки
        показывали бы разные снимки одного товара."""
        self.product.photo = picture('white')
        self.product.save()
        self.add_photo(picture('black'))

        urls = self.product.photo_urls()
        self.assertEqual(len(urls), 2)
        self.assertEqual(urls[0], self.product.photo.url)

    def test_product_without_cover_still_shows_the_rest(self):
        self.add_photo()
        self.assertEqual(len(self.product.photo_urls()), 1)


class DeleteTest(PhotoTestBase):
    def test_photo_can_be_removed(self):
        self.add_photo()
        photo = self.product.photos.first()
        answer = self.client.delete(
            f'/api/products/{self.product.pk}/photos/{photo.pk}/')
        self.assertEqual(answer.status_code, 204)
        self.assertEqual(self.product.photos.count(), 0)

    def test_file_is_removed_from_disk(self):
        """Иначе папка media растёт от каждой замены снимка."""
        import os
        self.add_photo()
        photo = self.product.photos.first()
        path = photo.image.path
        self.assertTrue(os.path.exists(path))

        self.client.delete(
            f'/api/products/{self.product.pk}/photos/{photo.pk}/')
        self.assertFalse(os.path.exists(path))

    def test_missing_photo_is_explained(self):
        answer = self.client.delete(
            f'/api/products/{self.product.pk}/photos/9999/')
        self.assertEqual(answer.status_code, 404)

    def test_photos_go_with_the_product(self):
        self.add_photo()
        self.product.delete()
        self.assertEqual(ProductPhoto.objects.count(), 0)


class ShopTest(PhotoTestBase):
    """Что видит покупатель."""

    def setUp(self):
        super().setUp()
        self.warehouse = Warehouse.objects.create(name='Готовая', type='finished')
        Stock.objects.create(warehouse=self.warehouse, product=self.product,
                             quantity=Decimal('5'))

    def variant(self):
        answer = self.client.get('/api/shop/products/').json()
        return answer['products'][0]['colors'][0]['variants'][0]

    def test_variant_carries_all_photos(self):
        self.product.photo = picture('white')
        self.product.save()
        self.add_photo(picture('black'))

        variant = self.variant()
        self.assertEqual(len(variant['photos']), 2)

    def test_old_photo_field_is_still_there(self):
        """На него опираются превью в списке и закладки покупателей."""
        self.product.photo = picture('white')
        self.product.save()
        self.assertIsNotNone(self.variant()['photo'])

    def test_product_without_photos_does_not_break_the_shop(self):
        variant = self.variant()
        self.assertEqual(variant['photos'], [])
        self.assertIsNone(variant['photo'])
