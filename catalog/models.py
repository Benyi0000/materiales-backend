from django.db import models

class Category(models.Model):
    """
    Categorías de productos con soporte para jerarquías (subcategorías)
    """
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    parent = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='subcategories'
    )

    class Meta:
        verbose_name = "Categoría"
        verbose_name_plural = "Categorías"

    def __str__(self):
        full_path = [self.name]
        k = self.parent
        while k is not None:
            full_path.append(k.name)
            k = k.parent
        return " -> ".join(reversed(full_path))


class Product(models.Model):
    """
    Modelo de Producto para el E-commerce.
    RNF 2: pgvector implementado con campo embedding de 384 dimensiones.
    """
    UNIT_CHOICES = (
        ('unidad', 'Unidad'),
        ('kg', 'Kilogramo'),
        ('m', 'Metro'),
        ('m2', 'Metro cuadrado'),
        ('m3', 'Metro cúbico'),
        ('litro', 'Litro'),
        ('bolsa', 'Bolsa'),
        ('rollo', 'Rollo'),
        ('par', 'Par'),
        ('caja', 'Caja'),
        ('pallet', 'Pallet'),
    )
    MATERIAL_CHOICES = (
        ('cemento', 'Cemento / Hormigón'),
        ('madera', 'Madera'),
        ('metal', 'Metal / Acero'),
        ('plastico', 'Plástico / PVC'),
        ('ceramico', 'Cerámico'),
        ('vidrio', 'Vidrio'),
        ('pintura', 'Pintura / Química'),
        ('electrico', 'Eléctrico'),
        ('otro', 'Otro'),
    )

    sku = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.IntegerField(default=0)
    min_stock = models.PositiveIntegerField(default=5, help_text="Umbral mínimo: por debajo se considera stock bajo")
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, help_text="Peso en kilogramos para lógica logística")
    length_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Largo en centímetros")
    width_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Ancho en centímetros")
    height_cm = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Alto en centímetros")
    unit_of_sale = models.CharField(max_length=10, choices=UNIT_CHOICES, default='unidad', help_text="Unidad en la que se vende el producto")
    brand = models.CharField(max_length=100, blank=True, help_text="Marca o fabricante")
    material = models.CharField(max_length=20, choices=MATERIAL_CHOICES, blank=True, help_text="Material principal del producto")
    image_url = models.URLField(max_length=512, blank=True, null=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products')
    subcategories = models.ManyToManyField(Category, related_name='multi_products', blank=True, help_text="Subcategorías a las que pertenece el producto")
    is_active = models.BooleanField(default=True, help_text="Indica si el producto está activo para los clientes")
    technical_pdf_url = models.URLField(
        max_length=512, 
        blank=True, 
        null=True, 
        help_text="Enlace a ficha técnica en PDF para el Tutor RAG"
    )
    created_by = models.ForeignKey(
        'auth.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_products',
        help_text="Usuario que creó el producto"
    )
    
    # Campo embedding de 768 dimensiones (Google Gemini)
    try:
        from pgvector.django import VectorField
        embedding = VectorField(dimensions=768, null=True, blank=True)
    except ImportError:
        # Fallback de desarrollo en caso de error de importación
        embedding = models.BinaryField(null=True, blank=True, help_text="Vectores semánticos (768 dimensiones)")

    def __str__(self):
        return f"{self.sku} - {self.name}"


class StockMovement(models.Model):
    """
    Historial de movimientos de stock (se registra hacia adelante: ventas,
    ajustes manuales, reposición). Usado por el reporte de stock bajo.
    """
    REASON_CHOICES = (
        ('sale', 'Venta'),
        ('adjust', 'Ajuste manual'),
        ('restock', 'Reposición'),
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_movements')
    change = models.IntegerField(help_text="Variación de stock (negativa = salida, positiva = entrada)")
    reason = models.CharField(max_length=10, choices=REASON_CHOICES)
    resulting_stock = models.IntegerField(help_text="Stock resultante luego del movimiento")
    user = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='stock_movements')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product.sku} {self.change:+d} ({self.get_reason_display()})"


class Banner(models.Model):
    """
    Banner promocional del catálogo público. Se administra desde Gestión Interna
    (gestion.gestionar_banners) y se muestra en su slot/ubicación si está activo.
    """
    SLOT_CHOICES = (
        ('hero', 'Hero (imagen grande arriba del catálogo)'),
        ('carousel', 'Carrusel (franja debajo del hero)'),
    )
    title = models.CharField(max_length=150, blank=True, help_text="Título principal (en Hero, el encabezado grande)")
    subtitle = models.CharField(max_length=300, blank=True, help_text="Subtítulo (solo aplica al Hero)")
    image_url = models.URLField(max_length=512)
    link = models.URLField(max_length=512, blank=True, help_text="Destino opcional al hacer clic")
    slot = models.CharField(max_length=20, choices=SLOT_CHOICES, default='carousel',
                            help_text="Ubicación del catálogo público donde se muestra")
    overlay_opacity = models.PositiveIntegerField(
        default=55,
        help_text="Opacidad del oscurecido sobre la imagen (0-100), para legibilidad del texto en el Hero"
    )
    order = models.PositiveIntegerField(default=0, help_text="Orden de aparición dentro del slot")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.title or f"Banner #{self.id}"
