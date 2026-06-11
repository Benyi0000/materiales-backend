from celery import shared_task
from django.core.mail import send_mail
from django.utils import timezone
from django.contrib.auth.models import User
from .models import UserProfileAssignment, PermissionAuditLog
import os
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_welcome_email(self, user_id):
    """
    Tarea para enviar email de bienvenida de forma asíncrona con reintentos.
    """
    try:
        user = User.objects.get(id=user_id)
        send_mail(
            subject='¡Bienvenido a Tienda de Materiales!',
            message=f'Hola {user.username}, gracias por registrarte en nuestra plataforma de materiales de construcción.',
            from_email='no-reply@construccion.com',
            recipient_list=[user.email],
            fail_silently=False,
        )
        logger.info(f"Email de bienvenida enviado con éxito al usuario {user.username}")
    except Exception as exc:
        logger.warning(f"Error al enviar email a usuario {user_id}. Reintentando en 60 segundos...")
        raise self.retry(exc=exc)


@shared_task
def revoke_expired_profiles():
    """
    Tarea periódica diaria que verifica perfiles con fecha de vencimiento y los revoque automáticamente.
    """
    now = timezone.now()
    expired_assignments = UserProfileAssignment.objects.filter(
        is_active=True,
        expires_at__lte=now
    )
    
    count = 0
    for assignment in expired_assignments:
        assignment.is_active = False
        assignment.save()
        
        # Registrar en el Log de Auditoría (RF 6.3)
        PermissionAuditLog.objects.create(
            user=assignment.user,
            profile=assignment.profile,
            action='auto_expire',
            performed_by=None,  # Acción automática del sistema
            notes=f"Expiración automática de perfil alcanzada el {assignment.expires_at}"
        )
        count += 1
        logger.info(f"Perfil {assignment.profile.name} revocado automáticamente por vencimiento para el usuario {assignment.user.username}")
    
    return f"Se revocaron {count} perfiles vencidos."


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_password_reset_email(self, user_id, token, uidb64):
    """
    RN_LOGIN_04: Envía de forma asíncrona por Celery el email con el enlace de recuperación de contraseña.
    """
    try:
        user = User.objects.get(id=user_id)
        # URL de recuperación apuntando al frontend (Next.js)
        frontend_url = os.environ.get('FRONTEND_URL', 'http://localhost:3000')
        reset_link = f"{frontend_url}/auth/password-reset-confirm?token={token}&uid={uidb64}"
        
        send_mail(
            subject='Restablecer tu Contraseña - Materiales Inteligentes',
            message=f'Hola {user.username},\nHemos recibido una solicitud para restablecer tu contraseña. Haz clic en el siguiente enlace para definir tu nueva contraseña. Este enlace expira en 15 minutos:\n\n{reset_link}\n\nSi no realizaste esta solicitud, puedes ignorar este correo.',
            from_email='security@construccion.com',
            recipient_list=[user.email],
            fail_silently=False,
        )
        logger.info(f"Email de recuperación de contraseña enviado con éxito al usuario {user.username}")
    except Exception as exc:
        logger.warning(f"Error al enviar email de recuperación a usuario {user_id}. Reintentando...")
        raise self.retry(exc=exc)

@shared_task
def send_verification_email(user_id, token, uid):
    from django.contrib.auth.models import User
    from django.core.mail import send_mail
    from django.conf import settings
    try:
        user = User.objects.get(id=user_id)
        verification_url = f'http://localhost:3000/auth/verify?uid={uid}&token={token}'
        subject = 'Verifica tu cuenta'
        message = f'Hola {user.username},\n\nPor favor, verifica tu cuenta haciendo clic en el siguiente enlace:\n{verification_url}'
        send_mail(
            subject, 
            message, 
            getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@construccion.com'), 
            [user.email]
        )
    except User.DoesNotExist:
        pass
