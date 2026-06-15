"""
Lógica de negocio de suscripciones: asignación/revocación dinámica de los
perfiles que otorga un Plan, activación con pago simulado, y renovación/
expiración automáticas.
"""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone

from users.models import UserProfileAssignment, PermissionAuditLog
from .models import Subscription, Payment


def _assign_plan_profiles(user, plan, by=None):
    for profile in plan.profiles.all():
        assignment, created = UserProfileAssignment.objects.get_or_create(
            user=user, profile=profile,
            defaults={'assigned_by': by, 'is_active': True},
        )
        if not created and not assignment.is_active:
            assignment.is_active = True
            assignment.save(update_fields=['is_active'])
        if created:
            PermissionAuditLog.objects.create(
                user=user, profile=profile, action='subs_activate', performed_by=by,
                notes=f"Activación de plan '{plan.name}'",
            )


def _revoke_plan_profiles(user, plan, by=None):
    if not plan:
        return
    profiles = list(plan.profiles.all())
    UserProfileAssignment.objects.filter(user=user, profile__in=profiles).delete()
    for profile in profiles:
        PermissionAuditLog.objects.create(
            user=user, profile=profile, action='subs_deactivate', performed_by=by,
            notes=f"Baja de plan '{plan.name}'",
        )


@transaction.atomic
def activate_plan(user, plan, by=None, register_payment=True):
    """
    Activa (o cambia) la suscripción del usuario al plan dado, asigna sus
    perfiles y registra el pago simulado (salvo en período de prueba).
    """
    sub, _ = Subscription.objects.get_or_create(user=user)
    # Si tenía otro plan, revocar sus perfiles primero
    if sub.current_plan_id and sub.current_plan_id != plan.id:
        _revoke_plan_profiles(user, sub.current_plan, by=by)

    now = timezone.now()
    sub.current_plan = plan
    sub.cancel_at_period_end = False
    if plan.trial_days:
        sub.status = 'trialing'
        sub.end_date = now + timedelta(days=plan.trial_days)
    else:
        sub.status = 'active'
        sub.end_date = now + timedelta(days=plan.duration_days)
    sub.plan = 'premium' if plan.profiles.exists() else 'free'  # compat legacy
    sub.save()

    _assign_plan_profiles(user, plan, by=by)

    if register_payment and not plan.trial_days:
        Payment.objects.create(user=user, plan=plan, amount=plan.price)
    return sub


@transaction.atomic
def cancel_plan(user, by=None, immediate=False):
    """
    Cancela la suscripción. Por defecto se revoca al fin del período
    (cancel_at_period_end). Con immediate=True revoca al instante.
    """
    sub = Subscription.objects.filter(user=user).first()
    if not sub or not sub.current_plan_id:
        return sub
    if immediate:
        _revoke_plan_profiles(user, sub.current_plan, by=by)
        sub.status = 'expired'
        sub.end_date = timezone.now()
        sub.cancel_at_period_end = True
        sub.save(update_fields=['status', 'end_date', 'cancel_at_period_end'])
    else:
        sub.cancel_at_period_end = True
        sub.status = 'cancelled'
        sub.save(update_fields=['cancel_at_period_end', 'status'])
    return sub


@transaction.atomic
def process_renewals_and_expirations():
    """
    Tarea diaria: renueva las suscripciones vencidas con auto-renovación
    (cobro simulado) y expira+revoca las canceladas o sin auto-renovación.
    """
    now = timezone.now()
    due = Subscription.objects.select_related('current_plan').filter(
        current_plan__isnull=False, end_date__lte=now,
    ).exclude(status='expired')

    renewed = expired = 0
    for sub in due:
        plan = sub.current_plan
        if sub.cancel_at_period_end or not plan.auto_renew or not plan.is_active:
            _revoke_plan_profiles(sub.user, plan)
            sub.status = 'expired'
            sub.save(update_fields=['status'])
            expired += 1
        else:
            sub.status = 'active'
            sub.end_date = now + timedelta(days=plan.duration_days)
            sub.save(update_fields=['status', 'end_date'])
            Payment.objects.create(user=sub.user, plan=plan, amount=plan.price)
            _assign_plan_profiles(sub.user, plan)  # asegura perfiles
            renewed += 1
    return renewed, expired
