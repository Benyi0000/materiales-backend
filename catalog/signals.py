from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Product
from .tasks import generate_product_embedding

@receiver(post_save, sender=Product)
def trigger_embedding_generation(sender, instance, created, update_fields, **kwargs):
    """
    Señal que se dispara cada vez que se guarda un Producto.
    Si se crea uno nuevo, o si se modifica su nombre/descripción,
    encolamos la tarea de Celery para re-calcular su vector (embedding).
    """
    # Si es nuevo, o si no hemos especificado campos (se actualizó todo)
    # o si se actualizó explícitamente el nombre o la descripción.
    needs_update = False
    
    if created:
        needs_update = True
    elif update_fields is None:
        # update() no fue usado con update_fields específicos, asumiendo cambio completo
        needs_update = True
    elif 'name' in update_fields or 'description' in update_fields:
        needs_update = True
        
    # Excluimos la actualización recursiva (cuando Celery actualiza el 'embedding')
    # ya que en tasks.py usamos .update(embedding=vector) que no llama a .save(), 
    # pero por las dudas evitamos disparos innecesarios.
    if update_fields and 'embedding' in update_fields and len(update_fields) == 1:
        needs_update = False

    if needs_update:
        # Mandar tarea a segundo plano para no demorar la respuesta de la API al usuario
        generate_product_embedding.delay(instance.id)
