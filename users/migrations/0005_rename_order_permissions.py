from django.db import migrations

# Renombrado de permisos atómicos de pedidos/ventas (spec pedidos/ventas).
# Renombrar el código del PermissionAtom preserva las asignaciones existentes
# (ProfilePermission referencia el atom por FK), por lo que los perfiles ya
# configurados siguen funcionando con los nuevos códigos.
#
#   pedidos.ver_propios   -> pedidos.ver               (compras del cliente)
#   pedidos.ver_todos     -> pedidosventas.ver         (ver ventas)
#   pedidos.cambiar_estado-> pedidosventas.cambiar_estado
RENAMES = [
    # (old_code, new_code, new_module, new_description)
    ('pedidos.ver_propios', 'pedidos.ver', 'orders',
     'Ver sus propias compras (Mis pedidos)'),
    ('pedidos.ver_todos', 'pedidosventas.ver', 'ventas',
     'Ver ventas (Propios: con sus productos / Todos: del sistema)'),
    ('pedidos.cambiar_estado', 'pedidosventas.cambiar_estado', 'ventas',
     'Avanzar el estado de un pedido de venta'),
]


def apply_renames(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    for old_code, new_code, module, desc in RENAMES:
        old = PermissionAtom.objects.filter(code=old_code).first()
        if old:
            # Si el nuevo código ya existe (DB nueva ya sembrada), eliminamos el viejo
            # para evitar conflicto de unicidad; si no, renombramos el viejo.
            if PermissionAtom.objects.filter(code=new_code).exclude(pk=old.pk).exists():
                old.delete()
            else:
                old.code = new_code
                old.module = module
                old.description = desc
                old.save(update_fields=['code', 'module', 'description'])
        else:
            PermissionAtom.objects.get_or_create(
                code=new_code,
                defaults={'module': module, 'description': desc},
            )


def revert_renames(apps, schema_editor):
    PermissionAtom = apps.get_model('users', 'PermissionAtom')
    reverse = [
        ('pedidos.ver', 'pedidos.ver_propios', 'orders', 'Ver solo sus propios pedidos recibidos'),
        ('pedidosventas.ver', 'pedidos.ver_todos', 'orders', 'Ver todos los pedidos del sistema'),
        ('pedidosventas.cambiar_estado', 'pedidos.cambiar_estado', 'orders', 'Cambiar estado de un pedido'),
    ]
    for new_code, old_code, module, desc in reverse:
        atom = PermissionAtom.objects.filter(code=new_code).first()
        if atom:
            if PermissionAtom.objects.filter(code=old_code).exclude(pk=atom.pk).exists():
                atom.delete()
            else:
                atom.code = old_code
                atom.module = module
                atom.description = desc
                atom.save(update_fields=['code', 'module', 'description'])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_forcepasswordchange'),
    ]

    operations = [
        migrations.RunPython(apply_renames, revert_renames),
    ]
