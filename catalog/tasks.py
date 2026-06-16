import os
import logging
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from .models import Product

logger = logging.getLogger('catalog.audit')

@shared_task(bind=True, max_retries=3)
def generate_product_embedding(self, product_id):
    """
    Tarea asíncrona de Celery para generar y guardar el embedding vectorial
    del nombre y descripción de un producto usando Google Gemini API.
    Produce vectores de 768 dimensiones.
    """
    try:
        product = Product.objects.get(id=product_id)

        # El texto que vamos a vectorizar (nombre + descripción corta)
        text_to_embed = f"Producto: {product.name}. Descripción: {product.description}"

        import google.generativeai as genai

        # Obtener API KEY (preferir settings, luego .env)
        api_key = getattr(settings, "GOOGLE_API_KEY", "")
        if not api_key:
            api_key = os.environ.get("GOOGLE_API_KEY", "")

        if not api_key:
            logger.error("RAG Error: GOOGLE_API_KEY no configurada. No se puede generar vector.")
            Product.objects.filter(id=product.id).update(embedding_error="GOOGLE_API_KEY no configurada")
            return False

        genai.configure(api_key=api_key)

        # Llamar a Gemini gemini-embedding-2 (output_dimensionality=768 para que coincida
        # con la columna vector(768); sin este parámetro el modelo devuelve 3072 dimensiones).
        result = genai.embed_content(
            model="models/gemini-embedding-2",
            content=text_to_embed,
            task_type="retrieval_document",
            output_dimensionality=768
        )

        if result and 'embedding' in result:
            vector = result['embedding']
            # Actualizamos el producto (usamos update() para no disparar el signal de nuevo)
            Product.objects.filter(id=product.id).update(
                embedding=vector, embedding_updated_at=timezone.now(), embedding_error="",
            )
            logger.info(f"RAG: Embedding vectorial (Gemini 768d) generado exitosamente para el producto {product.sku}")
            return True
        else:
            logger.error(f"RAG Error: Fallo en API Gemini al generar embedding.")
            Product.objects.filter(id=product.id).update(embedding_error="Respuesta vacía de la API de Gemini")
            raise self.retry(countdown=60)

    except Product.DoesNotExist:
        logger.warning(f"RAG: Producto {product_id} no encontrado. Tarea abortada.")
    except Exception as e:
        logger.error(f"RAG Exception: Error generando vector para producto {product_id} - {str(e)}")
        Product.objects.filter(id=product_id).update(embedding_error=str(e)[:255])
        # Reintentar en caso de error de red
        raise self.retry(exc=e, countdown=60)


def regenerate_missing_embeddings(limit=None):
    """
    Genera (de forma síncrona) el embedding de los productos que todavía no
    tienen uno. Usado tanto por el panel de administración como por el
    management command `regenerate_embeddings`.
    Devuelve una lista de resultados: [{"id", "sku", "success"}, ...]
    """
    qs = Product.objects.filter(embedding__isnull=True).order_by('id')
    if limit:
        qs = qs[:limit]
    results = []
    for product in qs:
        try:
            ok = bool(generate_product_embedding(product.id))
        except Exception as e:
            logger.error(f"RAG: fallo no controlado generando embedding de {product.sku} - {e}")
            ok = False
        results.append({"id": product.id, "sku": product.sku, "success": ok})
    return results
