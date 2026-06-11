from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils.text import slugify
from users.models import PermissionAtom, Profile, ProfilePermission, UserProfileAssignment
from catalog.models import Category, Product
from orders.models import Subscription

class Command(BaseCommand):
    help = 'Semilla inicial de permisos, perfiles, categorías y catálogo de productos demostrativos.'

    def handle(self, *args, **kwargs):
        self.stdout.write("Iniciando la carga de datos de prueba...")

        # Limpiar registros previos para evitar conflictos de códigos antiguos
        PermissionAtom.objects.all().delete()
        Profile.objects.all().delete()

        # ----------------------------------------------------
        # 1. Crear Permisos Atómicos
        # ----------------------------------------------------
        permissions_data = [
            # Catálogo
            ('catalogo.crear_producto', 'catalog', 'Crear nuevos productos'),
            ('catalogo.editar_producto', 'catalog', 'Editar productos existentes'),
            ('catalogo.eliminar_producto', 'catalog', 'Eliminar productos'),
            ('catalogo.gestionar_stock', 'catalog', 'Modificar stock disponible'),
            ('catalogo.ver_catalogo', 'catalog', 'Ver el catálogo navegable'),
            
            # Pedidos
            ('pedidos.ver_propios', 'orders', 'Ver solo sus propios pedidos recibidos'),
            ('pedidos.ver_todos', 'orders', 'Ver todos los pedidos del sistema'),
            ('pedidos.cambiar_estado', 'orders', 'Cambiar estado de un pedido'),
            
            # Carrito
            ('carrito.gestionar', 'cart', 'Agregar, editar y eliminar del carrito'),
            ('carrito.checkout', 'cart', 'Confirmar pedidos'),
            
            # Tutor IA
            ('tutor.acceder', 'tutor', 'Acceder al Tutor Visual IA'),
            ('tutor.ver_historial', 'tutor', 'Ver historial de proyectos'),
            
            # Gestión Interna
            ('gestion.ver_dashboard', 'gestion', 'Ver métricas y reportes de ventas'),
            ('gestion.exportar_reportes', 'gestion', 'Exportar reportes de pedidos/stock'),
            ('gestion.gestionar_banners', 'gestion', 'Crear y ordenar banners del home'),
            ('gestion.gestionar_promociones', 'gestion', 'Crear cupones y descuentos'),
            
            # Administración
            ('admin.gestionar_usuarios', 'admin', 'Crear, editar, activar/desactivar usuarios'),
            ('admin.asignar_perfiles', 'admin', 'Asignar/revocar perfiles a usuarios'),
            ('admin.configurar_perfiles', 'admin', 'Crear y configurar perfiles'),
            ('admin.ver_auditoria', 'admin', 'Ver log de auditoría'),
            ('admin.configuracion_general', 'admin', 'Modificar parámetros globales del sistema'),
        ]

        permission_atoms = {}
        for code, module, desc in permissions_data:
            atom, created = PermissionAtom.objects.get_or_create(
                code=code,
                defaults={'module': module, 'description': desc}
            )
            permission_atoms[code] = atom
            if created:
                self.stdout.write(f"Permiso creado: {code}")

        # ----------------------------------------------------
        # 2. Crear Perfiles por Defecto
        # ----------------------------------------------------
        # Perfil 1: Comprar en la tienda (Cliente base)
        client_profile, _ = Profile.objects.get_or_create(
            name="Comprar en la tienda",
            defaults={'description': 'Perfil base para compras ordinarias en la tienda.'}
        )
        ProfilePermission.objects.get_or_create(
            profile=client_profile,
            permission=permission_atoms['carrito.gestionar'],
            defaults={'scope': 'propios'}
        )
        ProfilePermission.objects.get_or_create(
            profile=client_profile,
            permission=permission_atoms['carrito.checkout'],
            defaults={'scope': 'propios'}
        )
        ProfilePermission.objects.get_or_create(
            profile=client_profile,
            permission=permission_atoms['catalogo.ver_catalogo'],
            defaults={'scope': 'propios'}
        )
        ProfilePermission.objects.get_or_create(
            profile=client_profile,
            permission=permission_atoms['pedidos.ver_propios'],
            defaults={'scope': 'propios'}
        )

        # Perfil 5: Administrador General
        admin_profile, _ = Profile.objects.get_or_create(
            name="Administrador del Sistema",
            defaults={'description': 'Acceso total a la configuración y auditoría.'}
        )
        for code, atom in permission_atoms.items():
            ProfilePermission.objects.get_or_create(
                profile=admin_profile,
                permission=atom,
                defaults={'scope': 'todos'}
            )

        self.stdout.write("Perfiles y permisos configurados con éxito.")

        # ----------------------------------------------------
        # 3. Crear Categorías
        # ----------------------------------------------------
        categories_data = [
            ("Obra Gruesa", ["Cementos y Cales", "Ladrillos y Bloques", "Hierros y Mallas"]),
            ("Terminaciones", ["Adhesivos y Pastinas", "Yesos y Placas de Yeso", "Pinturas e Impermeabilizantes"]),
        ]

        category_objs = {}
        for parent_name, subcategories in categories_data:
            parent_cat, _ = Category.objects.get_or_create(
                name=parent_name,
                defaults={'slug': slugify(parent_name)}
            )
            category_objs[parent_name] = parent_cat
            for sub_name in subcategories:
                sub_cat, _ = Category.objects.get_or_create(
                    name=sub_name,
                    defaults={'slug': slugify(sub_name), 'parent': parent_cat}
                )
                category_objs[sub_name] = sub_cat

        self.stdout.write("Categorías creadas.")

        # ----------------------------------------------------
        # 4. Crear Catálogo de 20 Productos Demostrativos (RF 2)
        # ----------------------------------------------------
        products_data = [
            # Cementos y Cales
            ("Cem-001", "Cemento Portland Normal Loma Negra 50kg", "Cemento de alta resistencia para estructuras de hormigón y albañilería general.", 12500.00, 150, 50.0, "Cementos y Cales"),
            ("Cem-002", "Cemento Portland Avellaneda 50kg", "Cemento de fraguado rápido y alta resistencia inicial, óptimo para losas.", 12300.00, 100, 50.0, "Cementos y Cales"),
            ("Cem-003", "Cal Hidratada Loma Negra 25kg", "Cal hidratada aérea de alta plasticidad para mezcla de revoques y contrapisos.", 5400.00, 200, 25.0, "Cementos y Cales"),
            
            # Ladrillos y Bloques
            ("Lad-001", "Ladrillo Hueco 12x18x33 (Por unidad)", "Ladrillo cerámico hueco de 6 tubos para muros portantes y divisiones.", 480.00, 2500, 4.2, "Ladrillos y Bloques"),
            ("Lad-002", "Ladrillo Hueco 18x18x33 (Por unidad)", "Ladrillo hueco de 9 tubos ideal para paredes exteriores y cerramientos térmicos.", 650.00, 1800, 5.8, "Ladrillos y Bloques"),
            ("Lad-003", "Ladrillo Común de Campo (Por 1000 u)", "Ladrillo macizo tradicional para hornos, paredes a la vista y cimientos.", 145000.00, 15, 2000.0, "Ladrillos y Bloques"),
            ("Lad-004", "Bloque de Hormigón 19x19x39 (Por unidad)", "Bloque de hormigón prensado para construcción modular de alta velocidad.", 980.00, 1200, 14.0, "Ladrillos y Bloques"),

            # Hierros y Mallas
            ("Hie-001", "Hierro Nervado Aluar 8mm (Barra 12m)", "Barra de acero nervado grado 420 para armaduras de hormigón armado.", 10800.00, 80, 4.74, "Hierros y Mallas"),
            ("Hie-002", "Hierro Nervado Aluar 10mm (Barra 12m)", "Barra de acero nervado grado 420 utilizada en vigas y columnas principales.", 16800.00, 60, 7.4, "Hierros y Mallas"),
            ("Hie-003", "Hierro Nervado Aluar 12mm (Barra 12m)", "Barra de acero nervado estructural de alta exigencia.", 24100.00, 40, 10.65, "Hierros y Mallas"),
            ("Hie-004", "Malla Sima C-15 15x15 (Paño 2.4x6m)", "Malla electrosoldada de alambre nervado para refuerzo de contrapisos y plateas.", 38500.00, 30, 22.0, "Hierros y Mallas"),

            # Adhesivos y Pastinas
            ("Adh-001", "Pegamento Impermeable Weber Col 25kg", "Mezcla adhesiva impermeable para la colocación de cerámicas en interiores y exteriores.", 8700.00, 90, 25.0, "Adhesivos y Pastinas"),
            ("Adh-002", "Adhesivo Weber Porcelanato Flex 25kg", "Pegamento flexible de alta tecnología para porcelanatos de gran formato.", 17500.00, 75, 25.0, "Adhesivos y Pastinas"),
            ("Adh-003", "Pastina Weber Classic 2kg (Gris)", "Mezcla cementicia para relleno de juntas de 1 a 4 mm en revestimientos.", 2900.00, 120, 2.0, "Adhesivos y Pastinas"),

            # Yesos y Placas
            ("Yes-001", "Placa de Yeso Durlock Estándar 12.5mm", "Placa de yeso estándar de 1.20 x 2.40 metros para tabiques y cielorrasos.", 14800.00, 85, 21.0, "Yesos y Placas de Yeso"),
            ("Yes-002", "Placa de Yeso Durlock Verde (Antihumedad)", "Placa de yeso impregnada para zonas húmedas como baños y cocinas.", 22300.00, 50, 22.0, "Yesos y Placas de Yeso"),
            ("Yes-003", "Yeso Manual Tuyango 40kg", "Yeso tradicional de fraguado lento para enlucidos y revoques interiores de alta calidad.", 9500.00, 110, 40.0, "Yesos y Placas de Yeso"),

            # Pinturas e Impermeabilizantes
            ("Pin-001", "Pintura Látex Interior Alba Mate 20L", "Pintura al agua de alto poder cubritivo y lavable para ambientes interiores.", 89000.00, 25, 28.0, "Pinturas e Impermeabilizantes"),
            ("Pin-002", "Membrana Líquida Weber Tech 20kg", "Impermeabilizante acrílico elastomérico con poliuretano para techos y terrazas transitables.", 124000.00, 30, 20.0, "Pinturas e Impermeabilizantes"),
            ("Pin-003", "Fijador Sellador Alba Concentrado 4L", "Acondicionador de superficies para homogeneizar la absorción antes de pintar.", 15200.00, 50, 4.2, "Pinturas e Impermeabilizantes"),
        ]

        for sku, name, desc, price, stock, weight, cat_name in products_data:
            cat = category_objs.get(cat_name)
            prod, created = Product.objects.get_or_create(
                sku=sku,
                defaults={
                    'name': name,
                    'description': desc,
                    'price': price,
                    'stock': stock,
                    'weight_kg': weight,
                    'category': cat,
                    'image_url': f"https://placehold.co/300?text={sku}"
                }
            )
            if cat:
                prod.subcategories.add(cat)
            if created:
                self.stdout.write(f"Producto creado: {name}")

        # ----------------------------------------------------
        # 5. Crear Superusuario de Administración
        # ----------------------------------------------------
        if not User.objects.filter(username="admin").exists():
            admin_user = User.objects.create_superuser(
                username="admin",
                email="admin@construccion.com",
                password="adminpassword"
            )
            # Asignar perfil de Administrador del Sistema
            UserProfileAssignment.objects.create(
                user=admin_user,
                profile=admin_profile,
                assigned_by=None
            )
            self.stdout.write("Superusuario 'admin' creado (Clave: adminpassword). Perfil Administrador asignado.")

        self.stdout.write("La carga de datos semilla ha finalizado con éxito.")
