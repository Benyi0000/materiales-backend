from django.db import migrations

PROFILE_NAME = "Chat bot + búsqueda semántica + historial de sesiones"
PERMS = ["tutor.acceder", "catalogo.busqueda_semantica", "tutor.ver_historial"]


def create_profile(apps, schema_editor):
    Profile = apps.get_model("users", "Profile")
    PermissionAtom = apps.get_model("users", "PermissionAtom")
    ProfilePermission = apps.get_model("users", "ProfilePermission")
    profile, _ = Profile.objects.get_or_create(
        name=PROFILE_NAME,
        defaults={"description": "Tutor Visual IA con búsqueda semántica e historial de sesiones (plan premium superior)."},
    )
    for code in PERMS:
        atom = PermissionAtom.objects.filter(code=code).first()
        if atom:
            ProfilePermission.objects.get_or_create(
                profile=profile, permission=atom, defaults={"scope": "todos"}
            )


def remove_profile(apps, schema_editor):
    Profile = apps.get_model("users", "Profile")
    Profile.objects.filter(name=PROFILE_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0010_chatbot_historial_profile"),
    ]

    operations = [
        migrations.RunPython(create_profile, remove_profile),
    ]
