from celery import shared_task
from django.core.mail import send_mail
from django.utils import timezone
from django.contrib.auth.models import User
from .models import Order, Subscription
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_order_confirmation_email(self, order_id):
    """
    RF 7.2: Envío de email de confirmación de pedido de forma asíncrona con reintentos.
    """
    try:
        order = Order.objects.get(id=order_id)

        items_lines = "\n".join(
            f"  - {item.quantity}x {item.product.name} (SKU: {item.product.sku}) a ${item.price_at_purchase} c/u = ${item.price_at_purchase * item.quantity}"
            for item in order.items.select_related('product')
        )

        discount_line = ""
        if order.discount_amount and order.discount_amount > 0:
            coupon_code = order.coupon.code if order.coupon else ""
            discount_line = f"\nDescuento aplicado ({coupon_code}): -${order.discount_amount}"

        send_mail(
            subject=f'Confirmación de Pedido #{order.id}',
            message=(
                f'Hola {order.user.username},\n'
                f'Hemos recibido tu pedido #{order.id}. Estado: Pendiente de Pago.\n\n'
                f'Detalle:\n{items_lines}'
                f'{discount_line}\n'
                f'Total: ${order.total}\n\n'
                f'Gracias por comprar en la Tienda de Materiales.'
            ),
            from_email='orders@construccion.com',
            recipient_list=[order.user.email],
            fail_silently=False,
        )
        logger.info(f"Email de confirmación de pedido #{order.id} enviado con éxito.")
    except Exception as exc:
        logger.warning(f"Error al enviar confirmación de pedido {order_id}. Reintentando en 60 segundos...")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_order_status_change_email(self, order_id, old_status, new_status):
    """
    RF 7.3: Notifica al cliente por email del cambio de estado del pedido.
    """
    try:
        order = Order.objects.get(id=order_id)
        # Traducir códigos a etiquetas legibles para el cliente.
        labels = dict(Order.STATUS_CHOICES)
        old_label = labels.get(old_status, old_status)
        new_label = labels.get(new_status, new_status)
        send_mail(
            subject=f'Actualización de tu Pedido #{order.id}',
            message=f'Hola {order.user.username},\nTu pedido #{order.id} ha cambiado de estado de "{old_label}" a "{new_label}".',
            from_email='orders@construccion.com',
            recipient_list=[order.user.email],
            fail_silently=False,
        )
        logger.info(f"Notificación de cambio de estado de pedido #{order.id} enviada.")
    except Exception as exc:
        logger.warning(f"Error al enviar notificación de cambio de estado para pedido {order_id}. Reintentando...")
        raise self.retry(exc=exc)


@shared_task
def notify_expiring_subscriptions():
    """
    RF 7.5: Envía una notificación por email al usuario 3 días antes del vencimiento
    de su suscripción Premium. Ejecutada como tarea programada diaria.
    """
    three_days_from_now = timezone.now() + timezone.timedelta(days=3)
    start_of_day = three_days_from_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = three_days_from_now.replace(hour=23, minute=59, second=59, microsecond=999999)

    # Buscamos suscripciones premium que vencen en exactamente 3 días
    expiring_subs = Subscription.objects.filter(
        plan='premium',
        status='active',
        end_date__range=(start_of_day, end_of_day)
    )

    count = 0
    for sub in expiring_subs:
        send_mail(
            subject='Tu suscripción Premium vencerá pronto',
            message=f'Hola {sub.user.username},\nTe recordamos que tu suscripción al Plan Premium de Materiales de Construcción vencerá en 3 días, el {sub.end_date.strftime("%d/%m/%Y")}.\nRenueva hoy para no perder el acceso a tu Tutor Visual IA.',
            from_email='billing@construccion.com',
            recipient_list=[sub.user.email],
            fail_silently=False,
        )
        count += 1
        logger.info(f"Notificación de vencimiento de suscripción enviada a {sub.user.username}")

    return f"Se enviaron {count} notificaciones de vencimiento de suscripción."


@shared_task
def process_subscription_renewals():
    """
    Tarea diaria: renueva (cobro simulado) las suscripciones vencidas con
    auto-renovación y expira+revoca las canceladas o sin auto-renovación.
    """
    from .subscriptions import process_renewals_and_expirations
    renewed, expired = process_renewals_and_expirations()
    logger.info(f"Suscripciones: {renewed} renovadas, {expired} expiradas.")
    return f"renovadas={renewed} expiradas={expired}"
