# Tests — Craftiar Backend

Esta carpeta contiene la especificación, las reglas y la guía de infraestructura
para todos los tests del backend de Craftiar.

---

## Contenido

| Archivo | Descripción |
|---------|-------------|
| `README.md` | Este archivo. Cómo ejecutar, infraestructura, requisitos. |
| `reglas_generales.md` | Reglas obligatorias para escribir tests en este proyecto. |
| `spec_orders_unitarios.md` | Especificación de los tests unitarios del módulo `orders`. |
| `spec_orders_integracion.md` | Especificación del test de integración end-to-end de compra. |

---

## Stack de testing

| Capa | Herramienta |
|------|-------------|
| Framework | Django TestCase (`django.test.TestCase`) |
| HTTP | `rest_framework.test.APIRequestFactory` + `force_authenticate` |
| Mocks | `unittest.mock.patch`, `MagicMock` |
| DB | PostgreSQL 16 en Docker (NUNCA SQLite) |
| Celery | `CELERY_TASK_ALWAYS_EAGER=true` en tests de integración |
| Email | `django.core.mail.backends.locmem.EmailBackend` |

---

## Infraestructura Docker

El proyecto requiere PostgreSQL corriendo en Docker. Dos bases de datos:

```
craftiar_db        — base de datos de desarrollo/staging
test_craftiar_db   — creada automáticamente por Django al correr tests
```

### Arrancar los contenedores

```powershell
docker-compose up -d
```

### Crear la extensión pgvector en la DB de test (solo la primera vez)

```powershell
docker exec craftiar_db psql -U craftiar_user -d test_craftiar_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Si la DB de test no existe aún, crear con `--keepdb` primero:

```powershell
$env:DATABASE_URL="postgresql://craftiar_user:craftiar_password@localhost:5432/craftiar_db"
.\venv\Scripts\python.exe manage.py test orders --keepdb -v 2
# Después de que falle por falta de extensión:
docker exec craftiar_db psql -U craftiar_user -d test_craftiar_db -c "CREATE EXTENSION IF NOT EXISTS vector;"
# Volver a correr
.\venv\Scripts\python.exe manage.py test orders --keepdb -v 2
```

---

## Comandos de ejecución

### Suite completa de `orders` (97 tests)

```powershell
$env:DATABASE_URL="postgresql://craftiar_user:craftiar_password@localhost:5432/craftiar_db"
.\venv\Scripts\python.exe manage.py test orders --keepdb -v 2
```

### Test de integración end-to-end (56 checks, no persiste datos)

```powershell
$env:DATABASE_URL="postgresql://craftiar_user:craftiar_password@localhost:5432/craftiar_db"
$env:DJANGO_SETTINGS_MODULE="config.settings"
$env:CELERY_TASK_ALWAYS_EAGER="true"
$env:EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
.\venv\Scripts\python.exe test_compra_integracion.py
```

### Solo una clase de test

```powershell
$env:DATABASE_URL="postgresql://craftiar_user:craftiar_password@localhost:5432/craftiar_db"
.\venv\Scripts\python.exe manage.py test orders.tests.TestNormalizarCelularAr --keepdb -v 2
```

### Solo un test puntual

```powershell
$env:DATABASE_URL="postgresql://craftiar_user:craftiar_password@localhost:5432/craftiar_db"
.\venv\Scripts\python.exe manage.py test orders.tests.TestNormalizarCelularAr.test_numero_extranjero --keepdb -v 2
```

---

## Variables de entorno requeridas para tests

| Variable | Valor para tests |
|----------|-----------------|
| `DATABASE_URL` | `postgresql://craftiar_user:craftiar_password@localhost:5432/craftiar_db` |
| `DJANGO_SETTINGS_MODULE` | `config.settings` |
| `CELERY_TASK_ALWAYS_EAGER` | `true` |
| `EMAIL_BACKEND` | `django.core.mail.backends.locmem.EmailBackend` |

`MP_ACCESS_TOKEN` y `GEMINI_API_KEY` deben estar en `.env` pero **no se usan** en tests
porque MercadoPago y el RAG siempre se mockean.

---

## Estado actual de la suite

| Suite | Tests | Estado |
|-------|-------|--------|
| `orders/tests.py` | 97 | Todos pasando |
| `test_compra_integracion.py` | 56 checks | Todos pasando |
| `catalog/tests.py` | — | No cubierto |
| `users/tests.py` | — | No cubierto |

---

## Archivos de test

```
materiales-backend/
├── orders/tests.py              — Suite Django (97 tests, 7 clases)
├── test_compra_integracion.py   — Script end-to-end (56 checks, rollback)
├── test_auth.py                 — Pruebas manuales de autenticación (legacy)
├── test_cart_flow.py            — Pruebas manuales de carrito (legacy)
└── test/                        — Esta carpeta (documentación de tests)
```
