from django.db import migrations

NEW_ATOMS = [
    ('suscripciones.ver', 'suscripciones', 'Ver planes y la sección Mis suscripciones'),
    ('suscripciones.suscribirse', 'suscripciones', 'Contratar y cancelar planes de suscripción'),
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
    dependencies = [('users', '0012_distinguir_perfiles_chat')]
    operations = [migrations.RunPython(create_atoms, remove_atoms)]
