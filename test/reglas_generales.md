# Reglas Generales de Testing — Craftiar Backend

Estas reglas son **obligatorias** para todos los tests del proyecto. Se establecieron
a partir de errores reales ocurridos durante el desarrollo; cada regla tiene
su razón de ser documentada.

---

## R1 — Siempre usar PostgreSQL, nunca SQLite

**Regla:** Los tests deben correr contra PostgreSQL en Docker. Está prohibido usar
`--settings` alternativos con SQLite o agregar `DATABASES` en el código de test.

**Por qué:** El proyecto usa `pgvector` (extensión nativa de Postgres) para embeddings
de productos. SQLite no soporta extensiones y provoca errores silenciosos donde los tests
pasan localmente pero fallan en producción con comportamientos distintos (tipos de datos,
JSON, operadores).

**Cómo aplicarlo:**

```powershell
$env:DATABASE_URL="postgresql://craftiar_user:craftiar_password@localhost:5432/craftiar_db"
.\venv\Scripts\python.exe manage.py test <app> --keepdb -v 2
```

---

## R2 — Usar `--keepdb` para conservar la extensión pgvector

**Regla:** Siempre pasar `--keepdb` al correr tests con `manage.py`.

**Por qué:** La DB de test `test_craftiar_db` necesita la extensión `vector` instalada
manualmente una sola vez. Sin `--keepdb`, Django destruye y recrea la DB en cada ejecución,
lo que elimina la extensión y rompe todos los tests.

**Excepción:** Si hay cambios de migración irreversibles, recrear la DB, instalar la
extensión de nuevo, y luego volver a `--keepdb`.

```powershell
# Recrear DB desde cero (solo cuando sea necesario)
.\venv\Scripts\python.exe manage.py test orders  # sin --keepdb, una vez
docker exec craftiar_db psql -U craftiar_user -d test_craftiar_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
# Desde ahora:
.\venv\Scripts\python.exe manage.py test orders --keepdb -v 2
```

---

## R3 — Mockear el RAG (señal de embeddings) en tests con Products

**Regla:** Todo test que cree instancias de `Product` debe mockear la señal de generación
de embeddings antes de hacerlo.

**Por qué:** `catalog/signals.py` define un `post_save` en `Product` que dispara
`catalog.tasks.generate_product_embedding.delay`. Esta tarea llama a la API de Gemini.
Dentro de una transacción atómica de test, si la llamada falla, corrompe la transacción
y ningún test posterior funciona correctamente.

**Cómo aplicarlo:**

```python
# En setUp() de la clase de test:
def setUp(self):
    _mock_rag(self)  # siempre antes de crear Products

def _mock_rag(test_instance):
    p = patch('catalog.tasks.generate_product_embedding.delay')
    p.start()
    test_instance.addCleanup(p.stop)
```

El helper `_mock_rag` está definido en `orders/tests.py`. Copiar el patrón si se crea
un nuevo archivo de tests.

---

## R4 — Mockear MercadoPago con función context manager, no con decorador de clase

**Regla:** No usar `@patch('mercadopago.SDK', mock)` a nivel de clase. Usar un helper
que retorne el `patch` como context manager y aplicarlo con `with` dentro de cada test.

**Por qué:** El decorador de clase evalúa el argumento `mock` una sola vez al momento
de definir la clase. Si el mock es mutable (MagicMock), los tests comparten estado y
los efectos secundarios de un test se filtran al siguiente.

**Patrón correcto:**

```python
FAKE_PREF_RESPONSE = {
    "status": 201,
    "response": {
        "id": "PREF_123",
        "init_point": "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=PREF_123",
        "sandbox_init_point": "https://sandbox.mercadopago.com.ar/checkout/v1/redirect?pref_id=PREF_123",
    }
}

def _patch_mp(pref_response=None):
    resp = pref_response or FAKE_PREF_RESPONSE
    mock_sdk = MagicMock()
    mock_sdk.return_value.preference.return_value.create.return_value = resp
    return patch('mercadopago.SDK', mock_sdk)

# En el test:
def test_algo(self):
    with _patch_mp():
        # hacer el request
```

---

## R5 — Mockear emails en tests de integración

**Regla:** Los tests que ejercen flujos de checkout o cambio de estado deben mockear
las tareas Celery de email.

**Por qué:** Si `CELERY_TASK_ALWAYS_EAGER=true`, las tareas corren de forma síncrona.
Sin mockear, intentan enviar emails reales, lo que falla en tests (sin SMTP configurado)
y genera efectos secundarios no deseados.

**Cómo aplicarlo:**

```python
with patch('orders.tasks.send_order_confirmation_email.delay'), \
     patch('orders.tasks.send_order_status_change_email.delay'):
    # flujo de test
```

O usando `@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')`.

---

## R6 — Los tests de integración deben usar rollback explícito

**Regla:** El test de integración `test_compra_integracion.py` debe ejecutarse dentro
de un `transaction.atomic()` que se revierta al final. No puede dejar datos en la DB.

**Por qué:** El test usa la DB real de desarrollo. Si persiste datos (usuarios, pedidos,
stock descontado), contamina la DB y afecta el comportamiento de la aplicación.

**Cómo verificarlo:** Al terminar el script, la salida debe decir:
```
(Transaccion revertida — la DB quedo sin cambios)
```

**Patrón:**

```python
try:
    with transaction.atomic():
        # ... todos los asserts ...
        raise Rollback("revertir")
except Rollback:
    print("(Transaccion revertida)")
```

---

## R7 — Helpers de creación de datos deben ser autocontenidos

**Regla:** Los helpers como `make_product()`, `make_superuser()`, `add_to_cart()`
deben incluir todos los campos NOT NULL del modelo y no depender de estado previo.

**Por qué:** Si el modelo tiene un campo obligatorio que el helper omite, el test falla
con un error de DB confuso en lugar de un error claro del test. Ejemplo real: `weight_kg`
en `Product` es NOT NULL y estaba ausente del helper original.

**Campos obligatorios actuales de Product:**

```python
Product.objects.create(
    name=name,
    sku=f'SKU-{uuid.uuid4().hex[:8].upper()}',  # único por test
    description='...',
    price=Decimal('1000'),
    stock=20,
    weight_kg=Decimal('0.5'),   # NOT NULL — no olvidar
    is_active=True,
    category=category,          # FK NOT NULL
)
```

---

## R8 — Usar `select_for_update()` en tests de concurrencia

**Regla:** Si se testea la atomicidad del descuento de stock, la validación de stock
en el serializer usa `select_for_update()`. Los tests deben correr dentro de `TestCase`
(que envuelve cada test en una transacción) para que el lock funcione correctamente.

**Por qué:** `TransactionTestCase` no envuelve en transacción, por lo que los locks
pueden quedar colgados. `TestCase` revierte todo al terminar el test.

---

## R9 — No omitir tests cuando un mock falla; arreglarlo

**Regla:** Prohibido usar `@skip`, `@expectedFailure` o `unittest.skip()` por
problemas de mocking. Si un test necesita un mock que no funciona, arreglar el mock.

**Por qué:** Los skips se acumulan y nunca se eliminan, formando deuda técnica
invisible. El problema de mock siempre tiene solución (ver R3, R4).

---

## R10 — Tests unitarios sin DB siempre en clases separadas

**Regla:** Los tests que no necesitan DB (como `TestNormalizarCelularAr`) deben estar
en su propia clase `TestCase`. No mezclar tests unitarios puros con tests que acceden
a DB en la misma clase.

**Por qué:** Django TestCase ejecuta `setUp`/`tearDown` de DB para cada test aunque
no sea necesario, ralentizando los tests unitarios. Mantenerlos separados también
hace más claro qué se testea.

---

## R11 — Perfiles de usuario: usar los de la DB real, no crear nuevos

**Regla:** En el test de integración `test_compra_integracion.py`, los perfiles se
obtienen con `Profile.objects.get(name='...')` de la DB real. No crear perfiles nuevos.

**Por qué:** Los perfiles tienen permisos atómicos pre-configurados. Crear perfiles
nuevos sin permisos haría que los tests pasen pero no verificarían el comportamiento
real de la app.

**Perfiles relevantes:**

| Perfil | Permisos clave |
|--------|---------------|
| `Comprar en la tienda` | `carrito.checkout`, `pedidos.ver`, `carrito.gestionar` |
| `Gestionsar ventas` | `pedidosventas.ver [todos]`, `pedidosventas.cambiar_estado [todos]` |

---

## R12 — Codificación de salida: solo ASCII en prints de tests

**Regla:** Los archivos de test que imprimen a consola deben usar solo caracteres ASCII.
Prohibido usar: `→`, `←`, `✓`, `✗`, `─`, `═`, acentos en strings literales de `print()`.

**Por qué:** La consola de Windows usa cp1252 por defecto. Los caracteres fuera del
rango cp1252 lanzan `UnicodeEncodeError` y el test termina con traceback aunque haya
pasado correctamente.

**Tabla de reemplazos:**

| Unicode | Reemplazo ASCII |
|---------|----------------|
| `→` | `->` |
| `←` | `<-` |
| `✓` | `OK` |
| `✗` | `X` o `FALLO` |
| `─`, `═` | `-`, `=` |
| acentos en print() | sin acento |

**Nota:** Los strings en `assertEqual`, `assertIn`, etc. pueden tener acentos porque
no se imprimen directamente.

---

## R13 — Teléfonos de prueba: usar números del área de CABA

**Regla:** Para datos de prueba de teléfonos argentinos, usar prefijo `11` (CABA).

**Por qué:** La librería `phonenumbers` de Google tiene cobertura variable por área.
El prefijo `2317` (9 de Julio, Buenos Aires) produce `None` con `phonenumbers` aunque
sea un número real válido. CABA (prefijo `11`) es siempre reconocido.

**Número de prueba estándar:** `1145678901` (10 dígitos, CABA, válido).

**Formatos equivalentes aceptados:**
- `1145678901`
- `+54 9 11 4567 8901`
- `011 15 4567-8901`
- `11 4567 8901`

---

## Resumen de mocks obligatorios por contexto

| Contexto | Mocks requeridos |
|----------|-----------------|
| Test que crea `Product` | `catalog.tasks.generate_product_embedding.delay` |
| Test de checkout MP | `mercadopago.SDK` vía `_patch_mp()` |
| Test de checkout efectivo/tarjeta | `orders.tasks.send_order_confirmation_email.delay` |
| Test de cambio de estado | `orders.tasks.send_order_status_change_email.delay` |
| Test de integración completo | Los 3 anteriores |
