from rest_framework import serializers
from .models import Category, Product

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
            'id', 'sku', 'name', 'description', 'price', 'stock', 
            'weight_kg', 'image_url', 'category', 'category_name', 
            'subcategories', 'subcategory_names', 'is_active',
            'technical_pdf_url', 'created_by', 'created_by_username'
        )
        read_only_fields = ('created_by',)

    def get_subcategory_names(self, obj):
        return [cat.name for cat in obj.subcategories.all()]
