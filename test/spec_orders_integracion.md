# Especificación — Test de Integración End-to-End de Compra

**Archivo:** `test_compra_integracion.py` (raíz del backend)  
**Comando:** `python test_compra_integracion.py`  
**Total de checks:** 56  
**Estado:** Todos pasando

---

## Propósito

Verifica el flujo completo de una compra real contra la DB de desarrollo,
ejerciendo todas las capas: serializers, views, permisos, modelos y señales.

**No es un test de Django.** Es un script Python que:
1. Inicializa Django manualmente (`django.setup()`)
2. Crea datos de prueba dentro de una transacción atómica
3. Usa `APIRequestFactory` para simular requests HTTP
4. Verifica assertions con una función `ok(nombre, condición, detalle)`
5. **Revierte toda la transacción al terminar** — la DB queda intacta

---

## Precondiciones

La DB debe tener:
- Al menos **2 productos activos** con stock >= 5 y >= 3 respectivamente
- El perfil **`Comprar en la tienda`** creado y con permisos configurados
- El perfil **`Gestionsar ventas`** creado y con permisos configurados

Si falta alguno de estos, el script falla en la sección PREPARACION con un `AssertionError`.

---

## Variables de entorno requeridas

```powershell
$env:DATABASE_URL="postgresql://craftiar_user:craftiar_password@localhost:5432/craftiar_db"
$env:DJANGO_SETTINGS_MODULE="config.settings"
$env:CELERY_TASK_ALWAYS_EAGER="true"
$env:EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
```

---

## Mocks aplicados

El script usa `unittest.mock.patch` como context manager global sobre toda la ejecución:

```python
with patch('catalog.tasks.generate_product_embedding.delay'), \
     patch('orders.tasks.send_order_confirmation_email.delay'), \
     patch('orders.tasks.send_order_status_change_email.delay'):
    # ... todos los tests
```

MercadoPago **no se testea** en este script (el flujo MP es unitario en `orders/tests.py`).

---

## Secciones y checks

### PREPARACION (0 checks — solo setup)

Crea:
- Usuario `integ_comprador` con perfil `Comprar en la tienda`
- Usuario `integ_gestor` con perfil `Gestionsar ventas`
- Toma `producto_a` (primer producto activo con stock >= 5)
- Toma `producto_b` (segundo producto activo con stock >= 3, distinto de A)
- Registra `stock_a_inicial`, `stock_b_inicial`, `pedidos_previos`

---

### Sección 1 — Validacion de Datos de Envio (4 checks)

**Datos de prueba:**

```python
SHIPPING_VALIDO = {
    'name': 'Maria Gonzalez',
    'address': 'Av. San Martin 1450',
    'city': '9 de Julio',      # ciudad con numero en el nombre
    'zip': '6500',
    'phone': '1145678901',     # CABA, valido segun phonenumbers
}

SHIPPING_INVALIDO = {
    'name': 'X',               # muy corto
    'address': 'Av',           # muy corta
    'city': '',                # vacia
    'zip': 'ABCD',             # no numerico
    'phone': '+1 212 555 1234' # numero extranjero
}
```

| # | Check | Descripcion |
|---|-------|-------------|
| 1 | `Datos validos pasan` | `SHIPPING_VALIDO` no lanza `ValidationError`. Verifica que ciudades con números como "9 de Julio" son aceptadas. |
| 2 | `Datos invalidos son rechazados` | `SHIPPING_INVALIDO` lanza error con exactamente 5 campos con error. |
| 3 | `Telefono extranjero (+1) es rechazado` | `phone='+1 212 555 1234'` con datos válidos en el resto → `ValidationError` en `phone`. |
| 4 | `Telefono AR con +54 9 es aceptado` | `phone='+54 9 11 4567 8901'` → pasa validación. |

---

### Sección 2 — Checkout Efectivo + Envio Estandar (15 checks)

**Accion:** Comprador agrega `producto_a` x2 al carrito. Confirma con `payment_method=cash`, `delivery_type=standard`.

| # | Check | Valor esperado |
|---|-------|---------------|
| 5 | `Checkout efectivo devuelve 201` | HTTP 201 |
| 6 | `checkout_payment_method = "cash"` | `order.checkout_payment_method == 'cash'` |
| 7 | `shipping_cost = 0 (estandar)` | `order.shipping_cost == '0.00'` |
| 8 | `status = "pending"` | `order.status == 'pending'` |
| 9 | `total correcto (sin envio)` | `total == producto_a.price * 2` |
| 10 | `Invariante total == subtotal - descuento + shipping_cost` | `25000 == 25000 - 0 + 0` |
| 11 | `OrderItem creado (1 producto)` | `items.count() == 1` |
| 12 | `OrderItem.quantity = 2` | `items[0].quantity == 2` |
| 13 | `OrderItem.price_at_purchase correcto` | `items[0].price_at_purchase == producto_a.price` |
| 14 | `shipping_data.name guardado` | `shipping_data['name'] == 'Maria Gonzalez'` |
| 15 | `shipping_data.city guardado (ciudad con numero)` | `shipping_data['city'] == '9 de Julio'` |
| 16 | `shipping_data.delivery_type guardado` | `shipping_data['delivery_type'] == 'standard'` |
| 17 | `shipping_data.payment_method guardado` | `shipping_data['payment_method'] == 'cash'` |
| 18 | `Stock de producto A descontado en 2` | `producto_a.stock == stock_a_inicial - 2` |
| 19 | `Carrito vaciado tras checkout` | `cart.items.count() == 0` |

---

### Sección 3 — Checkout Tarjeta + Envio Express (8 checks)

**Accion:** Comprador agrega `producto_b` x1 al carrito. Confirma con `payment_method=card`, `delivery_type=express`.

| # | Check | Valor esperado |
|---|-------|---------------|
| 20 | `Checkout tarjeta+express devuelve 201` | HTTP 201 |
| 21 | `checkout_payment_method = "card"` | `order.checkout_payment_method == 'card'` |
| 22 | `shipping_cost = 4000 (express)` | `order.shipping_cost == '4000.00'` |
| 23 | `total incluye costo express` | `total == producto_b.price * 1 + 4000` |
| 24 | `Invariante total con express` | `total == subtotal - 0 + 4000` |
| 25 | `Ciudad "25 de Mayo" guardada correctamente` | `shipping_data['city'] == '25 de Mayo'` (ciudad con número) |
| 26 | `Stock de producto B descontado en 1` | `producto_b.stock == stock_b_inicial - 1` |
| 27 | `Stock de producto A no fue afectado por segundo pedido` | `producto_a.stock == stock_a_inicial - 2` (sin cambio) |

---

### Sección 4 — Validaciones que Deben Rechazar el Checkout (4 checks)

| # | Check | Descripcion |
|---|-------|-------------|
| 28 | `Carrito vacio -> 400` | POST a `/orders/` con carrito vacío → 400 |
| 29 | `Datos de envio invalidos -> 400` | POST con `name='X'` → 400 |
| 30 | `payment_method invalido -> 400` | `payment_method='bitcoin'` → 400 |
| 31 | `delivery_type invalido -> 400` | `delivery_type='teleportacion'` → 400 |

---

### Sección 5 — Modulo Ventas (usuario interno) (13 checks)

**Actor:** `gestor` (perfil `Gestionsar ventas`, permiso `pedidosventas.ver [todos]`)

| # | Check | Descripcion |
|---|-------|-------------|
| 32 | `Gestor puede listar ventas (200)` | GET `?view=ventas` → 200 |
| 33 | `Pedido efectivo aparece en listado de ventas` | `order_efectivo.id` en la lista |
| 34 | `Pedido tarjeta+express aparece en listado de ventas` | `order_tarjeta.id` en la lista |
| 35 | `checkout_payment_method visible en ventas` | `'checkout_payment_method'` en los keys del response |
| 36 | `shipping_cost visible en ventas` | `'shipping_cost'` en los keys del response |
| 37 | `checkout_payment_method = "cash" en ventas` | El pedido efectivo muestra `checkout_payment_method='cash'` |
| 38 | `shipping_cost = "0.00" en ventas` | El pedido efectivo muestra `shipping_cost='0.00'` |
| 39 | `shipping_cost = "4000.00" en pedido express en ventas` | El pedido express muestra `shipping_cost='4000.00'` |
| 40 | `checkout_payment_method = "card" en pedido express` | El pedido express muestra `checkout_payment_method='card'` |
| 41 | `Gestor puede ver detalle de pedido (200)` | GET `?view=ventas` con pk → 200 |
| 42 | `Detalle incluye items` | `response.data['items']` no está vacío |
| 43 | `Detalle incluye shipping_data` | `response.data['shipping_data']` no es None |
| 44 | `Detalle shipping_data.name correcto` | `shipping_data['name'] == 'Maria Gonzalez'` |

---

### Sección 6 — Modulo Pedidos (comprador ve sus compras) (4 checks)

**Actor:** `comprador` (perfil `Comprar en la tienda`, permiso `pedidos.ver`)

| # | Check | Descripcion |
|---|-------|-------------|
| 45 | `Comprador puede listar sus pedidos (200)` | GET `?view=compras` → 200 |
| 46 | `Pedido efectivo en mis compras` | El pedido efectivo aparece |
| 47 | `Pedido tarjeta en mis compras` | El pedido express aparece |
| 48 | `Solo aparecen pedidos propios` | Todos los usernames en la lista son `integ_comprador` |

---

### Sección 7 — Cancelacion y Reposicion de Stock (4 checks)

**Accion:** Cancelar el pedido efectivo (producto A, qty=2).

| # | Check | Descripcion |
|---|-------|-------------|
| 49 | `Cancelacion devuelve 200` | POST `/{id}/cancel/` → 200 |
| 50 | `Estado cambia a "cancelled"` | `order.status == 'cancelled'` |
| 51 | `Stock de producto A repuesto tras Cancelacion` | `producto_a.stock == stock_a_inicial` |
| 52 | `Segunda Cancelacion -> 400` | Segunda llamada a `cancel` → 400 |

---

### Sección 8 — No Conflicto con Datos Existentes (4 checks)

| # | Check | Descripcion |
|---|-------|-------------|
| 53 | `Solo se crearon 2 pedidos nuevos` | `Order.objects.count() == pedidos_previos + 2` (ya que el cancelado sigue siendo 1) |
| 54 | `Stock de producto B sigue descontado` | El pedido express no se canceló; `producto_b.stock == stock_b_inicial - 1` |
| 55 | `Stock de producto A fue repuesto al nivel inicial` | `producto_a.stock == stock_a_inicial` |
| 56 | `Gestor en vista compras no ve pedidos del buyer` | El gestor no tiene permiso `pedidos.ver` para ver compras de otros |

---

## Invariantes verificadas

### Invariante de Total

```
order.total == subtotal - order.discount_amount + order.shipping_cost
```

Verificada en las secciones 2 y 3 para ambos métodos de pago.

### Invariante de Stock

```
# Al confirmar un pedido:
producto.stock_nuevo == producto.stock_anterior - cantidad_comprada

# Al cancelar un pedido:
producto.stock_nuevo == producto.stock_original
```

Verificada en las secciones 2, 3 y 7.

### Invariante de Aislamiento

```
# Un pedido no afecta el stock de productos no comprados
stock_de_producto_A_despues_de_comprar_B == stock_de_producto_A_antes
```

Verificada en la sección 3, check 27.

---

## Datos de prueba usados

```python
SHIPPING_VALIDO = {
    'name': 'Maria Gonzalez',
    'address': 'Av. San Martin 1450',
    'city': '9 de Julio',
    'zip': '6500',
    'phone': '1145678901',
}

# Para el segundo pedido (express):
SHIPPING_EXPRESS = {
    **SHIPPING_VALIDO,
    'city': '25 de Mayo',  # segunda ciudad con numero
}
```

**Por qué `city='9 de Julio'` y `city='25 de Mayo'`:**  
Son ciudades reales argentinas cuyo nombre contiene números. Verifican explícitamente
que el regex de validación de ciudad acepta `^[a-zA-Z0-9...]+$`.

**Por qué `phone='1145678901'`:**  
La librería `phonenumbers` tiene cobertura variable. El área 11 (CABA) es siempre
válido. Ver Regla R13 en `reglas_generales.md`.

---

## Salida esperada (resumen)

```
------------------------------------------------------------
  RESULTADO: 56 OK  |  0 FALLOS
------------------------------------------------------------
  Todos los checks pasaron.
  (Transaccion revertida - la DB quedo sin cambios)
```

---

## Cómo agregar nuevos checks

1. Agregar dentro del bloque `with transaction.atomic():` y dentro del
   bloque `with patch(...):`.
2. Usar la función `ok(nombre, condicion, detalle_opcional)`.
3. Usar solo ASCII en los strings de `ok()` y en `print()` (ver Regla R12).
4. Si se necesita un nuevo producto, usar `Product.objects.filter(...)` de la DB real.
   No crear productos nuevos (dispara el RAG signal).
5. Actualizar el contador de checks en este documento.

```python
ok('Nombre del check', expresion_booleana, f'detalle={valor}')
```
