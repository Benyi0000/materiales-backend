"""
Test de integracion end-to-end: flujo completo de compra.

Verifica:
  1. Validacion de datos de envio
  2. Checkout (efectivo + express)
  3. Checkout (tarjeta + envio estandar)
  4. Estado y campos en DB (checkout_payment_method, shipping_cost, total, items, shipping_data)
  5. Invariante total = subtotal - descuento + shipping_cost
  6. Stock descontado correctamente
  7. Carrito vaciado
  8. La venta aparece en el Modulo Ventas para el usuario interno
  9. El comprador puede ver sus propias compras (Modulo Pedidos)
 10. Validaciones de envio invalido rechazan el checkout
 11. Sin conflicto con stock de otros productos ni con pedidos existentes
 12. Cancelacion repone stock
 13. Compatibilidad: el Modulo ventas retorna los nuevos campos

Ejecutar:
    python test_compra_integracion.py

Nota: corre contra la DB real en una transaccion que se revierte al final.
No deja datos persistidos.
"""

import os
import sys
import django
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ.setdefault('CELERY_TASK_ALWAYS_EAGER', 'true')
os.environ.setdefault('EMAIL_BACKEND', 'django.core.mail.backends.locmem.EmailBackend')
django.setup()

from decimal import Decimal
from django.db import transaction
from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory, force_authenticate

from catalog.models import Product
from orders.models import Cart, CartItem, Order, OrderItem, CouponRedemption, Coupon
from orders.views import CartItemView, CartCouponView, OrderViewSet
from orders.serializers import validate_shipping_fields
from rest_framework import serializers as drf_serializers
from users.models import Profile, UserProfileAssignment

factory = APIRequestFactory()
PASS, FAIL = [], []


def ok(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    icon = '  OK  ' if cond else '  FALLO'
    extra = f'  -> {detail}' if detail and not cond else (f'  ({detail})' if detail and cond else '')
    print(f'{icon}  {name}{extra}')


def section(title):
    print(f'\n{"-"*60}')
    print(f'  {title}')
    print(f'{"-"*60}')


class Rollback(Exception):
    pass


# --------------------------------------------------------------------------------------------------------------------------
# Datos de envio de prueba
# --------------------------------------------------------------------------------------------------------------------------
SHIPPING_VALIDO = {
    'name': 'Maria Gonzalez',
    'address': 'Av. San Martin 1450',
    'city': '9 de Julio',        # ciudad argentina con numero
    'zip': '6500',
    'phone': '1145678901',       # CABA valido segun phonenumbers
}

SHIPPING_INVALIDO = {
    'name': 'X',                 # muy corto
    'address': 'Av',             # muy corta
    'city': '',                  # vacia
    'zip': 'ABCD',              # no numerico
    'phone': '+1 212 555 1234', # numero extranjero
}


try:
    with transaction.atomic():

        # ----------------------------------------------------------------------------------------------------------
        section('PREPARACION')
        # ----------------------------------------------------------------------------------------------------------

        # Comprador con perfil "Comprar en la tienda"
        buyer = User.objects.create_user(
            username='integ_comprador', email='comprador@integ.test', password='x'
        )
        perfil_comprador = Profile.objects.get(name='Comprar en la tienda')
        UserProfileAssignment.objects.create(user=buyer, profile=perfil_comprador)

        # Usuario interno con "Gestionsar ventas"
        gestor = User.objects.create_user(
            username='integ_gestor', email='gestor@integ.test', password='x'
        )
        perfil_gestor = Profile.objects.get(name='Gestionsar ventas')
        UserProfileAssignment.objects.create(user=gestor, profile=perfil_gestor)

        # Producto con suficiente stock
        producto_a = Product.objects.filter(is_active=True, stock__gte=5).first()
        producto_b = Product.objects.filter(
            is_active=True, stock__gte=3
        ).exclude(id=producto_a.id).first()

        assert producto_a, 'Se necesita al menos 1 producto activo con stock >= 5'
        assert producto_b, 'Se necesita al menos 2 productos activos con stock >= 3'

        stock_a_inicial = producto_a.stock
        stock_b_inicial = producto_b.stock
        pedidos_previos = Order.objects.count()

        print(f'  Comprador:  {buyer.username}')
        print(f'  Gestor:     {gestor.username}')
        print(f'  Producto A: {producto_a.name} (stock={stock_a_inicial}, precio={producto_a.price})')
        print(f'  Producto B: {producto_b.name} (stock={stock_b_inicial}, precio={producto_b.price})')
        print(f'  Pedidos previos en DB: {pedidos_previos}')

        with patch('catalog.tasks.generate_product_embedding.delay'), \
             patch('orders.tasks.send_order_confirmation_email.delay'), \
             patch('orders.tasks.send_order_status_change_email.delay'):

            # --------------------------------------------------------------------------------------------------
            section('1 · Validacion DE DATOS DE ENViO')
            # --------------------------------------------------------------------------------------------------

            try:
                validate_shipping_fields(SHIPPING_VALIDO)
                ok('Datos validos (ciudad con numero "9 de Julio") pasan', True)
            except drf_serializers.ValidationError as e:
                ok('Datos validos pasan', False, str(e))

            try:
                validate_shipping_fields(SHIPPING_INVALIDO)
                ok('Datos invalidos son rechazados', False, 'deberia haber lanzado error')
            except drf_serializers.ValidationError as e:
                errores = e.detail.get('shipping', {})
                ok('Datos invalidos son rechazados', True, f'{len(errores)} campos con error')
                for campo, msg in errores.items():
                    print(f'       {campo}: {msg}')

            try:
                validate_shipping_fields({**SHIPPING_VALIDO, 'phone': '+1 212 555 1234'})
                ok('Telefono extranjero (+1) es rechazado', False, 'deberia rechazar numero no AR')
            except drf_serializers.ValidationError:
                ok('Telefono extranjero (+1) es rechazado', True)

            try:
                validate_shipping_fields({**SHIPPING_VALIDO, 'phone': '+54 9 11 4567 8901'})
                ok('Telefono AR con +54 9 es aceptado', True)
            except drf_serializers.ValidationError as e:
                ok('Telefono AR con +54 9 es aceptado', False, str(e))

            # --------------------------------------------------------------------------------------------------
            section('2 · CHECKOUT EFECTIVO · ENViO estandar')
            # --------------------------------------------------------------------------------------------------

            # Agregar al carrito
            req = factory.post('/api/orders/cart/items/', {'product_id': producto_a.id, 'quantity': 2}, format='json')
            force_authenticate(req, user=buyer)
            CartItemView.as_view()(req)

            # Confirmar pedido
            req = factory.post('/api/orders/orders/', {
                'shipping': SHIPPING_VALIDO,
                'delivery_type': 'standard',
                'payment_method': 'cash',
            }, format='json')
            force_authenticate(req, user=buyer)
            res = OrderViewSet.as_view({'post': 'create'})(req)

            ok('Checkout efectivo devuelve 201', res.status_code == 201, str(res.data))

            order_efectivo = Order.objects.get(id=res.data['id'])
            precio_a = producto_a.price
            subtotal_efectivo = precio_a * 2

            ok('checkout_payment_method = "cash"',
               order_efectivo.checkout_payment_method == 'cash',
               f'obtenido: {order_efectivo.checkout_payment_method}')

            ok('shipping_cost = 0 (estandar)',
               order_efectivo.shipping_cost == Decimal('0'),
               f'obtenido: {order_efectivo.shipping_cost}')

            ok('status = "pending"',
               order_efectivo.status == 'pending',
               f'obtenido: {order_efectivo.status}')

            ok('total correcto (sin envio)',
               order_efectivo.total == subtotal_efectivo,
               f'esperado={subtotal_efectivo}, obtenido={order_efectivo.total}')

            ok('Invariante: total == subtotal - descuento + shipping_cost',
               order_efectivo.total == subtotal_efectivo - order_efectivo.discount_amount + order_efectivo.shipping_cost,
               f'{order_efectivo.total} vs {subtotal_efectivo} - {order_efectivo.discount_amount} + {order_efectivo.shipping_cost}')

            # Verificar OrderItems
            items = list(order_efectivo.items.all())
            ok('OrderItem creado (1 producto)', len(items) == 1, f'items={len(items)}')
            ok('OrderItem.quantity = 2', items[0].quantity == 2, f'qty={items[0].quantity}')
            ok('OrderItem.price_at_purchase correcto',
               items[0].price_at_purchase == precio_a,
               f'price={items[0].price_at_purchase}')

            # Verificar shipping_data
            sd = order_efectivo.shipping_data or {}
            ok('shipping_data.name guardado', sd.get('name') == SHIPPING_VALIDO['name'], f"name={sd.get('name')}")
            ok('shipping_data.city guardado (ciudad con numero)',
               sd.get('city') == SHIPPING_VALIDO['city'], f"city={sd.get('city')}")
            ok('shipping_data.delivery_type guardado', sd.get('delivery_type') == 'standard')
            ok('shipping_data.payment_method guardado', sd.get('payment_method') == 'cash')

            # Verificar stock
            producto_a.refresh_from_db()
            ok('Stock de producto A descontado en 2',
               producto_a.stock == stock_a_inicial - 2,
               f'esperado={stock_a_inicial - 2}, obtenido={producto_a.stock}')

            # Verificar carrito vaciado
            cart = Cart.objects.filter(user=buyer).first()
            ok('Carrito vaciado tras checkout', not cart or not cart.items.exists())

            # --------------------------------------------------------------------------------------------------
            section('3 · CHECKOUT TARJETA · ENViO EXPRESS')
            # --------------------------------------------------------------------------------------------------

            # Nuevo carrito con producto B
            req = factory.post('/api/orders/cart/items/', {'product_id': producto_b.id, 'quantity': 1}, format='json')
            force_authenticate(req, user=buyer)
            CartItemView.as_view()(req)

            req = factory.post('/api/orders/orders/', {
                'shipping': {**SHIPPING_VALIDO, 'city': '25 de Mayo'},  # otra ciudad con numero
                'delivery_type': 'express',
                'payment_method': 'card',
            }, format='json')
            force_authenticate(req, user=buyer)
            res = OrderViewSet.as_view({'post': 'create'})(req)

            ok('Checkout tarjeta+express devuelve 201', res.status_code == 201, str(res.data))

            order_tarjeta = Order.objects.get(id=res.data['id'])
            subtotal_tarjeta = producto_b.price * 1

            ok('checkout_payment_method = "card"',
               order_tarjeta.checkout_payment_method == 'card',
               f'obtenido: {order_tarjeta.checkout_payment_method}')

            ok('shipping_cost = 4000 (express)',
               order_tarjeta.shipping_cost == Decimal('4000'),
               f'obtenido: {order_tarjeta.shipping_cost}')

            ok('total incluye costo express',
               order_tarjeta.total == subtotal_tarjeta + Decimal('4000'),
               f'esperado={subtotal_tarjeta + 4000}, obtenido={order_tarjeta.total}')

            ok('Invariante total con express',
               order_tarjeta.total == subtotal_tarjeta - order_tarjeta.discount_amount + order_tarjeta.shipping_cost)

            sd2 = order_tarjeta.shipping_data or {}
            ok('Ciudad "25 de Mayo" guardada correctamente',
               sd2.get('city') == '25 de Mayo', f"city={sd2.get('city')}")

            producto_b.refresh_from_db()
            ok('Stock de producto B descontado en 1',
               producto_b.stock == stock_b_inicial - 1,
               f'esperado={stock_b_inicial - 1}, obtenido={producto_b.stock}')

            ok('Stock de producto A no fue afectado por segundo pedido',
               producto_a.stock == stock_a_inicial - 2,
               f'stock_a={producto_a.stock}, esperado={stock_a_inicial - 2}')

            # --------------------------------------------------------------------------------------------------
            section('4 · VALIDACIONES QUE DEBEN RECHAZAR EL CHECKOUT')
            # --------------------------------------------------------------------------------------------------

            # Carrito vacio
            req = factory.post('/api/orders/orders/', {
                'shipping': SHIPPING_VALIDO, 'delivery_type': 'standard', 'payment_method': 'cash',
            }, format='json')
            force_authenticate(req, user=buyer)
            res = OrderViewSet.as_view({'post': 'create'})(req)
            ok('Carrito vacio -> 400', res.status_code == 400, f'status={res.status_code}')

            # Datos de envio invalidos (rellenar carrito primero)
            req = factory.post('/api/orders/cart/items/', {'product_id': producto_a.id, 'quantity': 1}, format='json')
            force_authenticate(req, user=buyer)
            CartItemView.as_view()(req)

            req = factory.post('/api/orders/orders/', {
                'shipping': SHIPPING_INVALIDO,
                'delivery_type': 'standard', 'payment_method': 'cash',
            }, format='json')
            force_authenticate(req, user=buyer)
            res = OrderViewSet.as_view({'post': 'create'})(req)
            ok('Datos de envio invalidos -> 400', res.status_code == 400, str(res.data)[:80])

            # payment_method invalido
            req = factory.post('/api/orders/orders/', {
                'shipping': SHIPPING_VALIDO,
                'delivery_type': 'standard', 'payment_method': 'cripto',
            }, format='json')
            force_authenticate(req, user=buyer)
            res = OrderViewSet.as_view({'post': 'create'})(req)
            ok('payment_method invalido -> 400', res.status_code == 400)

            # delivery_type invalido
            req = factory.post('/api/orders/orders/', {
                'shipping': SHIPPING_VALIDO,
                'delivery_type': 'moto', 'payment_method': 'cash',
            }, format='json')
            force_authenticate(req, user=buyer)
            res = OrderViewSet.as_view({'post': 'create'})(req)
            ok('delivery_type invalido -> 400', res.status_code == 400)

            # Vaciar carrito para las proximas secciones
            Cart.objects.filter(user=buyer).first().items.all().delete()

            # --------------------------------------------------------------------------------------------------
            section('5 · Modulo VENTAS (usuario interno)')
            # --------------------------------------------------------------------------------------------------

            # Listar ventas como gestor
            req = factory.get('/api/orders/orders/?view=ventas')
            force_authenticate(req, user=gestor)
            res = OrderViewSet.as_view({'get': 'list'})(req)

            ok('Gestor puede listar ventas (200)', res.status_code == 200, str(res.status_code))

            todos_pedidos = res.data.get('results', [])
            ids_en_ventas = [o['id'] for o in todos_pedidos]

            ok('Pedido efectivo aparece en listado de ventas',
               order_efectivo.id in ids_en_ventas,
               f'buscando id={order_efectivo.id} en {ids_en_ventas[:5]}...')

            ok('Pedido tarjeta+express aparece en listado de ventas',
               order_tarjeta.id in ids_en_ventas,
               f'buscando id={order_tarjeta.id}')

            # Verificar campos nuevos en respuesta del gestor
            pedido_en_lista = next((o for o in todos_pedidos if o['id'] == order_efectivo.id), None)
            if pedido_en_lista:
                ok('checkout_payment_method visible en ventas',
                   'checkout_payment_method' in pedido_en_lista,
                   str(list(pedido_en_lista.keys())))
                ok('shipping_cost visible en ventas',
                   'shipping_cost' in pedido_en_lista)
                ok('checkout_payment_method = "cash" en ventas',
                   pedido_en_lista.get('checkout_payment_method') == 'cash',
                   f"valor={pedido_en_lista.get('checkout_payment_method')}")
                ok('shipping_cost = "0.00" en ventas',
                   Decimal(str(pedido_en_lista.get('shipping_cost', -1))) == Decimal('0'),
                   f"valor={pedido_en_lista.get('shipping_cost')}")
            else:
                ok('Pedido efectivo encontrado en detalle', False, 'no se encontro en la lista')

            pedido_express = next((o for o in todos_pedidos if o['id'] == order_tarjeta.id), None)
            if pedido_express:
                ok('shipping_cost = "4000.00" en pedido express en ventas',
                   Decimal(str(pedido_express.get('shipping_cost', -1))) == Decimal('4000'),
                   f"valor={pedido_express.get('shipping_cost')}")
                ok('checkout_payment_method = "card" en pedido express',
                   pedido_express.get('checkout_payment_method') == 'card')

            # Detalle de pedido individual
            req = factory.get(f'/api/orders/orders/{order_efectivo.id}/')
            force_authenticate(req, user=gestor)
            res = OrderViewSet.as_view({'get': 'retrieve'})(req, pk=order_efectivo.id)
            ok('Gestor puede ver detalle de pedido (200)', res.status_code == 200)
            if res.status_code == 200:
                ok('Detalle incluye items', 'items' in res.data and len(res.data['items']) > 0)
                ok('Detalle incluye shipping_data', 'shipping_data' in res.data)
                ok('Detalle shipping_data.name correcto',
                   res.data.get('shipping_data', {}).get('name') == SHIPPING_VALIDO['name'])

            # --------------------------------------------------------------------------------------------------
            section('6 · Modulo PEDIDOS (comprador ve sus compras)')
            # --------------------------------------------------------------------------------------------------

            req = factory.get('/api/orders/orders/')
            force_authenticate(req, user=buyer)
            res = OrderViewSet.as_view({'get': 'list'})(req)
            ok('Comprador puede listar sus pedidos (200)', res.status_code == 200)

            mis_pedidos = res.data.get('results', [])
            mis_ids = [o['id'] for o in mis_pedidos]
            ok('Pedido efectivo en mis compras', order_efectivo.id in mis_ids)
            ok('Pedido tarjeta en mis compras', order_tarjeta.id in mis_ids)

            # El comprador NO ve pedidos de otros (si existieran)
            ok('Solo aparecen pedidos propios',
               all(o['username'] == buyer.username for o in mis_pedidos),
               f'usernames: {[o["username"] for o in mis_pedidos]}')

            # --------------------------------------------------------------------------------------------------
            section('7 · Cancelacion Y Reposicion DE STOCK')
            # --------------------------------------------------------------------------------------------------

            stock_a_antes_cancel = producto_a.stock

            req = factory.post(f'/api/orders/orders/{order_efectivo.id}/cancel/', {}, format='json')
            force_authenticate(req, user=buyer)
            res = OrderViewSet.as_view({'post': 'cancel'})(req, pk=order_efectivo.id)

            ok('Cancelacion devuelve 200', res.status_code == 200, str(res.data))
            order_efectivo.refresh_from_db()
            ok('Estado cambia a "cancelled"', order_efectivo.status == 'cancelled')

            producto_a.refresh_from_db()
            ok('Stock de producto A repuesto tras Cancelacion',
               producto_a.stock == stock_a_antes_cancel + 2,
               f'esperado={stock_a_antes_cancel + 2}, obtenido={producto_a.stock}')

            # Segunda Cancelacion debe fallar
            req = factory.post(f'/api/orders/orders/{order_efectivo.id}/cancel/', {}, format='json')
            force_authenticate(req, user=buyer)
            res = OrderViewSet.as_view({'post': 'cancel'})(req, pk=order_efectivo.id)
            ok('Segunda Cancelacion -> 400', res.status_code == 400)

            # --------------------------------------------------------------------------------------------------
            section('8 · NO CONFLICTO CON DATOS EXISTENTES')
            # --------------------------------------------------------------------------------------------------

            # Pedidos previos no se modificaron
            total_pedidos_final = Order.objects.count()
            nuevos_pedidos = total_pedidos_final - pedidos_previos
            ok(f'Solo se crearon 2 pedidos nuevos (hay {nuevos_pedidos})', nuevos_pedidos == 2)

            # Stock de producto B (no cancelado) sigue descontado
            producto_b.refresh_from_db()
            ok('Stock de producto B sigue descontado (pedido no cancelado)',
               producto_b.stock == stock_b_inicial - 1,
               f'esperado={stock_b_inicial - 1}, obtenido={producto_b.stock}')

            # Stock de producto A fue repuesto (pedido cancelado)
            ok('Stock de producto A fue repuesto al nivel inicial',
               producto_a.stock == stock_a_inicial,
               f'esperado={stock_a_inicial}, obtenido={producto_a.stock}')

            # Gestor no puede ver compras del comprador en vista propia (solo en ventas)
            req = factory.get('/api/orders/orders/')
            force_authenticate(req, user=gestor)
            res = OrderViewSet.as_view({'get': 'list'})(req)
            compras_gestor = res.data.get('results', [])
            ok('Gestor en vista compras no ve pedidos del buyer',
               all(o['username'] == gestor.username for o in compras_gestor),
               f'usernames vistos: {set(o["username"] for o in compras_gestor)}')

        raise Rollback()

except Rollback:
    pass
except Exception as e:
    import traceback
    print(f'\n  ERROR INESPERADO: {e}')
    traceback.print_exc()

# --------------------------------------------------------------------------------------------------------------------------
print(f'\n{"="*60}')
print(f'  RESULTADO: {len(PASS)} OK  |  {len(FAIL)} FALLOS')
print(f'{"="*60}')
if FAIL:
    print('  FALLOS:')
    for f in FAIL:
        print(f'    • {f}')
    print()
    sys.exit(1)
else:
    print('  Todos los checks pasaron.')
    print('  (Transaccion revertida — la DB quedo sin cambios)')
    print()
