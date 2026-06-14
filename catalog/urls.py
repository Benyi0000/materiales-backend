from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CategoryListView, ProductViewSet, ProductImageUploadView,
    LowStockReportView, StockMovementListView, BannerViewSet, PublicBannerListView,
)

router = DefaultRouter()
router.register(r'products', ProductViewSet, basename='products')
router.register(r'banners', BannerViewSet, basename='banners')

urlpatterns = [
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('products/upload-image/', ProductImageUploadView.as_view(), name='product-image-upload'),
    path('gestion/stock-bajo/', LowStockReportView.as_view(), name='gestion-stock-bajo'),
    path('gestion/stock-movimientos/', StockMovementListView.as_view(), name='gestion-stock-movimientos'),
    path('banners/public/', PublicBannerListView.as_view(), name='banners-public'),
    path('', include(router.urls)),
]
