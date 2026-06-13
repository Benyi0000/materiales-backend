from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CategoryListView, ProductViewSet, ProductImageUploadView

router = DefaultRouter()
router.register(r'products', ProductViewSet, basename='products')

urlpatterns = [
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('products/upload-image/', ProductImageUploadView.as_view(), name='product-image-upload'),
    path('', include(router.urls)),
]
