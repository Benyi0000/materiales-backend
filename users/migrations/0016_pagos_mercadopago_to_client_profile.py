from django.db import migrations


def _client_profiles(apps):
    """
    Identifica el/los perfil(es) cliente. Primero por nombre exacto
    'Comprar en la tienda'; si no existe (nombre distinto en algún entorno),
    cae a los perfiles que tienen 'carrito.checkout', que es el marcador real
    de un perfil de comprador.
    """
    Profile = apps.get_model('users', 'Profile')
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    ProfilePermission = apps.get_model('users', 'ProfilePermission')

    by_name = Profile.objects.filter(name='Comprar en la tienda')
    if by_name.exists():
        return list(by_name)

    checkout = PermissionAtom.objects.filter(code='carrito.checkout').first()
    if not checkout:
        return []
    profile_ids = ProfilePermission.objects.filter(
        permission=checkout
    ).values_list('profile_id', flat=True)
    return list(Profile.objects.filter(id__in=profile_ids))


def add_mp_perm_to_client_profile(apps, schema_editor):
    """
    MercadoPago queda habilitado por defecto para los clientes (preserva el
    comportamiento actual). El gestor de perfiles puede quitar el permiso para
    deshabilitarlo, lo que oculta MercadoPago entre las opciones de pago.
    """
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    ProfilePermission = apps.get_model('users', 'ProfilePermission')

    atom = PermissionAtom.objects.filter(code='pagos.mercadopago').first()
    if not atom:
        return

    for profile in _client_profiles(apps):
        ProfilePermission.objects.get_or_create(
            profile=profile,
            permission=atom,
            defaults={'scope': 'todos'},
        )


def remove_mp_perm_from_client_profile(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    ProfilePermission = apps.get_model('users', 'ProfilePermission')

    atom = PermissionAtom.objects.filter(code='pagos.mercadopago').first()
    if not atom:
        return

    for profile in _client_profiles(apps):
        ProfilePermission.objects.filter(profile=profile, permission=atom).delete()


class Migration(migrations.Migration):
    dependencies = [('users', '0015_pagos_mercadopago_atom')]

    operations = [
        migrations.RunPython(add_mp_perm_to_client_profile, remove_mp_perm_from_client_profile)
    ]
