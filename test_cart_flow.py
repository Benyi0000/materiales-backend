"""
Prueba de integración del flujo de la spec 'Carrito y Pedidos'.
Ejecutar: python test_cart_flow.py
Usa la base de datos real (SQLite) dentro de una transacción que se revierte al final.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ.setdefault('CELERY_TASK_ALWAYS_EAGER', 'true')
os.environ.setdefault('EMAIL_BACKEND', 'django.core.mail.backends.locmem.EmailBackend')
django.setup()

from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.models import User
from rest_framework.test import APIRequestFactory, force_authenticate

from catalog.models import Product
from orders.models import Coupon, Cart, Order
from orders.views import CartView, CartItemView, CartCouponView, OrderViewSet, CouponViewSet
from users.models import Profile, UserProfileAssignment

factory = APIRequestFactory()
PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'OK ' if condition else 'FALLO'} {name}" + (f" -> {detail}" if detail and not condition else ""))


class Rollback(Exception):
    pass


try:
    with transaction.atomic():
        # ----- Preparación -----
        buyer = User.objects.create_user(username='test_comprador', email='comprador@test.com', password='x')
        client_profile = Profile.objects.get(name="Comprar en la tienda")
        UserProfileAssignment.objects.create(user=buyer, profile=client_profile)

        admin = User.objects.filter(is_superuser=True).first() or User.objects.create_superuser('test_admin', 'a@a.com', 'x')

        product = Product.objects.filter(is_active=True, stock__gte=10).first()
        assert product, "Se necesita al menos un producto activo con stock >= 10"
        initial_stock = product.stock
        print(f"Producto de prueba: {product.name} (stock inicial: {initial_stock})")

        # ----- 1. Crear cupón (ABM con permiso marketing.gestionar_cupones via superuser) -----
        print("\n[1] ABM de cupones")
        req = factory.post('/api/orders/coupons/', {
            'code': 'test10',
            'description': 'Cupón de prueba',
            'discount_type': 'percent',
            'value': '10.00',
            'valid_from': (timezone.now() - timedelta(days=1)).isoformat(),
            'valid_until': (timezone.now() + timedelta(days=7)).isoformat(),
            'max_uses': 5,
        }, format='json')
        force_authenticate(req, user=admin)
        res = CouponViewSet.as_view({'post': 'create'})(req)
        check("Crear cupón TEST10 (10%)", res.status_code == 201, str(res.data))
        check("Código normalizado a mayúsculas", res.data.get('code') == 'TEST10')

        # ----- 2. Carrito persistente -----
        print("\n[2] Carrito persistente")
        req = factory.post('/api/orders/cart/items/', {'product_id': product.id, 'quantity': 2}, format='json')
        force_authenticate(req, user=buyer)
        res = CartItemView.as_view()(req)
        check("Agregar 2 unidades al carrito", res.status_code in (200, 201), str(res.data))
        check("Carrito persistido en BD", Cart.objects.filter(user=buyer, items__product=product).exists())

        # RN-03: pedir más que el stock ajusta
        req = factory.patch(f'/api/orders/cart/items/{product.id}/', {'quantity': product.stock + 999}, format='json')
        force_authenticate(req, user=buyer)
        res = CartItemView.as_view()(req, product_id=product.id)
        qty_in_cart = Cart.objects.get(user=buyer).items.get(product=product).quantity
        check("RN-03: cantidad ajustada al stock", res.status_code == 200 and qty_in_cart == product.stock and 'adjusted' in res.data)

        # volver a 2 unidades
        req = factory.patch(f'/api/orders/cart/items/{product.id}/', {'quantity': 2}, format='json')
        force_authenticate(req, user=buyer)
        CartItemView.as_view()(req, product_id=product.id)

        # ----- 3. Cupones -----
        print("\n[3] Aplicación de cupones")
        req = factory.post('/api/orders/cart/coupon/', {'code': 'NOEXISTE'}, format='json')
        force_authenticate(req, user=buyer)
        res = CartCouponView.as_view()(req)
        check("RN-10: cupón inexistente -> 404", res.status_code == 404)

        req = factory.post('/api/orders/cart/coupon/', {'code': 'test10'}, format='json')
        force_authenticate(req, user=buyer)
        res = CartCouponView.as_view()(req)
        expected_subtotal = float(product.price * 2)
        check("Aplicar TEST10", res.status_code == 200, str(res.data))
        check("Descuento del 10% calculado", abs(res.data['discount_amount'] - expected_subtotal * 0.10) < 0.01,
              f"discount={res.data['discount_amount']} esperado={expected_subtotal * 0.10}")
        check("Total = subtotal - descuento", abs(res.data['total'] - expected_subtotal * 0.90) < 0.01)

        # Cupón vencido
        Coupon.objects.create(
            code='VIEJO20', discount_type='fixed', value=500,
            valid_from=timezone.now() - timedelta(days=30),
            valid_until=timezone.now() - timedelta(days=1),
        )
        req = factory.post('/api/orders/cart/coupon/', {'code': 'VIEJO20'}, format='json')
        force_authenticate(req, user=buyer)
        res = CartCouponView.as_view()(req)
        check("RN-10: cupón vencido rechazado", res.status_code == 400 and 'vencido' in res.data['error'].lower())

        # ----- 4. Checkout -----
        print("\n[4] Checkout (todo-o-nada, estado pending, descuento)")
        req = factory.post('/api/orders/orders/', {}, format='json')
        force_authenticate(req, user=buyer)
        res = OrderViewSet.as_view({'post': 'create'})(req)
        check("Checkout exitoso", res.status_code == 201, str(res.data))
        order_id = res.data['id']
        order = Order.objects.get(id=order_id)
        product.refresh_from_db()
        check("RN-13: pedido nace Pendiente de Pago", order.status == 'pending')
        check("Stock descontado (-2)", product.stock == initial_stock - 2, f"stock={product.stock}")
        check("Cupón registrado en el pedido", order.coupon is not None and order.coupon.code == 'TEST10')
        check("Total con descuento", abs(float(order.total) - expected_subtotal * 0.90) < 0.01)
        check("Carrito vaciado tras checkout", not Cart.objects.get(user=buyer).items.exists())
        check("times_used incrementado", Coupon.objects.get(code='TEST10').times_used == 1)

        # RN-08: segundo uso del mismo cupón por el mismo usuario -> rechazado
        req = factory.post('/api/orders/cart/items/', {'product_id': product.id, 'quantity': 1}, format='json')
        force_authenticate(req, user=buyer)
        CartItemView.as_view()(req)
        req = factory.post('/api/orders/cart/coupon/', {'code': 'TEST10'}, format='json')
        force_authenticate(req, user=buyer)
        res = CartCouponView.as_view()(req)
        check("RN-08: cupón ya utilizado rechazado", res.status_code == 400 and 'utilizaste' in res.data['error'].lower())

        # RN-11: checkout con stock insuficiente -> todo-o-nada
        req = factory.patch(f'/api/orders/cart/items/{product.id}/', {'quantity': 1}, format='json')
        force_authenticate(req, user=buyer)
        CartItemView.as_view()(req, product_id=product.id)
        stock_before = Product.objects.get(id=product.id).stock
        Product.objects.filter(id=product.id).update(stock=0)
        req = factory.post('/api/orders/orders/', {}, format='json')
        force_authenticate(req, user=buyer)
        res = OrderViewSet.as_view({'post': 'create'})(req)
        check("RN-11: rechazo total por stock insuficiente", res.status_code == 400 and 'stock' in res.data)
        Product.objects.filter(id=product.id).update(stock=stock_before)

        # ----- 5. Cancelación -----
        print("\n[5] Cancelación (RN-15)")
        req = factory.post(f'/api/orders/orders/{order_id}/cancel/', {}, format='json')
        force_authenticate(req, user=buyer)
        res = OrderViewSet.as_view({'post': 'cancel'})(req, pk=order_id)
        order.refresh_from_db()
        product.refresh_from_db()
        check("Cancelar pedido pendiente", res.status_code == 200, str(res.data))
        check("Estado -> cancelled", order.status == 'cancelled')
        check("Stock repuesto", product.stock == initial_stock, f"stock={product.stock} esperado={initial_stock}")

        # No se puede cancelar dos veces
        req = factory.post(f'/api/orders/orders/{order_id}/cancel/', {}, format='json')
        force_authenticate(req, user=buyer)
        res = OrderViewSet.as_view({'post': 'cancel'})(req, pk=order_id)
        check("No se puede cancelar un pedido ya cancelado", res.status_code == 400)

        # ----- 6. Historial -----
        print("\n[6] Historial paginado y filtrado")
        req = factory.get('/api/orders/orders/?status=cancelled')
        force_authenticate(req, user=buyer)
        res = OrderViewSet.as_view({'get': 'list'})(req)
        check("Listado paginado (claves count/results)", 'count' in res.data and 'results' in res.data)
        check("Filtro por estado cancelled", all(o['status'] == 'cancelled' for o in res.data['results']))
        check("Solo pedidos propios", all(o['username'] == buyer.username for o in res.data['results']))

        raise Rollback()
except Rollback:
    pass

print(f"\n{'='*50}\nResultado: {len(PASS)} OK, {len(FAIL)} FALLOS")
if FAIL:
    print("Fallos:", FAIL)
    raise SystemExit(1)
print("Todos los checks pasaron. (Transacción revertida, BD sin cambios)")
