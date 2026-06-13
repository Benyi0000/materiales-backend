import os
import logging
from celery import shared_task
from django.conf import settings
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
            return False

        genai.configure(api_key=api_key)
        
        # Llamar a Gemini gemini-embedding-2
        result = genai.embed_content(
            model="models/gemini-embedding-2",
            content=text_to_embed,
            task_type="retrieval_document"
        )
        
        if result and 'embedding' in result:
            vector = result['embedding']
            # Actualizamos el producto (usamos update() para no disparar el signal de nuevo)
            Product.objects.filter(id=product.id).update(embedding=vector)
            logger.info(f"RAG: Embedding vectorial (Gemini 768d) generado exitosamente para el producto {product.sku}")
            return True
        else:
            logger.error(f"RAG Error: Fallo en API Gemini al generar embedding.")
            raise self.retry(countdown=60)
            
    except Product.DoesNotExist:
        logger.warning(f"RAG: Producto {product_id} no encontrado. Tarea abortada.")
    except Exception as e:
        logger.error(f"RAG Exception: Error generando vector para producto {product_id} - {str(e)}")
        # Reintentar en caso de error de red
        raise self.retry(exc=e, countdown=60)
