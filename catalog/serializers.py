from rest_framework import serializers
from .models import Category, Product, StockMovement, Banner

class CategorySerializer(serializers.ModelSerializer):
    subcategories = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ('id', 'name', 'slug', 'parent', 'subcategories')

    def get_subcategories(self, obj):
        # Serializar subcategorías de primer nivel
        subs = obj.subcategories.all()
        return CategorySerializer(subs, many=True).data


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    subcategory_names = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            'id', 'sku', 'name', 'description', 'price', 'stock', 'min_stock',
            'weight_kg', 'image_url', 'category', 'category_name',
            'subcategories', 'subcategory_names', 'is_active',
            'technical_pdf_url', 'created_by', 'created_by_username'
        )
        read_only_fields = ('created_by',)

    def get_subcategory_names(self, obj):
        return [cat.name for cat in obj.subcategories.all()]


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
        fields = ('id', 'title', 'image_url', 'link', 'order', 'is_active', 'created_at', 'updated_at')
        read_only_fields = ('created_at', 'updated_at')
