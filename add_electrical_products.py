import os
import django
from django.contrib.auth.models import User
from catalog.models import Category, Product
from catalog.tasks import generate_product_embedding

admin_user = User.objects.filter(is_superuser=True).first()

# Create categories
electricidad, _ = Category.objects.get_or_create(name='Electricidad', slug='electricidad')
cables, _ = Category.objects.get_or_create(name='Cables', slug='cables', parent=electricidad)
canos, _ = Category.objects.get_or_create(name='Caños y Conductos', slug='canos-y-conductos', parent=electricidad)
tableros, _ = Category.objects.get_or_create(name='Tableros y Protecciones', slug='tableros-y-protecciones', parent=electricidad)
iluminacion, _ = Category.objects.get_or_create(name='Iluminación', slug='iluminacion', parent=electricidad)
tomas, _ = Category.objects.get_or_create(name='Llaves y Tomas', slug='llaves-y-tomas', parent=electricidad)

products_data = [
    # Cables (10)
    {"name": "Cable Unipolar 1.5 mm Celeste (Rollo 100m)", "sku": "ELEC-C-15-C", "price": 18000, "cat": cables, "desc": "Cable unipolar de cobre extra flexible de 1.5mm2, color celeste. Ideal para cableado de iluminación y retornos."},
    {"name": "Cable Unipolar 1.5 mm Marrón (Rollo 100m)", "sku": "ELEC-C-15-M", "price": 18000, "cat": cables, "desc": "Cable unipolar de cobre extra flexible de 1.5mm2, color marrón. Ideal para fase en circuitos de iluminación."},
    {"name": "Cable Unipolar 2.5 mm Rojo (Rollo 100m)", "sku": "ELEC-C-25-R", "price": 26000, "cat": cables, "desc": "Cable unipolar de cobre extra flexible de 2.5mm2, color rojo. Recomendado para tomacorrientes de uso general."},
    {"name": "Cable Unipolar 2.5 mm Celeste (Rollo 100m)", "sku": "ELEC-C-25-C", "price": 26000, "cat": cables, "desc": "Cable unipolar extra flexible de 2.5mm2, celeste (neutro). Recomendado para tomacorrientes."},
    {"name": "Cable Unipolar 2.5 mm Verde/Amarillo (Rollo 100m)", "sku": "ELEC-C-25-T", "price": 26000, "cat": cables, "desc": "Cable unipolar para puesta a tierra. Bicolor verde y amarillo de 2.5mm2."},
    {"name": "Cable Subterráneo 2x2.5 mm (Por Metro)", "sku": "ELEC-SUB-225", "price": 850, "cat": cables, "desc": "Cable subterráneo tipo Sintenax de 2 conductores de 2.5mm2 con doble aislación PVC. Uso exterior e instalaciones bajo tierra."},
    {"name": "Cable Subterráneo 3x4 mm (Por Metro)", "sku": "ELEC-SUB-340", "price": 1400, "cat": cables, "desc": "Cable subterráneo tripolar de 4mm2, ideal para tableros seccionales y bombas de agua."},
    {"name": "Cable Tipo Taller 2x1.5 mm (Rollo 100m)", "sku": "ELEC-TAL-215", "price": 32000, "cat": cables, "desc": "Cable flexible tipo taller redondo, 2 conductores de 1.5mm2. Para herramientas portátiles y electrodomésticos."},
    {"name": "Cable Tipo Taller 3x2.5 mm (Rollo 100m)", "sku": "ELEC-TAL-325", "price": 48000, "cat": cables, "desc": "Cable redondo flexible bipolar más tierra (3x2.5mm). Alta resistencia mecánica."},
    {"name": "Cable Coaxial RG6 para TV/Internet (Rollo 100m)", "sku": "ELEC-COAX-RG6", "price": 21000, "cat": cables, "desc": "Cable coaxial RG6 con malla de aluminio para instalaciones de TV por cable e internet."},

    # Caños y Conductos (12)
    {"name": "Caño Corrugado Blanco 3/4 (Rollo 25m)", "sku": "ELEC-COR-B34", "price": 4500, "cat": canos, "desc": "Caño corrugado plástico semipesado color blanco de 3/4 pulgada (19mm). Retardante de llama."},
    {"name": "Caño Corrugado Blanco 7/8 (Rollo 25m)", "sku": "ELEC-COR-B78", "price": 5200, "cat": canos, "desc": "Caño corrugado ignífugo blanco de 7/8. Para paso de múltiples cables."},
    {"name": "Caño Corrugado Naranja 3/4 (Rollo 25m)", "sku": "ELEC-COR-N34", "price": 3800, "cat": canos, "desc": "Caño corrugado estándar color naranja de 3/4. Para instalaciones embutidas en mampostería ligera."},
    {"name": "Caño Rígido PVC Blanco 3/4 (Tira 3m)", "sku": "ELEC-RIG-B34", "price": 2100, "cat": canos, "desc": "Tubo rígido de PVC blanco de 3/4, tipo Tubelectric, para instalaciones a la vista."},
    {"name": "Caño Rígido PVC Blanco 1 pulgada (Tira 3m)", "sku": "ELEC-RIG-B1", "price": 2800, "cat": canos, "desc": "Tubo rígido PVC de 1 pulgada para montaje en superficie."},
    {"name": "Curva 90° PVC Blanca 3/4", "sku": "ELEC-CUR-B34", "price": 450, "cat": canos, "desc": "Curva a 90 grados para caño rígido PVC de 3/4."},
    {"name": "Conector PVC Blanco a Caja 3/4", "sku": "ELEC-CON-B34", "price": 300, "cat": canos, "desc": "Unión roscada para conectar caño rígido PVC de 3/4 a cajas de pase."},
    {"name": "Cable Canal Blanco 20x10mm con Adhesivo (Tira 2m)", "sku": "ELEC-CC-2010", "price": 1800, "cat": canos, "desc": "Cablecanal plástico para instalaciones exteriores. Medidas 20x10 mm, con cinta adhesiva doble faz incorporada."},
    {"name": "Cable Canal Blanco 40x20mm (Tira 2m)", "sku": "ELEC-CC-4020", "price": 3200, "cat": canos, "desc": "Cable canal de mayor capacidad, 40x20mm. Ideal para múltiples redes y energía."},
    {"name": "Caja Octogonal de Chapa Liviana", "sku": "ELEC-CAJ-OCT", "price": 600, "cat": canos, "desc": "Caja octogonal chica de chapa galvanizada para centros de iluminación."},
    {"name": "Caja Rectangular Mignon de Chapa", "sku": "ELEC-CAJ-MIG", "price": 550, "cat": canos, "desc": "Caja rectangular estándar (10x5cm) en chapa estampada para interruptores y tomacorrientes embutidos."},
    {"name": "Caja Estanca de Paso PVC 100x100mm IP65", "sku": "ELEC-EST-100", "price": 2500, "cat": canos, "desc": "Caja de paso estanca cuadrada de 100x100mm con tapa atornillada y protección IP65 contra agua y polvo."},

    # Tableros y Protecciones (10)
    {"name": "Llave Termomagnética Bipolar 16A Sica", "sku": "ELEC-TER-216", "price": 8500, "cat": tableros, "desc": "Interruptor termomagnético (térmica) de 2 polos x 16 Amperes. Curva C. Ideal iluminación y circuitos estándar."},
    {"name": "Llave Termomagnética Bipolar 20A Sica", "sku": "ELEC-TER-220", "price": 8500, "cat": tableros, "desc": "Térmica bipolar de 20A para circuitos de tomacorrientes de uso general."},
    {"name": "Llave Termomagnética Bipolar 32A Sica", "sku": "ELEC-TER-232", "price": 9200, "cat": tableros, "desc": "Térmica de 32 Amperes. Ideal para circuitos de aire acondicionado o protección general."},
    {"name": "Interruptor Diferencial Bipolar 25A 30mA (Disyuntor)", "sku": "ELEC-DIS-225", "price": 25000, "cat": tableros, "desc": "Disyuntor diferencial salvavidas de 2 polos, 25A y sensibilidad de 30mA. Protección esencial contra contactos indirectos."},
    {"name": "Interruptor Diferencial Bipolar 40A 30mA", "sku": "ELEC-DIS-240", "price": 28000, "cat": tableros, "desc": "Disyuntor diferencial de 40A para tableros principales de vivienda."},
    {"name": "Tablero Embutir Plástico 8 a 12 Polos (DIN)", "sku": "ELEC-TAB-E12", "price": 6500, "cat": tableros, "desc": "Caja para llaves térmicas de embutir, capacidad de 8 a 12 módulos DIN. Con tapa fumé."},
    {"name": "Tablero Superficie Exterior 4 a 6 Polos", "sku": "ELEC-TAB-S6", "price": 4200, "cat": tableros, "desc": "Tablero exterior para adosar, capacidad hasta 6 módulos DIN. Ideal ampliaciones pequeñas."},
    {"name": "Jabalina de Cobre 3/8 x 1.5m con Tomacable", "sku": "ELEC-JAB-15", "price": 12000, "cat": tableros, "desc": "Electrodo de puesta a tierra (jabalina) de acero bañado en cobre, longitud 1.5 metros, incluye prensa cable de bronce."},
    {"name": "Caja de Inspección para Jabalina PVC", "sku": "ELEC-CAJ-JAB", "price": 1800, "cat": tableros, "desc": "Cámara de inspección plástica redonda para jabalina de puesta a tierra."},
    {"name": "Fusible Tabaquera 25A con Base", "sku": "ELEC-FUS-25", "price": 3000, "cat": tableros, "desc": "Base portafusible tipo tabaquera con cartucho cilíndrico de 25A."},

    # Llaves y Tomas (10)
    {"name": "Modulo Tomacorriente 10A Blanco Jeluz", "sku": "ELEC-TOM-10B", "price": 950, "cat": tomas, "desc": "Módulo tomacorriente combinado 10A para fichas binorma (patas chatas y redondas). Color blanco."},
    {"name": "Modulo Tomacorriente 20A Blanco (Pata Ancha)", "sku": "ELEC-TOM-20B", "price": 1200, "cat": tomas, "desc": "Toma de 20A para equipos de alto consumo como hornos eléctricos y aires acondicionados."},
    {"name": "Modulo Punto/Interruptor Simple Jeluz", "sku": "ELEC-INT-SIM", "price": 850, "cat": tomas, "desc": "Módulo interruptor simple de 1 punto para encendido de luminarias."},
    {"name": "Modulo Interruptor Combinación (Escalera)", "sku": "ELEC-INT-COM", "price": 1100, "cat": tomas, "desc": "Punto de combinación para encender la misma luz desde dos lugares diferentes (escaleras, pasillos)."},
    {"name": "Tapa Bastidor Blanco 3 Módulos (Línea Verona)", "sku": "ELEC-TAP-VER", "price": 700, "cat": tomas, "desc": "Tapa y bastidor integrados color blanco brillante. Capacidad para 3 módulos estándar."},
    {"name": "Tapa Ciega Blanca", "sku": "ELEC-TAP-CIE", "price": 500, "cat": tomas, "desc": "Tapa para tapar cajas rectangulares en desuso."},
    {"name": "Tapón Ciego Módulo", "sku": "ELEC-MOD-CIE", "price": 200, "cat": tomas, "desc": "Módulo ciego para ocupar espacios vacíos en bastidores de tomacorrientes."},
    {"name": "Modulo Cargador USB Doble 5V 2.1A", "sku": "ELEC-USB-DOB", "price": 5500, "cat": tomas, "desc": "Módulo de carga USB doble para pared. Salida total 2.1A para smartphones y tablets."},
    {"name": "Ficha Macho 10A 3 Patas Chatas", "sku": "ELEC-FIC-M10", "price": 450, "cat": tomas, "desc": "Enchufe macho desarmable de 3 patas chatas (con tierra), 10 Amperes."},
    {"name": "Ficha Hembra 10A Exterior", "sku": "ELEC-FIC-H10", "price": 500, "cat": tomas, "desc": "Ficha prolongadora hembra (zapatilla individual) para cables tipo taller."},

    # Iluminación y Varios (8)
    {"name": "Lámpara LED Bulbo 9W E27 Luz Fría", "sku": "ELEC-LED-9F", "price": 1500, "cat": iluminacion, "desc": "Foco LED tipo bulbo estándar, rosca E27. 9 Watts, equivalente a 60W. Luz blanca fría 6500K."},
    {"name": "Lámpara LED Bulbo 9W E27 Luz Cálida", "sku": "ELEC-LED-9C", "price": 1500, "cat": iluminacion, "desc": "Foco LED bulbo rosca E27, 9W. Luz cálida 3000K, ideal para dormitorios y livings."},
    {"name": "Listón Tubo LED 18W 1.20m Frío", "sku": "ELEC-TUB-18", "price": 4800, "cat": iluminacion, "desc": "Artefacto listón completo con tubo LED de vidrio de 1.2 metros, luz fría. Conexión a 220V directa."},
    {"name": "Reflector LED Exterior 50W IP65 Frío", "sku": "ELEC-REF-50", "price": 12500, "cat": iluminacion, "desc": "Proyector reflector LED chato de 50W. Apto para intemperie (IP65). Alto brillo para fachadas y patios."},
    {"name": "Cinta Aisladora PVC Negra 19mm x 20m", "sku": "ELEC-CIN-N20", "price": 1100, "cat": iluminacion, "desc": "Cinta aislante eléctrica de PVC color negra, calidad premium. Rollo de 20 metros."},
    {"name": "Cinta Aisladora PVC Colores (Pack x5)", "sku": "ELEC-CIN-COL", "price": 4500, "cat": iluminacion, "desc": "Pack de 5 cintas aisladoras de colores (rojo, verde, blanco, azul, amarillo) para identificación de fases."},
    {"name": "Buscapolo Digital con Visor LCD", "sku": "ELEC-BUS-DIG", "price": 2800, "cat": iluminacion, "desc": "Destornillador buscapolo digital, detecta tensión directa e inducida (12V a 220V)."},
    {"name": "Cinta Pasacable Plástica 15 Metros", "sku": "ELEC-PAS-15", "price": 3500, "cat": iluminacion, "desc": "Cinta pasacables de nylon de alta resistencia con puntera metálica. Largo 15m, diámetro 4mm."}
]

created_products = []
for pdata in products_data:
    obj, created = Product.objects.get_or_create(
        sku=pdata['sku'],
        defaults={
            'name': pdata['name'],
            'description': pdata['desc'],
            'price': pdata['price'],
            'stock': 100,
            'category': pdata['cat'],
            'is_active': True,
            'created_by': admin_user,
            'weight_kg': 1.0
        }
    )
    if created:
        obj.subcategories.add(electricidad)
        created_products.append(obj)

print(f"Creados {len(created_products)} productos eléctricos.")

# Encolar generación de embeddings
for p in created_products:
    generate_product_embedding.delay(p.id)

print("Enviados a Celery para vectorizar.")
