from rest_framework import serializers
from .models import Category, Product, StockMovement, Banner

class CategorySerializer(serializers.ModelSerializer):
    subcategories = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ('id', 'name', 'slug', 'parent', 'subcategories')
        extra_kwargs = {'slug': {'read_only': True}}

    def get_subcategories(self, obj):
        # Serializar subcategorías de primer nivel
        subs = obj.subcategories.all()
        return CategorySerializer(subs, many=True).data


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    subcategory_names = serializers.SerializerMethodField()
    unit_of_sale_display = serializers.CharField(source='get_unit_of_sale_display', read_only=True)
    material_display = serializers.CharField(source='get_material_display', read_only=True)

    class Meta:
        model = Product
        fields = (
            'id', 'sku', 'name', 'description', 'price', 'stock', 'min_stock',
            'weight_kg', 'length_cm', 'width_cm', 'height_cm',
            'unit_of_sale', 'unit_of_sale_display', 'brand', 'material', 'material_display',
            'image_url', 'category', 'category_name',
            'subcategories', 'subcategory_names', 'is_active',
            'technical_pdf_url', 'created_by', 'created_by_username'
        )
        read_only_fields = ('created_by',)

    def get_subcategory_names(self, obj):
        return [cat.name for cat in obj.subcategories.all()]

    def validate_sku(self, value):
        # SKU único e insensible a mayúsculas, con mensaje claro en español.
        qs = Product.objects.filter(sku__iexact=value.strip())
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Ya existe un producto con ese SKU.")
        return value


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    user_username = serializers.CharField(source='user.username', read_only=True, default=None)
    reason_display = serializers.CharField(source='get_reason_display', read_only=True)

    class Meta:
        model = StockMovement
        fields = ('id', 'product', 'product_name', 'product_sku', 'change', 'reason',
                  'reason_display', 'resulting_stock', 'user_username', 'created_at')


class LowStockProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = Product
        fields = ('id', 'sku', 'name', 'stock', 'min_stock', 'category_name', 'is_active')


class BannerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Banner
        fields = ('id', 'title', 'subtitle', 'image_url', 'link', 'slot', 'overlay_opacity', 'order', 'is_active', 'created_at', 'updated_at')
        read_only_fields = ('created_at', 'updated_at')
