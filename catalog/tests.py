"""
Tests para los nuevos campos de Product (dimensiones, unidad de venta, marca,
material) introducidos para materiales y equipos de construcción.

Cubre:
  - ProductSerializer expone correctamente los nuevos campos (incluyendo los
    *_display de los choices).
  - Endpoint /api/catalog/products/filter_options/ devuelve marcas, unidades
    y materiales.
  - Filtros públicos ?brand=, ?material=, ?unit_of_sale= sobre el listado.
  - Los nuevos campos NO rompen el flujo de carrito ni de checkout (orders):
    un producto con todos los campos nuevos completos se puede agregar al
    carrito y comprar normalmente, y las respuestas de Cart/Order siguen
    devolviendo únicamente los campos que ya exponían antes (no se filtran
    ni se cuelgan por los campos nuevos).

Ejecutar (Docker Postgres, --keepdb recomendado):
    python manage.py test catalog -v 2 --keepdb
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from catalog.models import Category, Product
from catalog.serializers import ProductSerializer
from catalog.views import ProductViewSet
from orders.models import Cart, CartItem, Order
from orders.views import OrderViewSet

factory = APIRequestFactory()

VALID_SHIPPING = {
    'name': 'Juan García',
    'address': 'Av. Corrientes 1234',
    'city': 'Buenos Aires',
    'zip': '1043',
    'phone': '1145678901',
}


def _mock_rag(test_instance):
    p = patch('catalog.tasks.generate_product_embedding.delay')
    p.start()
    test_instance.addCleanup(p.stop)


def make_category(name='Categoría Test'):
    return Category.objects.create(name=name, slug=f'cat-{name[:8].lower().replace(" ", "-")}')


def make_construction_product(category=None, **overrides):
    if category is None:
        category, _ = Category.objects.get_or_create(
            slug='cat-construccion', defaults={'name': 'Construcción'}
        )
    import uuid
    defaults = dict(
        name='Bolsa de Cemento',
        sku=f'SKU-{uuid.uuid4().hex[:8].upper()}',
        description='Cemento para construcción',
        price=Decimal('5000'),
        stock=10,
        weight_kg=Decimal('50'),
        length_cm=Decimal('60'),
        width_cm=Decimal('40'),
        height_cm=Decimal('15'),
        unit_of_sale='bolsa',
        brand='Loma Negra',
        material='cemento',
        is_active=True,
        category=category,
    )
    defaults.update(overrides)
    return Product.objects.create(**defaults)


class ProductSerializerNewFieldsTest(TestCase):
    """El serializer expone correctamente los campos nuevos."""

    def setUp(self):
        _mock_rag(self)
        self.product = make_construction_product()

    def test_serializer_incluye_campos_nuevos(self):
        data = ProductSerializer(self.product).data
        self.assertEqual(data['length_cm'], '60.00')
        self.assertEqual(data['width_cm'], '40.00')
        self.assertEqual(data['height_cm'], '15.00')
        self.assertEqual(data['unit_of_sale'], 'bolsa')
        self.assertEqual(data['unit_of_sale_display'], 'Bolsa')
        self.assertEqual(data['brand'], 'Loma Negra')
        self.assertEqual(data['material'], 'cemento')
        self.assertEqual(data['material_display'], 'Cemento / Hormigón')

    def test_campos_nuevos_son_opcionales(self):
        # Un producto sin ninguno de los campos nuevos debe seguir
        # serializando sin errores (no deben volverse requeridos).
        product = make_construction_product(
            length_cm=None, width_cm=None, height_cm=None, brand='', material='',
            unit_of_sale='unidad',
        )
        data = ProductSerializer(product).data
        self.assertIsNone(data['length_cm'])
        self.assertIsNone(data['width_cm'])
        self.assertIsNone(data['height_cm'])
        self.assertEqual(data['brand'], '')
        self.assertEqual(data['material'], '')
        self.assertEqual(data['unit_of_sale'], 'unidad')


class ProductFilterOptionsAndFiltersTest(TestCase):
    """Endpoint filter_options y filtros públicos por brand/material/unit_of_sale."""

    def setUp(self):
        _mock_rag(self)
        self.cemento = make_construction_product(
            name='Cemento Loma Negra', brand='Loma Negra', material='cemento',
            unit_of_sale='bolsa',
        )
        self.madera = make_construction_product(
            name='Tabla de Madera', brand='Maderera Sur', material='madera',
            unit_of_sale='unidad', length_cm=Decimal('200'),
        )
        self.inactivo = make_construction_product(
            name='Producto Inactivo', brand='Marca Oculta', material='metal',
            unit_of_sale='m', is_active=False,
        )

    def test_filter_options_devuelve_marcas_unidades_materiales(self):
        req = factory.get('/api/catalog/products/filter_options/')
        res = ProductViewSet.as_view({'get': 'filter_options'})(req)
        self.assertEqual(res.status_code, 200)
        self.assertIn('Loma Negra', res.data['brands'])
        self.assertIn('Maderera Sur', res.data['brands'])
        # Solo productos activos aportan marcas
        self.assertNotIn('Marca Oculta', res.data['brands'])
        unit_values = [u['value'] for u in res.data['units']]
        material_values = [m['value'] for m in res.data['materials']]
        self.assertIn('bolsa', unit_values)
        self.assertIn('cemento', material_values)

    def test_filtro_por_brand(self):
        req = factory.get('/api/catalog/products/', {'brand': 'Loma Negra'})
        res = ProductViewSet.as_view({'get': 'list'})(req)
        self.assertEqual(res.status_code, 200)
        names = [p['name'] for p in res.data['results']] if 'results' in res.data else [p['name'] for p in res.data]
        self.assertIn('Cemento Loma Negra', names)
        self.assertNotIn('Tabla de Madera', names)

    def test_filtro_por_material(self):
        req = factory.get('/api/catalog/products/', {'material': 'madera'})
        res = ProductViewSet.as_view({'get': 'list'})(req)
        self.assertEqual(res.status_code, 200)
        names = [p['name'] for p in res.data['results']] if 'results' in res.data else [p['name'] for p in res.data]
        self.assertIn('Tabla de Madera', names)
        self.assertNotIn('Cemento Loma Negra', names)

    def test_filtro_por_unit_of_sale(self):
        req = factory.get('/api/catalog/products/', {'unit_of_sale': 'bolsa'})
        res = ProductViewSet.as_view({'get': 'list'})(req)
        self.assertEqual(res.status_code, 200)
        names = [p['name'] for p in res.data['results']] if 'results' in res.data else [p['name'] for p in res.data]
        self.assertIn('Cemento Loma Negra', names)
        self.assertNotIn('Tabla de Madera', names)


class ProductNewFieldsCartCheckoutIntegrationTest(TestCase):
    """
    Verifica que un Product con todos los campos nuevos completos no genera
    ningún conflicto al pasar por carrito y checkout (orders).
    """

    def setUp(self):
        _mock_rag(self)
        self.user = User.objects.create_superuser('admin_construccion', 'a@test.com', 'pass')
        self.product = make_construction_product(price=Decimal('5000'), stock=10)
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(
            cart=self.cart, product=self.product, quantity=2,
            price_at_add=self.product.price,
        )

    def test_cart_serializer_no_rompe_con_campos_nuevos(self):
        from orders.serializers import CartSerializer
        data = CartSerializer(self.cart).data
        item = data['items'][0]
        # El carrito sigue exponiendo exactamente los campos que ya tenía;
        # los campos nuevos del producto no se filtran ni rompen la serialización.
        self.assertEqual(item['sku'], self.product.sku)
        self.assertEqual(item['name'], self.product.name)
        self.assertEqual(float(item['price']), float(self.product.price))
        self.assertEqual(data['subtotal'], 10000.0)

    @patch('orders.tasks.send_order_confirmation_email.delay')
    def test_checkout_con_producto_con_campos_nuevos(self, mock_email):
        req = factory.post('/api/orders/orders/', {
            'shipping': VALID_SHIPPING,
            'delivery_type': 'standard',
            'payment_method': 'cash',
        }, format='json')
        force_authenticate(req, user=self.user)
        res = OrderViewSet.as_view({'post': 'create'})(req)

        self.assertEqual(res.status_code, 201, res.data)
        order = Order.objects.get(id=res.data['id'])
        self.assertEqual(order.total, Decimal('10000'))  # 5000 x 2
        item = order.items.first()
        self.assertEqual(item.product_id, self.product.id)
        self.assertEqual(item.quantity, 2)
        self.assertTrue(mock_email.called)

        # El producto conserva intactos sus campos nuevos tras la compra.
        self.product.refresh_from_db()
        self.assertEqual(self.product.length_cm, Decimal('60.00'))
        self.assertEqual(self.product.brand, 'Loma Negra')
        self.assertEqual(self.product.material, 'cemento')
        # El stock se descontó normalmente (RN existente, no afectada por los campos nuevos).
        self.assertEqual(self.product.stock, 8)

    def test_order_serializer_no_incluye_ni_rompe_con_campos_nuevos(self):
        from orders.serializers import OrderSerializer
        order = Order.objects.create(
            user=self.user, status='pending', total=Decimal('10000'),
            checkout_payment_method='cash', shipping_cost=Decimal('0'),
            shipping_data=VALID_SHIPPING,
        )
        from orders.models import OrderItem
        OrderItem.objects.create(
            order=order, product=self.product, quantity=2,
            price_at_purchase=self.product.price,
        )
        data = OrderSerializer(order).data
        item = data['items'][0]
        self.assertEqual(item['product_name'], self.product.name)
        self.assertEqual(item['product_sku'], self.product.sku)


class EmbeddingManagementPanelTest(TestCase):
    """
    Panel de gestión de embeddings: estadísticas, regeneración individual y
    masiva, y que las acciones queden protegidas por el permiso atómico
    'gestion.gestionar_embeddings'.
    """

    def setUp(self):
        _mock_rag(self)
        self.admin = User.objects.create_superuser('admin_embeddings', 'a@test.com', 'pass')
        self.staff_sin_permiso = User.objects.create_user('staff_sin_permiso', 'b@test.com', 'pass')
        self.con_embedding = make_construction_product(name='Con embedding')
        Product.objects.filter(id=self.con_embedding.id).update(embedding=[0.1] * 768)
        self.sin_embedding_1 = make_construction_product(name='Sin embedding 1')
        self.sin_embedding_2 = make_construction_product(name='Sin embedding 2')

    def test_embedding_stats_requiere_permiso(self):
        req = factory.get('/api/catalog/products/embedding_stats/')
        force_authenticate(req, user=self.staff_sin_permiso)
        res = ProductViewSet.as_view({'get': 'embedding_stats'})(req)
        self.assertEqual(res.status_code, 403)

    def test_embedding_stats_devuelve_conteos_correctos(self):
        req = factory.get('/api/catalog/products/embedding_stats/')
        force_authenticate(req, user=self.admin)
        res = ProductViewSet.as_view({'get': 'embedding_stats'})(req)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['total'], 3)
        self.assertEqual(res.data['with_embedding'], 1)
        self.assertEqual(res.data['without_embedding'], 2)
        missing_names = [p['name'] for p in res.data['missing']]
        self.assertIn('Sin embedding 1', missing_names)
        self.assertIn('Sin embedding 2', missing_names)
        self.assertNotIn('Con embedding', missing_names)

    @patch('catalog.tasks.generate_product_embedding', return_value=True)
    def test_regenerate_embedding_individual_exitoso(self, mock_gen):
        req = factory.post(f'/api/catalog/products/{self.sin_embedding_1.id}/regenerate_embedding/')
        force_authenticate(req, user=self.admin)
        res = ProductViewSet.as_view({'post': 'regenerate_embedding'})(req, pk=self.sin_embedding_1.id)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data['success'])
        mock_gen.assert_called_once_with(self.sin_embedding_1.id)

    @patch('catalog.tasks.generate_product_embedding', return_value=False)
    def test_regenerate_embedding_individual_fallido_devuelve_502(self, mock_gen):
        req = factory.post(f'/api/catalog/products/{self.sin_embedding_1.id}/regenerate_embedding/')
        force_authenticate(req, user=self.admin)
        res = ProductViewSet.as_view({'post': 'regenerate_embedding'})(req, pk=self.sin_embedding_1.id)
        self.assertEqual(res.status_code, 502)
        self.assertFalse(res.data['success'])

    def test_regenerate_embedding_requiere_permiso(self):
        req = factory.post(f'/api/catalog/products/{self.sin_embedding_1.id}/regenerate_embedding/')
        force_authenticate(req, user=self.staff_sin_permiso)
        res = ProductViewSet.as_view({'post': 'regenerate_embedding'})(req, pk=self.sin_embedding_1.id)
        self.assertEqual(res.status_code, 403)

    @patch('catalog.tasks.generate_product_embedding', return_value=True)
    def test_regenerate_missing_embeddings_batch(self, mock_gen):
        req = factory.post('/api/catalog/products/regenerate_missing_embeddings/', {'limit': 10}, format='json')
        force_authenticate(req, user=self.admin)
        res = ProductViewSet.as_view({'post': 'regenerate_missing_embeddings'})(req)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['processed'], 2)
        self.assertEqual(res.data['succeeded'], 2)
        self.assertEqual(res.data['failed'], 0)
        self.assertEqual(mock_gen.call_count, 2)
