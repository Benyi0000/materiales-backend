from django.db import migrations


def add_subs_perms_to_client_profile(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    Profile = apps.get_model('users', 'Profile')
    ProfilePermission = apps.get_model('users', 'ProfilePermission')

    client_profile = Profile.objects.filter(name='Comprar en la tienda').first()
    if not client_profile:
        return

    for code in ('suscripciones.ver', 'suscripciones.suscribirse'):
        atom = PermissionAtom.objects.filter(code=code).first()
        if atom:
            ProfilePermission.objects.get_or_create(
                profile=client_profile,
                permission=atom,
                defaults={'scope': 'todos'},
            )


def remove_subs_perms_from_client_profile(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    Profile = apps.get_model('users', 'Profile')
    ProfilePermission = apps.get_model('users', 'ProfilePermission')

    client_profile = Profile.objects.filter(name='Comprar en la tienda').first()
    if not client_profile:
        return

    atoms = PermissionAtom.objects.filter(
        code__in=('suscripciones.ver', 'suscripciones.suscribirse')
    )
    ProfilePermission.objects.filter(
        profile=client_profile, permission__in=atoms
    ).delete()


class Migration(migrations.Migration):
    dependencies = [('users', '0013_suscripciones_atoms')]

    operations = [
        migrations.RunPython(
            add_subs_perms_to_client_profile,
            remove_subs_perms_from_client_profile,
        )
    ]
