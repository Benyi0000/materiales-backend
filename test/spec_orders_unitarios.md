# Especificación — Tests Unitarios del Módulo `orders`

**Archivo:** `orders/tests.py`  
**Comando:** `python manage.py test orders --keepdb -v 2`  
**Total de tests:** 97  
**Estado:** Todos pasando

---

## Estructura de clases

```
orders/tests.py
├── TestNormalizarCelularAr          (16 tests) — sin DB
├── TestValidateShippingFields       (22 tests) — sin DB
├── TestOrderModelFields             (6 tests)  — con DB
├── TestOrderCreateSerializerFields  (8 tests)  — con DB
├── TestMercadoPagoPreferenceView    (10 tests) — con DB + mock MP
├── TestMercadoPagoWebhookView       (14 tests) — con DB + mock MP
└── TestOrderSerializerFields        (21 tests) — con DB
```

---

## Clase 1: `TestNormalizarCelularAr` (sin DB)

Función bajo test: `orders/serializers.py:normalizar_celular_ar(raw: str) -> str | None`

**Propósito:** Verifica que la función normaliza números de celular argentinos a
exactamente 10 dígitos sin prefijos, o retorna `None` para entradas inválidas.

### Casos válidos

| Test | Entrada | Salida esperada |
|------|---------|-----------------|
| `test_diez_digitos_planos` | `'1145678901'` | `'1145678901'` |
| `test_con_espacios` | `'11 4567 8901'` | `'1145678901'` |
| `test_con_guiones` | `'11-4567-8901'` | `'1145678901'` |
| `test_con_cero_area` | `'01145678901'` | `'1145678901'` |
| `test_formato_internacional_54_9` | `'+54 9 11 4567 8901'` | `'1145678901'` |
| `test_formato_internacional_54_sin_9` | `'+54 11 4567 8901'` | 10 dígitos |
| `test_interior_sin_prefijo` | `'3704123456'` | `'3704123456'` |
| `test_interior_con_0` | `'03704123456'` | `'3704123456'` |
| `test_interior_con_54_9` | `'+54 9 370 4123456'` | `'3704123456'` |
| `test_interior_con_15` | `'011 15 4567-8901'` | 10 dígitos |
| `test_numero_con_espacios_extra` | `'  1145678901  '` | `'1145678901'` |

### Casos inválidos (debe retornar `None`)

| Test | Entrada | Razón |
|------|---------|-------|
| `test_vacio` | `''` | cadena vacía |
| `test_none` | `None` | sin valor |
| `test_muy_corto` | `'12345'` | menos de 10 dígitos |
| `test_muy_largo` | `'123456789012345'` | más de 11 dígitos |
| `test_letras` | `'abcdefghij'` | no numérico |
| `test_numero_extranjero` | `'+1 212 555 1234'` | country_code != 54 |
| `test_solo_ceros` | `'0000000000'` | número inválido |
| `test_numero_ficticio_invalido` | `'9999999999'` | número inválido |

**Invariante clave:** Siempre que la función retorna un valor, `len(resultado) == 10`.

---

## Clase 2: `TestValidateShippingFields` (sin DB)

Función bajo test: `orders/serializers.py:validate_shipping_fields(shipping: dict) -> dict`

**Propósito:** Verifica la validación de los campos de envío. La función lanza
`serializers.ValidationError({'shipping': {campo: mensaje}})` ante datos inválidos,
o retorna el dict limpio si todo es válido.

### Casos válidos

| Test | Descripción |
|------|-------------|
| `test_datos_completos_validos` | Todos los campos correctos |
| `test_zip_y_phone_opcionales` | zip y phone vacíos son aceptados |
| `test_ciudad_con_numeros` | `'9 de Julio'`, `'25 de Mayo'` son aceptados |
| `test_ciudad_con_acento` | `'Córdoba'` es aceptado |
| `test_nombre_con_acento_y_apostrofe` | `"María O'Brien"` es aceptado |
| `test_phone_formato_internacional` | `'+54 9 11 4567 8901'` retorna 10 dígitos |
| `test_strip_espacios_en_campos` | Espacios al inicio/fin son eliminados |

### Validación de nombre

| Test | Entrada inválida | Campo de error esperado |
|------|-----------------|------------------------|
| `test_nombre_obligatorio` | `''` | `name` |
| `test_nombre_muy_corto` | `'AB'` | `name` |
| `test_nombre_con_digitos` | `'Juan123'` | `name` |

### Validación de dirección

| Test | Entrada inválida | Campo de error esperado |
|------|-----------------|------------------------|
| `test_direccion_obligatoria` | `''` | `address` |
| `test_direccion_muy_corta` | `'Cll'` | `address` |

### Validación de ciudad

| Test | Entrada inválida | Campo de error esperado |
|------|-----------------|------------------------|
| `test_ciudad_obligatoria` | `''` | `city` |
| `test_ciudad_muy_corta` | `'X'` | `city` |
| `test_ciudad_con_caracteres_invalidos` | `'Ciudad@#$'` | `city` |

### Validación de código postal

| Test | Entrada inválida | Campo de error esperado |
|------|-----------------|------------------------|
| `test_zip_cinco_digitos_invalido` | `'12345'` | `zip` |
| `test_zip_letras_invalido` | `'ABCD'` | `zip` |
| `test_zip_tres_digitos_invalido` | `'123'` | `zip` |
| `test_zip_cuatro_digitos_valido` | `'1043'` | (válido) |

### Validación de teléfono

| Test | Entrada inválida | Campo de error esperado |
|------|-----------------|------------------------|
| `test_phone_invalido` | `'123'` | `phone` |
| `test_phone_letras_invalido` | `'abcdefghij'` | `phone` |
| `test_phone_extranjero_invalido` | `'+1 212 555 1234'` | `phone` |

### Errores múltiples

| Test | Descripción |
|------|-------------|
| `test_multiples_errores` | Todos los campos inválidos a la vez — todos los campos deben aparecer en el error |

---

## Clase 3: `TestOrderModelFields` (con DB)

Modelos bajo test: `orders/models.py:Order`

**Propósito:** Verificar que los campos `checkout_payment_method` y `shipping_cost`
existen en la tabla y se comportan según su definición.

### Tests

| Test | Descripción |
|------|-------------|
| `test_campos_existen_en_tabla` | `checkout_payment_method` y `shipping_cost` están en los campos del modelo |
| `test_checkout_payment_method_choices` | Los choices son exactamente `mercadopago`, `card`, `cash` |
| `test_checkout_payment_method_default_vacio` | El valor por defecto es `''` (blank=True) |
| `test_shipping_cost_default_cero` | El valor por defecto es `0` |
| `test_shipping_cost_mercadopago` | Se puede guardar `shipping_cost=4000` con `checkout_payment_method='mercadopago'` |
| `test_invariante_total` | `total == subtotal - discount_amount + shipping_cost` |

**Invariante central:**
```
order.total = subtotal - order.discount_amount + order.shipping_cost
```

---

## Clase 4: `TestOrderCreateSerializerFields` (con DB)

Serializer bajo test: `orders/serializers.py:OrderCreateSerializer`

**Propósito:** Verificar que el checkout por tarjeta/efectivo guarda correctamente
`checkout_payment_method` y `shipping_cost` en el `Order` resultante.

### Setup

```python
setUp():
    _mock_rag(self)
    user = make_superuser()
    product = make_product(price=1000, stock=10)
    cart = add_to_cart(user, product, quantity=2)
```

### Tests

| Test | Método de pago | Tipo entrega | Verificación |
|------|---------------|-------------|--------------|
| `test_cash_standard_guarda_metodo_pago` | `cash` | `standard` | `checkout_payment_method == 'cash'` |
| `test_card_standard_guarda_metodo_pago` | `card` | `standard` | `checkout_payment_method == 'card'` |
| `test_standard_shipping_cost_cero` | `cash` | `standard` | `shipping_cost == 0` |
| `test_express_shipping_cost_4000` | `card` | `express` | `shipping_cost == 4000` |
| `test_total_incluye_shipping` | `card` | `express` | `total == subtotal + 4000` |
| `test_total_sin_shipping` | `cash` | `standard` | `total == subtotal` |
| `test_stock_descontado_tras_checkout` | cualquiera | cualquiera | `product.stock` decrementó |
| `test_carrito_vaciado_tras_checkout` | cualquiera | cualquiera | `cart.items.count() == 0` |

---

## Clase 5: `TestMercadoPagoPreferenceView` (con DB + mock MP)

Vista bajo test: `orders/views.py:MercadoPagoPreferenceView`

**Propósito:** Verificar el flujo de creación de preferencia de MercadoPago.
El SDK de MP siempre se mockea usando `_patch_mp()`.

### Setup

```python
setUp():
    _mock_rag(self)
    user = make_superuser()
    product = make_product(price=500, stock=5)
    add_to_cart(user, product, quantity=1)
```

### Tests

| Test | Descripción |
|------|-------------|
| `test_crea_preferencia_exitosa` | POST con datos válidos → 201, response tiene `order_id`, `init_point` |
| `test_checkout_payment_method_es_mercadopago` | El `Order` creado tiene `checkout_payment_method == 'mercadopago'` |
| `test_shipping_cost_standard` | Con `delivery_type=standard` → `shipping_cost == 0` |
| `test_shipping_cost_express` | Con `delivery_type=express` → `shipping_cost == 4000` |
| `test_total_guardado_en_order` | `order.total` == precio del producto |
| `test_stock_descontado` | `product.stock` decrementó en la cantidad del carrito |
| `test_carrito_vaciado` | `cart.items.count() == 0` después del checkout |
| `test_carrito_vacio_400` | POST con carrito vacío → 400 |
| `test_shipping_invalido_400` | POST con datos de envío inválidos → 400 |
| `test_mp_falla_502` | Si MP retorna error → 502, stock repuesto, pedido cancelado |

### Mock de MP

```python
FAKE_PREF_RESPONSE = {
    "status": 201,
    "response": {
        "id": "PREF_123",
        "init_point": "https://www.mercadopago.com.ar/...",
        "sandbox_init_point": "https://sandbox.mercadopago.com.ar/...",
    }
}

def _patch_mp(pref_response=None):
    resp = pref_response or FAKE_PREF_RESPONSE
    mock_sdk = MagicMock()
    mock_sdk.return_value.preference.return_value.create.return_value = resp
    return patch('mercadopago.SDK', mock_sdk)
```

---

## Clase 6: `TestMercadoPagoWebhookView` (con DB + mock MP)

Vista bajo test: `orders/views.py:MercadoPagoWebhookView`

**Propósito:** Verificar que el webhook de MP actualiza correctamente el estado
del pedido según el estado del pago recibido.

### Setup

```python
setUp():
    _mock_rag(self)
    user = make_superuser()
    product = make_product(price=1000, stock=10)
    # Crear Order pre-existente en estado pending_payment
    order = Order.objects.create(
        user=user, total=1000, status='pending_payment',
        checkout_payment_method='mercadopago', shipping_cost=0
    )
    OrderItem.objects.create(order=order, product=product, quantity=1, price_at_purchase=1000)
```

### Flujo `approved`

| Test | Descripción |
|------|-------------|
| `test_webhook_aprobado_cambia_estado` | Pago approved → `order.status == 'pending'` |
| `test_webhook_aprobado_guarda_payment_id` | `order.mp_payment_id` se guarda |
| `test_webhook_aprobado_guarda_snapshot` | `order.mp_payment_data` tiene `payment_id`, `status`, `net_received_amount` |
| `test_webhook_aprobado_guarda_mp_paid_at` | `order.mp_paid_at` se setea |
| `test_webhook_aprobado_net_received_amount` | `mp_payment_data['net_received_amount']` está presente |
| `test_webhook_aprobado_fee_details` | `mp_payment_data['fee_details']` está presente |

### Flujo `rejected`/`cancelled`

| Test | Descripción |
|------|-------------|
| `test_webhook_rechazado_cancela` | Pago rejected → `order.status == 'cancelled'` |
| `test_webhook_rechazado_repone_stock` | Stock del producto vuelve al valor original |
| `test_webhook_cancelado_cancela` | Pago cancelled → `order.status == 'cancelled'` |

### Casos edge

| Test | Descripción |
|------|-------------|
| `test_webhook_topic_ignorado` | Topic `shipment` → 200 con `{"status": "ignored"}` |
| `test_webhook_sin_id` | Sin `data.id` → 200 con `{"status": "ignored"}` |
| `test_webhook_order_inexistente` | `external_reference` inválido → 404 |
| `test_webhook_aprobado_idempotente` | Segundo webhook approved sobre `order.status='pending'` → no cambia |
| `test_webhook_merchant_order` | Topic `merchant_order` con pago aprobado → estado pending |

---

## Clase 7: `TestOrderSerializerFields` (con DB)

Serializer bajo test: `orders/serializers.py:OrderSerializer`

**Propósito:** Verificar que `OrderSerializer` expone correctamente los nuevos campos
y que el módulo de ventas muestra los datos esperados.

### Campos nuevos

| Test | Descripción |
|------|-------------|
| `test_campos_nuevos_en_serializer` | `checkout_payment_method` y `shipping_cost` están en `fields` |
| `test_checkout_payment_method_cash` | Serializa correctamente `'cash'` |
| `test_checkout_payment_method_card` | Serializa correctamente `'card'` |
| `test_checkout_payment_method_mercadopago` | Serializa correctamente `'mercadopago'` |
| `test_shipping_cost_cero` | Serializa `0.00` |
| `test_shipping_cost_4000` | Serializa `4000.00` |

### Módulo de ventas (vista `?view=ventas`)

| Test | Descripción |
|------|-------------|
| `test_ventas_incluye_checkout_payment_method` | `checkout_payment_method` visible en la lista de ventas |
| `test_ventas_incluye_shipping_cost` | `shipping_cost` visible en la lista de ventas |
| `test_ventas_filtro_estado` | `?status=pending` filtra correctamente |
| `test_ventas_filtro_fecha_desde` | `?date_from=` filtra por fecha |
| `test_ventas_filtro_fecha_hasta` | `?date_to=` filtra por fecha |

### Módulo de compras (vista `?view=compras`, por defecto)

| Test | Descripción |
|------|-------------|
| `test_compras_solo_propias` | El comprador solo ve sus propios pedidos |
| `test_compras_no_ve_pedidos_ajenos` | Un comprador no ve los pedidos de otro |

### Cancelación

| Test | Descripción |
|------|-------------|
| `test_cancelacion_cambia_estado` | `POST /orders/{id}/cancel/` → `status == 'cancelled'` |
| `test_cancelacion_repone_stock` | El stock del producto vuelve al nivel original |
| `test_cancelacion_doble_400` | Segunda cancelación del mismo pedido → 400 |
| `test_cancelacion_solo_pendiente` | Cancelar pedido en `preparing` → 400 |
| `test_cancelacion_solo_propia` | Un usuario no puede cancelar el pedido de otro → 403 |

---

## Helpers compartidos

Definidos al inicio de `orders/tests.py`:

```python
def _mock_rag(test_instance):
    """Desactiva el signal de embeddings. Llamar en setUp() de toda clase que cree Products."""
    p = patch('catalog.tasks.generate_product_embedding.delay')
    p.start()
    test_instance.addCleanup(p.stop)

def _patch_mp(pref_response=None):
    """Retorna patch de mercadopago.SDK como context manager. Usar con `with`."""
    ...

def make_superuser(username='admin_test'):
    """Crea un superuser con todos los permisos."""
    ...

def make_product(name='Producto Test', price=1000, stock=20, category=None):
    """Crea un Product con todos los campos obligatorios. SKU único por UUID."""
    ...

def add_to_cart(user, product, quantity=2):
    """Agrega el producto al carrito del usuario y retorna el Cart."""
    ...
```

---

## Cobertura por componente

| Componente | Cobertura |
|------------|-----------|
| `normalizar_celular_ar` | 11 casos validos + 8 invalidos |
| `validate_shipping_fields` | 7 casos validos + 15 invalidos + 1 multiple |
| `Order` (nuevos campos) | campos, defaults, choices, invariante |
| `OrderCreateSerializer` | cash/card, standard/express, stock, carrito |
| `MercadoPagoPreferenceView` | flujo OK, carrito vacio, shipping invalido, MP falla |
| `MercadoPagoWebhookView` | approved, rejected, cancelled, edge cases |
| `OrderSerializer` | campos nuevos, ventas, compras, cancelacion |
