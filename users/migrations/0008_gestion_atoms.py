from django.db import migrations


NEW_ATOMS = [
    ('gestion.ver_stock_bajo', 'gestion', 'Ver reporte de stock bajo e historial de movimientos'),
    ('gestion.gestionar_planes', 'gestion', 'Crear y configurar tipos de planes de suscripción'),
    ('gestion.gestionar_suscripciones', 'gestion', 'Administrar suscripciones de cualquier usuario'),
]


def create_atoms(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    for code, module, desc in NEW_ATOMS:
        PermissionAtom.objects.get_or_create(
            code=code,
            defaults={'module': module, 'description': desc, 'scope_aplica': False},
        )


def remove_atoms(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    PermissionAtom.objects.filter(code__in=[c for c, _, _ in NEW_ATOMS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0007_activesession'),
    ]

    operations = [
        migrations.RunPython(create_atoms, remove_atoms),
    ]
