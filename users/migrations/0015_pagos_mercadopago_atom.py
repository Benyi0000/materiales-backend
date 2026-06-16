from django.db import migrations

NEW_ATOMS = [
    ('pagos.mercadopago', 'pagos', 'Habilitar MercadoPago como medio de pago en el checkout'),
]


def create_atoms(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    for code, module, desc in NEW_ATOMS:
        PermissionAtom.objects.get_or_create(
            code=code, defaults={'module': module, 'description': desc, 'scope_aplica': False},
        )


def remove_atoms(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    PermissionAtom.objects.filter(code__in=[c for c, _, _ in NEW_ATOMS]).delete()


class Migration(migrations.Migration):
    dependencies = [('users', '0014_suscripciones_perms_to_client_profile')]
    operations = [migrations.RunPython(create_atoms, remove_atoms)]
