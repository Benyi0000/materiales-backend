from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import Profile, UserProfileAssignment, PermissionAuditLog
from orders.models import Subscription
from .tasks import send_welcome_email

@receiver(post_save, sender=User)
def handle_user_registration(sender, instance, created, **kwargs):
    """
    RF 1.7: Asigna automáticamente el perfil "Comprar en la tienda" al registrarse.
    RF 7.1: Dispara el envío asíncrono del email de bienvenida.
    """
    if created:
        try:
            base_profile = Profile.objects.get(name="Comprar en la tienda")
            UserProfileAssignment.objects.create(
                user=instance,
                profile=base_profile,
                assigned_by=None
            )
            # Log de auditoría
            PermissionAuditLog.objects.create(
                user=instance,
                profile=base_profile,
                action='assign',
                notes="Perfil base asignado automáticamente al registrarse"
            )
        except Profile.DoesNotExist:
            pass
        
        # Enviar email de bienvenida de forma asíncrona mediante Celery (Solo para registros públicos inactivos)
        if not instance.is_active:
            send_welcome_email.delay(instance.id)


@receiver(post_save, sender=Subscription)
def handle_subscription_change(sender, instance, **kwargs):
    """
    RF 1.5 y RF 4.4: Habilitación o revocación automática del perfil "Tutor Visual IA"
    basado en el estado de la suscripción premium.
    """
    try:
        premium_profile = Profile.objects.get(name="Tutor Visual IA")
    except Profile.DoesNotExist:
        return

    if instance.plan == 'premium' and instance.status == 'active':
        # Asignar perfil premium
        assignment, created = UserProfileAssignment.objects.get_or_create(
            user=instance.user,
            profile=premium_profile,
            defaults={'assigned_by': None}
        )
        if not assignment.is_active:
            assignment.is_active = True
            assignment.save()
            # Auditoría
            PermissionAuditLog.objects.create(
                user=instance.user,
                profile=premium_profile,
                action='subs_activate',
                notes="Perfil premium asignado automáticamente por Suscripción Premium activa"
            )
    else:
        # Revocar perfil si el plan no es premium o no está activo
        assignments = UserProfileAssignment.objects.filter(
            user=instance.user,
            profile=premium_profile,
            is_active=True
        )
        for assignment in assignments:
            assignment.is_active = False
            assignment.save()
            # Auditoría
            PermissionAuditLog.objects.create(
                user=instance.user,
                profile=premium_profile,
                action='subs_deactivate',
                notes="Perfil premium revocado automáticamente por vencimiento/cancelación de Suscripción"
            )
