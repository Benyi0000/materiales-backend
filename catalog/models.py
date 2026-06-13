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
    sku = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.IntegerField(default=0)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, help_text="Peso en kilogramos para lógica logística")
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
