"""
Tests exhaustivos para el módulo orders.

Cubre:
  - normalizar_celular_ar         (unitarios, sin DB)
  - validate_shipping_fields      (unitarios, sin DB)
  - Modelo Order                  (nuevos campos checkout_payment_method y shipping_cost)
  - OrderCreateSerializer         (flujo tarjeta/efectivo con nuevos campos)
  - MercadoPagoPreferenceView     (flujo MP con SDK mockeado)
  - MercadoPagoWebhookView        (webhook aprobado, rechazado, cancelado)
  - OrderSerializer               (exposición de nuevos campos)
  - Migración 0007                (los campos existen en la tabla)

Ejecutar:
    python manage.py test orders -v 2
"""

from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework.test import APIRequestFactory, force_authenticate

from catalog.models import Product, Category
from orders.models import Cart, CartItem, Order, OrderItem
from orders.serializers import normalizar_celular_ar, validate_shipping_fields, OrderSerializer
from orders.views import (
    CartItemView, MercadoPagoPreferenceView, MercadoPagoWebhookView, OrderViewSet,
)

factory = APIRequestFactory()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_rag(test_instance):
    """
    Desactiva el signal que genera embeddings de producto con Gemini durante tests.
    Sin esto, crear un Product dispara una tarea Celery que llama a la API de IA
    y falla dentro de la transacción, corrompiendo el test.
    """
    p = patch('catalog.tasks.generate_product_embedding.delay')
    p.start()
    test_instance.addCleanup(p.stop)

VALID_SHIPPING = {
    'name': 'Juan García',
    'address': 'Av. Corrientes 1234',
    'city': 'Buenos Aires',
    'zip': '1043',
    'phone': '1145678901',
}


def make_superuser(username='admin_test'):
    return User.objects.create_superuser(username, f'{username}@test.com', 'pass')


def make_category(name='Categoría Test'):
    return Category.objects.create(name=name, slug=f'cat-{name[:8].lower().replace(" ", "-")}')


def make_product(name='Producto Test', price=1000, stock=20, category=None):
    if category is None:
        category, _ = Category.objects.get_or_create(
            slug='cat-test', defaults={'name': 'Categoría Test'}
        )
    import uuid
    return Product.objects.create(
        name=name,
        sku=f'SKU-{uuid.uuid4().hex[:8].upper()}',
        description='Descripción de prueba',
        price=Decimal(price),
        stock=stock,
        weight_kg=Decimal('0.5'),
        is_active=True,
        category=category,
    )


def add_to_cart(user, product, quantity=2):
    cart, _ = Cart.objects.get_or_create(user=user)
    CartItem.objects.create(cart=cart, product=product, quantity=quantity,
                            price_at_add=product.price)
    return cart


# ---------------------------------------------------------------------------
# 1. normalizar_celular_ar
# ---------------------------------------------------------------------------

class TestNormalizarCelularAr(TestCase):
    """Unitarios puros — sin acceso a DB."""

    # --- Formatos válidos ---

    def test_diez_digitos_planos(self):
        self.assertEqual(normalizar_celular_ar('1145678901'), '1145678901')

    def test_con_espacios(self):
        self.assertEqual(normalizar_celular_ar('11 4567 8901'), '1145678901')

    def test_con_guiones(self):
        self.assertEqual(normalizar_celular_ar('11-4567-8901'), '1145678901')

    def test_con_cero_area(self):
        self.assertEqual(normalizar_celular_ar('01145678901'), '1145678901')

    def test_formato_internacional_54_9(self):
        self.assertEqual(normalizar_celular_ar('+54 9 11 4567 8901'), '1145678901')

    def test_formato_internacional_54_sin_9(self):
        # Fijo de CABA — 10 dígitos directos
        result = normalizar_celular_ar('+54 11 4567 8901')
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 10)

    def test_interior_sin_prefijo(self):
        self.assertEqual(normalizar_celular_ar('3704123456'), '3704123456')

    def test_interior_con_0(self):
        self.assertEqual(normalizar_celular_ar('03704123456'), '3704123456')

    def test_interior_con_54_9(self):
        self.assertEqual(normalizar_celular_ar('+54 9 370 4123456'), '3704123456')

    def test_interior_con_15(self):
        # "011 15 4567-8901" → normaliza a 10 dígitos
        result = normalizar_celular_ar('011 15 4567-8901')
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 10)

    def test_numero_con_espacios_extra(self):
        self.assertEqual(normalizar_celular_ar('  1145678901  '), '1145678901')

    # --- Formatos inválidos ---

    def test_vacio(self):
        self.assertIsNone(normalizar_celular_ar(''))

    def test_none(self):
        self.assertIsNone(normalizar_celular_ar(None))

    def test_muy_corto(self):
        self.assertIsNone(normalizar_celular_ar('12345'))

    def test_muy_largo(self):
        self.assertIsNone(normalizar_celular_ar('123456789012345'))

    def test_letras(self):
        self.assertIsNone(normalizar_celular_ar('abcdefghij'))

    def test_numero_extranjero(self):
        # Número de EE.UU. — no es AR
        self.assertIsNone(normalizar_celular_ar('+1 212 555 1234'))

    def test_solo_ceros(self):
        self.assertIsNone(normalizar_celular_ar('0000000000'))

    def test_numero_ficticio_invalido(self):
        self.assertIsNone(normalizar_celular_ar('9999999999'))


# ---------------------------------------------------------------------------
# 2. validate_shipping_fields
# ---------------------------------------------------------------------------

class TestValidateShippingFields(TestCase):
    """Unitarios — sin acceso a DB."""

    def _ok(self, data):
        """Verifica que validate_shipping_fields no lanza error."""
        result = validate_shipping_fields(data)
        self.assertIsInstance(result, dict)
        return result

    def _err(self, data, field=None):
        """Verifica que lanza ValidationError; opcionalmente verifica el campo."""
        with self.assertRaises(drf_serializers.ValidationError) as ctx:
            validate_shipping_fields(data)
        if field:
            errors = ctx.exception.detail.get('shipping', {})
            self.assertIn(field, errors, f"Se esperaba error en '{field}', se obtuvo: {errors}")
        return ctx.exception

    # --- Casos válidos ---

    def test_datos_completos_validos(self):
        result = self._ok(VALID_SHIPPING)
        self.assertEqual(result['name'], 'Juan García')
        self.assertEqual(result['city'], 'Buenos Aires')
        self.assertEqual(len(result['phone']), 10)

    def test_zip_y_phone_opcionales(self):
        data = {**VALID_SHIPPING, 'zip': '', 'phone': ''}
        result = self._ok(data)
        self.assertEqual(result['zip'], '')
        self.assertEqual(result['phone'], '')

    def test_ciudad_con_numeros(self):
        # "9 de Julio", "25 de Mayo" — ciudades argentinas con números
        for city in ['9 de Julio', '25 de Mayo', '3 de Febrero', 'Villa 9 de Julio']:
            result = self._ok({**VALID_SHIPPING, 'city': city})
            self.assertEqual(result['city'], city)

    def test_ciudad_con_acento(self):
        result = self._ok({**VALID_SHIPPING, 'city': 'Córdoba'})
        self.assertEqual(result['city'], 'Córdoba')

    def test_nombre_con_acento_y_apostrofe(self):
        result = self._ok({**VALID_SHIPPING, 'name': "María O'Brien"})
        self.assertEqual(result['name'], "María O'Brien")

    def test_phone_formato_internacional(self):
        result = self._ok({**VALID_SHIPPING, 'phone': '+54 9 11 4567 8901'})
        self.assertEqual(len(result['phone']), 10)

    def test_strip_espacios_en_campos(self):
        data = {**VALID_SHIPPING, 'name': '  Juan García  ', 'city': '  CABA  '}
        result = self._ok(data)
        self.assertEqual(result['name'], 'Juan García')
        self.assertEqual(result['city'], 'CABA')

    # --- Nombre ---

    def test_nombre_obligatorio(self):
        self._err({**VALID_SHIPPING, 'name': ''}, 'name')

    def test_nombre_muy_corto(self):
        self._err({**VALID_SHIPPING, 'name': 'AB'}, 'name')

    def test_nombre_con_digitos(self):
        self._err({**VALID_SHIPPING, 'name': 'Juan123'}, 'name')

    # --- Dirección ---

    def test_direccion_obligatoria(self):
        self._err({**VALID_SHIPPING, 'address': ''}, 'address')

    def test_direccion_muy_corta(self):
        self._err({**VALID_SHIPPING, 'address': 'Cll'}, 'address')

    # --- Ciudad ---

    def test_ciudad_obligatoria(self):
        self._err({**VALID_SHIPPING, 'city': ''}, 'city')

    def test_ciudad_muy_corta(self):
        self._err({**VALID_SHIPPING, 'city': 'X'}, 'city')

    def test_ciudad_con_caracteres_invalidos(self):
        self._err({**VALID_SHIPPING, 'city': 'Ciudad@#$'}, 'city')

    # --- Código postal ---

    def test_zip_cinco_digitos_invalido(self):
        self._err({**VALID_SHIPPING, 'zip': '12345'}, 'zip')

    def test_zip_letras_invalido(self):
        self._err({**VALID_SHIPPING, 'zip': 'ABCD'}, 'zip')

    def test_zip_tres_digitos_invalido(self):
        self._err({**VALID_SHIPPING, 'zip': '123'}, 'zip')

    def test_zip_cuatro_digitos_valido(self):
        result = self._ok({**VALID_SHIPPING, 'zip': '1043'})
        self.assertEqual(result['zip'], '1043')

    # --- Teléfono ---

    def test_phone_invalido(self):
        self._err({**VALID_SHIPPING, 'phone': '123'}, 'phone')

    def test_phone_letras_invalido(self):
        self._err({**VALID_SHIPPING, 'phone': 'abcdefghij'}, 'phone')

    def test_phone_extranjero_invalido(self):
        self._err({**VALID_SHIPPING, 'phone': '+1 212 555 1234'}, 'phone')

    # --- Múltiples errores a la vez ---

    def test_multiples_errores(self):
        data = {'name': 'X', 'address': '', 'city': '', 'zip': 'AAAA', 'phone': '123'}
        with self.assertRaises(drf_serializers.ValidationError) as ctx:
            validate_shipping_fields(data)
        errors = ctx.exception.detail.get('shipping', {})
        for field in ('name', 'address', 'city', 'zip', 'phone'):
            self.assertIn(field, errors, f"Falta error en campo '{field}'")


# ---------------------------------------------------------------------------
# 3. Modelo Order — campos nuevos
# ---------------------------------------------------------------------------

class TestOrderModelFields(TestCase):

    def setUp(self):
        _mock_rag(self)
        self.user = make_superuser()

    def test_checkout_payment_method_default_vacio(self):
        order = Order.objects.create(user=self.user, total=0)
        self.assertEqual(order.checkout_payment_method, '')

    def test_shipping_cost_default_cero(self):
        order = Order.objects.create(user=self.user, total=0)
        self.assertEqual(order.shipping_cost, Decimal('0'))

    def test_guardar_checkout_payment_method_mercadopago(self):
        order = Order.objects.create(
            user=self.user, total=1000,
            checkout_payment_method='mercadopago',
        )
        order.refresh_from_db()
        self.assertEqual(order.checkout_payment_method, 'mercadopago')

    def test_guardar_checkout_payment_method_cash(self):
        order = Order.objects.create(
            user=self.user, total=1000, checkout_payment_method='cash',
        )
        order.refresh_from_db()
        self.assertEqual(order.checkout_payment_method, 'cash')

    def test_guardar_checkout_payment_method_card(self):
        order = Order.objects.create(
            user=self.user, total=1000, checkout_payment_method='card',
        )
        order.refresh_from_db()
        self.assertEqual(order.checkout_payment_method, 'card')

    def test_guardar_shipping_cost_express(self):
        order = Order.objects.create(
            user=self.user, total=14000, shipping_cost=Decimal('4000'),
        )
        order.refresh_from_db()
        self.assertEqual(order.shipping_cost, Decimal('4000'))

    def test_filtrar_por_checkout_payment_method(self):
        Order.objects.create(user=self.user, total=1000, checkout_payment_method='mercadopago')
        Order.objects.create(user=self.user, total=500, checkout_payment_method='cash')
        mp_orders = Order.objects.filter(checkout_payment_method='mercadopago')
        cash_orders = Order.objects.filter(checkout_payment_method='cash')
        self.assertEqual(mp_orders.count(), 1)
        self.assertEqual(cash_orders.count(), 1)

    def test_invariante_total(self):
        """total == subtotal - discount + shipping_cost"""
        subtotal = Decimal('10000')
        discount = Decimal('1000')
        shipping_cost = Decimal('4000')
        expected_total = subtotal - discount + shipping_cost  # 13000
        order = Order.objects.create(
            user=self.user,
            total=expected_total,
            discount_amount=discount,
            shipping_cost=shipping_cost,
        )
        order.refresh_from_db()
        self.assertEqual(order.total, expected_total)
        self.assertEqual(
            order.total,
            subtotal - order.discount_amount + order.shipping_cost,
        )

    def test_campos_en_tabla_db(self):
        """Verifica que los campos realmente existen en la tabla (migración aplicada)."""
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA table_info(orders_order)" if
                           connection.vendor == 'sqlite' else
                           "SELECT column_name FROM information_schema.columns WHERE table_name='orders_order'")
            columns = [row[1] if connection.vendor == 'sqlite' else row[0]
                       for row in cursor.fetchall()]
        self.assertIn('checkout_payment_method', columns)
        self.assertIn('shipping_cost', columns)


# ---------------------------------------------------------------------------
# 4. OrderCreateSerializer — flujo tarjeta / efectivo
# ---------------------------------------------------------------------------

class TestOrderCreateSerializerCashFlow(TestCase):

    def setUp(self):
        _mock_rag(self)
        self.user = make_superuser()
        self.product = make_product(price=5000, stock=10)
        add_to_cart(self.user, self.product, quantity=2)

    def _post_checkout(self, data):
        req = factory.post('/api/orders/orders/', data, format='json')
        force_authenticate(req, user=self.user)
        return OrderViewSet.as_view({'post': 'create'})(req)

    @patch('orders.tasks.send_order_confirmation_email.delay')
    def test_checkout_efectivo_estandar(self, mock_email):
        res = self._post_checkout({
            'shipping': VALID_SHIPPING,
            'delivery_type': 'standard',
            'payment_method': 'cash',
        })
        self.assertEqual(res.status_code, 201, res.data)
        order = Order.objects.get(id=res.data['id'])
        self.assertEqual(order.checkout_payment_method, 'cash')
        self.assertEqual(order.shipping_cost, Decimal('0'))
        self.assertEqual(order.total, Decimal('10000'))  # 5000 × 2
        self.assertTrue(mock_email.called)

    @patch('orders.tasks.send_order_confirmation_email.delay')
    def test_checkout_tarjeta_express(self, mock_email):
        res = self._post_checkout({
            'shipping': VALID_SHIPPING,
            'delivery_type': 'express',
            'payment_method': 'card',
        })
        self.assertEqual(res.status_code, 201, res.data)
        order = Order.objects.get(id=res.data['id'])
        self.assertEqual(order.checkout_payment_method, 'card')
        self.assertEqual(order.shipping_cost, Decimal('4000'))
        self.assertEqual(order.total, Decimal('14000'))  # 5000×2 + 4000

    @patch('orders.tasks.send_order_confirmation_email.delay')
    def test_invariante_total_con_descuento_y_express(self, _):
        from orders.models import Coupon, CouponRedemption
        coupon = Coupon.objects.create(
            code='DESC20', discount_type='percent', value=20,
            valid_from=timezone.now() - timezone.timedelta(days=1),
            valid_until=timezone.now() + timezone.timedelta(days=7),
        )
        cart = Cart.objects.get(user=self.user)
        cart.coupon = coupon
        cart.save()

        res = self._post_checkout({
            'shipping': VALID_SHIPPING,
            'delivery_type': 'express',
            'payment_method': 'cash',
        })
        self.assertEqual(res.status_code, 201, res.data)
        order = Order.objects.get(id=res.data['id'])
        # subtotal=10000, descuento=2000 (20%), envío=4000 → total=12000
        self.assertEqual(order.discount_amount, Decimal('2000'))
        self.assertEqual(order.shipping_cost, Decimal('4000'))
        self.assertEqual(order.total, Decimal('12000'))
        # Invariante
        items_subtotal = sum(
            i.price_at_purchase * i.quantity for i in order.items.all()
        )
        self.assertEqual(
            order.total,
            items_subtotal - order.discount_amount + order.shipping_cost,
        )

    def test_checkout_sin_datos_envio_falla(self):
        res = self._post_checkout({
            'delivery_type': 'standard',
            'payment_method': 'cash',
        })
        self.assertEqual(res.status_code, 400)

    def test_checkout_datos_envio_invalidos_falla(self):
        res = self._post_checkout({
            'shipping': {**VALID_SHIPPING, 'name': 'X'},  # nombre muy corto
            'delivery_type': 'standard',
            'payment_method': 'cash',
        })
        self.assertEqual(res.status_code, 400)

    def test_checkout_payment_method_invalido(self):
        res = self._post_checkout({
            'shipping': VALID_SHIPPING,
            'delivery_type': 'standard',
            'payment_method': 'bitcoin',
        })
        self.assertEqual(res.status_code, 400)

    def test_checkout_delivery_type_invalido(self):
        res = self._post_checkout({
            'shipping': VALID_SHIPPING,
            'delivery_type': 'drone',
            'payment_method': 'cash',
        })
        self.assertEqual(res.status_code, 400)

    def test_checkout_carrito_vacio_falla(self):
        Cart.objects.get(user=self.user).items.all().delete()
        res = self._post_checkout({
            'shipping': VALID_SHIPPING,
            'delivery_type': 'standard',
            'payment_method': 'cash',
        })
        self.assertEqual(res.status_code, 400)

    @patch('orders.tasks.send_order_confirmation_email.delay')
    def test_stock_descontado_en_checkout(self, _):
        stock_inicial = self.product.stock
        self._post_checkout({
            'shipping': VALID_SHIPPING,
            'delivery_type': 'standard',
            'payment_method': 'cash',
        })
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, stock_inicial - 2)

    @patch('orders.tasks.send_order_confirmation_email.delay')
    def test_carrito_vaciado_post_checkout(self, _):
        self._post_checkout({
            'shipping': VALID_SHIPPING,
            'delivery_type': 'standard',
            'payment_method': 'cash',
        })
        cart = Cart.objects.get(user=self.user)
        self.assertFalse(cart.items.exists())

    @patch('orders.tasks.send_order_confirmation_email.delay')
    def test_shipping_data_guardado_en_json(self, _):
        res = self._post_checkout({
            'shipping': VALID_SHIPPING,
            'delivery_type': 'standard',
            'payment_method': 'cash',
        })
        order = Order.objects.get(id=res.data['id'])
        self.assertEqual(order.shipping_data['name'], VALID_SHIPPING['name'])
        self.assertEqual(order.shipping_data['city'], VALID_SHIPPING['city'])
        self.assertEqual(order.shipping_data['delivery_type'], 'standard')

    @patch('orders.tasks.send_order_confirmation_email.delay')
    def test_status_es_pending_en_flujo_card_cash(self, _):
        res = self._post_checkout({
            'shipping': VALID_SHIPPING,
            'delivery_type': 'standard',
            'payment_method': 'cash',
        })
        order = Order.objects.get(id=res.data['id'])
        self.assertEqual(order.status, 'pending')


# ---------------------------------------------------------------------------
# 5. MercadoPagoPreferenceView — flujo MP (SDK mockeado)
# ---------------------------------------------------------------------------

FAKE_PREF_RESPONSE = {
    'status': 201,
    'response': {
        'id': 'PREF-123456789',
        'init_point': 'https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=PREF-123456789',
        'sandbox_init_point': 'https://sandbox.mercadopago.com.ar/checkout/v1/redirect?pref_id=PREF-123456789',
    }
}

BAD_PREF_RESPONSE = {'status': 500, 'response': {}}


def _patch_mp(pref_response=None):
    """Context manager / decorador que mockea mercadopago.SDK."""
    resp = pref_response or FAKE_PREF_RESPONSE
    mock_sdk = MagicMock()
    mock_sdk.return_value.preference.return_value.create.return_value = resp
    return patch('mercadopago.SDK', mock_sdk)


class TestMercadoPagoPreferenceView(TestCase):

    def setUp(self):
        _mock_rag(self)
        self.user = make_superuser()
        self.product = make_product(price=5000, stock=10)
        add_to_cart(self.user, self.product, quantity=2)

    def _post_mp(self, data=None):
        payload = data or {'shipping': VALID_SHIPPING, 'delivery_type': 'standard'}
        req = factory.post('/api/orders/mp/create-preference/', payload, format='json')
        force_authenticate(req, user=self.user)
        return MercadoPagoPreferenceView.as_view()(req)

    def test_crea_order_con_checkout_payment_method_mercadopago(self):
        with _patch_mp():
            res = self._post_mp()
        self.assertEqual(res.status_code, 201, res.data)
        order = Order.objects.get(id=res.data['order_id'])
        self.assertEqual(order.checkout_payment_method, 'mercadopago')

    def test_shipping_cost_estandar_es_cero(self):
        with _patch_mp():
            res = self._post_mp({'shipping': VALID_SHIPPING, 'delivery_type': 'standard'})
        self.assertEqual(res.status_code, 201, res.data)
        order = Order.objects.get(id=res.data['order_id'])
        self.assertEqual(order.shipping_cost, Decimal('0'))

    def test_shipping_cost_express_es_4000(self):
        with _patch_mp():
            res = self._post_mp({'shipping': VALID_SHIPPING, 'delivery_type': 'express'})
        self.assertEqual(res.status_code, 201, res.data)
        order = Order.objects.get(id=res.data['order_id'])
        self.assertEqual(order.shipping_cost, Decimal('4000'))

    def test_total_con_express_incluye_envio(self):
        with _patch_mp():
            res = self._post_mp({'shipping': VALID_SHIPPING, 'delivery_type': 'express'})
        order = Order.objects.get(id=res.data['order_id'])
        self.assertEqual(order.total, Decimal('14000'))  # 5000×2 + 4000

    def test_status_inicial_es_pending_payment(self):
        with _patch_mp():
            res = self._post_mp()
        order = Order.objects.get(id=res.data['order_id'])
        self.assertEqual(order.status, 'pending_payment')

    def test_mp_preference_id_guardado(self):
        with _patch_mp():
            res = self._post_mp()
        order = Order.objects.get(id=res.data['order_id'])
        self.assertEqual(order.mp_preference_id, 'PREF-123456789')

    def test_respuesta_incluye_init_points(self):
        with _patch_mp():
            res = self._post_mp()
        self.assertIn('init_point', res.data)
        self.assertIn('sandbox_init_point', res.data)
        self.assertIn('order_id', res.data)

    def test_stock_descontado_al_crear_preferencia(self):
        stock_inicial = self.product.stock
        with _patch_mp():
            self._post_mp()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, stock_inicial - 2)

    def test_carrito_vaciado_tras_crear_preferencia(self):
        with _patch_mp():
            self._post_mp()
        cart = Cart.objects.get(user=self.user)
        self.assertFalse(cart.items.exists())

    def test_datos_envio_invalidos_devuelve_400(self):
        with _patch_mp():
            res = self._post_mp({'shipping': {**VALID_SHIPPING, 'name': 'X'}, 'delivery_type': 'standard'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('shipping', res.data)

    def test_delivery_type_invalido_devuelve_400(self):
        with _patch_mp():
            res = self._post_mp({'shipping': VALID_SHIPPING, 'delivery_type': 'teleport'})
        self.assertEqual(res.status_code, 400)

    def test_carrito_vacio_devuelve_400(self):
        Cart.objects.get(user=self.user).items.all().delete()
        with _patch_mp():
            res = self._post_mp()
        self.assertEqual(res.status_code, 400)

    def test_mp_falla_revierte_order_a_cancelled(self):
        with _patch_mp(BAD_PREF_RESPONSE):
            res = self._post_mp()
        self.assertEqual(res.status_code, 502)
        self.assertTrue(Order.objects.filter(status='cancelled').exists())

    def test_mp_falla_restaura_stock(self):
        stock_inicial = self.product.stock
        with _patch_mp(BAD_PREF_RESPONSE):
            self._post_mp()
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, stock_inicial)

    def test_stock_insuficiente_devuelve_400(self):
        self.product.stock = 1
        self.product.save()
        with _patch_mp():
            res = self._post_mp()
        self.assertEqual(res.status_code, 400)


# ---------------------------------------------------------------------------
# 6. MercadoPagoWebhookView
# ---------------------------------------------------------------------------

FAKE_PAYMENT_APPROVED = {
    'status': 200,
    'response': {
        'id': '987654321',
        'status': 'approved',
        'status_detail': 'accredited',
        'payment_type_id': 'credit_card',
        'payment_method_id': 'visa',
        'installments': 1,
        'transaction_amount': 10000.0,
        'net_received_amount': 9650.0,
        'fee_details': [{'type': 'mercadopago_fee', 'amount': 350.0, 'fee_payer': 'collector'}],
        'currency_id': 'ARS',
        'payer': {'email': 'comprador@test.com', 'id': 111},
        'card': {'last_four_digits': '1234', 'cardholder': {'name': 'JUAN GARCIA'}},
        'date_approved': '2026-06-15T10:00:00Z',
        'date_created': '2026-06-15T09:59:00Z',
        'external_reference': None,  # se sobreescribe en cada test
    }
}

FAKE_PAYMENT_REJECTED = {
    'status': 200,
    'response': {
        'id': '987654322',
        'status': 'rejected',
        'status_detail': 'cc_rejected_insufficient_amount',
        'payment_type_id': 'credit_card',
        'payment_method_id': 'master',
        'installments': 1,
        'transaction_amount': 10000.0,
        'net_received_amount': 0,
        'fee_details': [],
        'currency_id': 'ARS',
        'payer': {'email': 'comprador@test.com', 'id': 222},
        'card': {'last_four_digits': '9999', 'cardholder': {'name': 'TEST USER'}},
        'date_approved': '',
        'date_created': '2026-06-15T09:59:00Z',
        'external_reference': None,
    }
}


class TestMercadoPagoWebhookView(TestCase):

    def setUp(self):
        _mock_rag(self)
        self.user = make_superuser()
        self.product = make_product(price=5000, stock=10)

    def _make_pending_payment_order(self, quantity=2):
        order = Order.objects.create(
            user=self.user,
            total=Decimal(str(5000 * quantity)),
            status='pending_payment',
            checkout_payment_method='mercadopago',
            shipping_cost=Decimal('0'),
            mp_preference_id='PREF-TEST',
        )
        OrderItem.objects.create(
            order=order, product=self.product,
            quantity=quantity, price_at_purchase=self.product.price,
        )
        self.product.stock -= quantity
        self.product.save()
        return order

    def _post_webhook(self, payload, payment_response=None):
        resp = payment_response or FAKE_PAYMENT_APPROVED
        mock_sdk = MagicMock()
        mock_sdk.return_value.payment.return_value.get.return_value = resp
        with patch('mercadopago.SDK', mock_sdk):
            req = factory.post('/api/orders/mp/webhook/', payload, format='json')
            return MercadoPagoWebhookView.as_view()(req)

    # --- Pago aprobado ---

    def test_pago_aprobado_status_cambia_a_pending(self):
        order = self._make_pending_payment_order()
        payment_resp = {
            **FAKE_PAYMENT_APPROVED,
            'response': {**FAKE_PAYMENT_APPROVED['response'], 'external_reference': str(order.id)},
        }
        res = self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654321'}},
            payment_response=payment_resp,
        )
        self.assertEqual(res.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')

    def test_pago_aprobado_guarda_mp_paid_at(self):
        order = self._make_pending_payment_order()
        payment_resp = {
            **FAKE_PAYMENT_APPROVED,
            'response': {**FAKE_PAYMENT_APPROVED['response'], 'external_reference': str(order.id)},
        }
        self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654321'}},
            payment_response=payment_resp,
        )
        order.refresh_from_db()
        self.assertIsNotNone(order.mp_paid_at)

    def test_pago_aprobado_guarda_mp_payment_id(self):
        order = self._make_pending_payment_order()
        payment_resp = {
            **FAKE_PAYMENT_APPROVED,
            'response': {**FAKE_PAYMENT_APPROVED['response'], 'external_reference': str(order.id)},
        }
        self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654321'}},
            payment_response=payment_resp,
        )
        order.refresh_from_db()
        self.assertEqual(order.mp_payment_id, '987654321')

    def test_pago_aprobado_guarda_net_received_amount(self):
        order = self._make_pending_payment_order()
        payment_resp = {
            **FAKE_PAYMENT_APPROVED,
            'response': {**FAKE_PAYMENT_APPROVED['response'], 'external_reference': str(order.id)},
        }
        self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654321'}},
            payment_response=payment_resp,
        )
        order.refresh_from_db()
        self.assertIn('net_received_amount', order.mp_payment_data)
        self.assertEqual(order.mp_payment_data['net_received_amount'], '9650.0')

    def test_pago_aprobado_guarda_fee_details(self):
        order = self._make_pending_payment_order()
        payment_resp = {
            **FAKE_PAYMENT_APPROVED,
            'response': {**FAKE_PAYMENT_APPROVED['response'], 'external_reference': str(order.id)},
        }
        self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654321'}},
            payment_response=payment_resp,
        )
        order.refresh_from_db()
        self.assertIn('fee_details', order.mp_payment_data)
        self.assertIsInstance(order.mp_payment_data['fee_details'], list)
        self.assertEqual(len(order.mp_payment_data['fee_details']), 1)
        self.assertEqual(order.mp_payment_data['fee_details'][0]['type'], 'mercadopago_fee')

    def test_pago_aprobado_guarda_datos_tarjeta(self):
        order = self._make_pending_payment_order()
        payment_resp = {
            **FAKE_PAYMENT_APPROVED,
            'response': {**FAKE_PAYMENT_APPROVED['response'], 'external_reference': str(order.id)},
        }
        self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654321'}},
            payment_response=payment_resp,
        )
        order.refresh_from_db()
        self.assertEqual(order.mp_payment_data['card_last_four'], '1234')
        self.assertEqual(order.mp_payment_data['payment_method'], 'visa')
        self.assertEqual(order.mp_payment_data['installments'], 1)

    @patch('orders.tasks.send_order_confirmation_email.delay')
    def test_pago_aprobado_dispara_email(self, mock_email):
        order = self._make_pending_payment_order()
        payment_resp = {
            **FAKE_PAYMENT_APPROVED,
            'response': {**FAKE_PAYMENT_APPROVED['response'], 'external_reference': str(order.id)},
        }
        self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654321'}},
            payment_response=payment_resp,
        )
        self.assertTrue(mock_email.called)

    # --- Pago rechazado ---

    def test_pago_rechazado_status_cambia_a_cancelled(self):
        order = self._make_pending_payment_order()
        payment_resp = {
            **FAKE_PAYMENT_REJECTED,
            'response': {**FAKE_PAYMENT_REJECTED['response'], 'external_reference': str(order.id)},
        }
        self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654322'}},
            payment_response=payment_resp,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

    def test_pago_rechazado_restaura_stock(self):
        stock_antes = self.product.stock
        order = self._make_pending_payment_order(quantity=2)
        stock_post_reserva = self.product.stock
        self.assertEqual(stock_post_reserva, stock_antes - 2)

        payment_resp = {
            **FAKE_PAYMENT_REJECTED,
            'response': {**FAKE_PAYMENT_REJECTED['response'], 'external_reference': str(order.id)},
        }
        self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654322'}},
            payment_response=payment_resp,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, stock_antes)

    def test_pago_rechazado_guarda_status_en_snapshot(self):
        order = self._make_pending_payment_order()
        payment_resp = {
            **FAKE_PAYMENT_REJECTED,
            'response': {**FAKE_PAYMENT_REJECTED['response'], 'external_reference': str(order.id)},
        }
        self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654322'}},
            payment_response=payment_resp,
        )
        order.refresh_from_db()
        self.assertEqual(order.mp_payment_data['status'], 'rejected')

    # --- Casos límite ---

    def test_webhook_order_inexistente_devuelve_404(self):
        payment_resp = {
            **FAKE_PAYMENT_APPROVED,
            'response': {**FAKE_PAYMENT_APPROVED['response'], 'external_reference': '99999'},
        }
        res = self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654321'}},
            payment_response=payment_resp,
        )
        self.assertEqual(res.status_code, 404)

    def test_webhook_topic_desconocido_ignorado(self):
        res = self._post_webhook({'type': 'subscription', 'data': {'id': '123'}})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data.get('status'), 'ignored')

    def test_webhook_sin_topic_ignorado(self):
        res = self._post_webhook({})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data.get('status'), 'ignored')

    def test_pago_aprobado_en_order_ya_pending_no_duplica(self):
        """Un segundo webhook aprobado para un pedido ya confirmado no cambia nada."""
        order = self._make_pending_payment_order()
        order.status = 'pending'
        order.mp_paid_at = timezone.now()
        order.save()

        payment_resp = {
            **FAKE_PAYMENT_APPROVED,
            'response': {**FAKE_PAYMENT_APPROVED['response'], 'external_reference': str(order.id)},
        }
        self._post_webhook(
            {'type': 'payment', 'data': {'id': '987654321'}},
            payment_response=payment_resp,
        )
        order.refresh_from_db()
        # El status sigue siendo 'pending' — no retrocede ni avanza
        self.assertEqual(order.status, 'pending')


# ---------------------------------------------------------------------------
# 7. OrderSerializer — campos nuevos expuestos en la respuesta
# ---------------------------------------------------------------------------

class TestOrderSerializerOutput(TestCase):

    def setUp(self):
        _mock_rag(self)
        self.user = make_superuser()

    def test_serializer_expone_checkout_payment_method(self):
        order = Order.objects.create(
            user=self.user, total=10000, checkout_payment_method='mercadopago',
        )
        data = OrderSerializer(order).data
        self.assertIn('checkout_payment_method', data)
        self.assertEqual(data['checkout_payment_method'], 'mercadopago')

    def test_serializer_expone_shipping_cost(self):
        order = Order.objects.create(
            user=self.user, total=14000, shipping_cost=Decimal('4000'),
        )
        data = OrderSerializer(order).data
        self.assertIn('shipping_cost', data)
        self.assertEqual(Decimal(data['shipping_cost']), Decimal('4000'))

    def test_serializer_defaults_pedidos_existentes(self):
        order = Order.objects.create(user=self.user, total=5000)
        data = OrderSerializer(order).data
        self.assertEqual(data['checkout_payment_method'], '')
        self.assertEqual(Decimal(data['shipping_cost']), Decimal('0'))

    def test_api_get_order_incluye_nuevos_campos(self):
        order = Order.objects.create(
            user=self.user, total=10000,
            checkout_payment_method='cash', shipping_cost=Decimal('0'),
        )
        req = factory.get(f'/api/orders/orders/{order.id}/')
        force_authenticate(req, user=self.user)
        res = OrderViewSet.as_view({'get': 'retrieve'})(req, pk=order.id)
        self.assertEqual(res.status_code, 200)
        self.assertIn('checkout_payment_method', res.data)
        self.assertIn('shipping_cost', res.data)

    def test_api_listado_incluye_nuevos_campos(self):
        Order.objects.create(user=self.user, total=5000, checkout_payment_method='card')
        req = factory.get('/api/orders/orders/')
        force_authenticate(req, user=self.user)
        res = OrderViewSet.as_view({'get': 'list'})(req)
        self.assertEqual(res.status_code, 200)
        results = res.data.get('results', [])
        self.assertTrue(len(results) > 0)
        self.assertIn('checkout_payment_method', results[0])
        self.assertIn('shipping_cost', results[0])
