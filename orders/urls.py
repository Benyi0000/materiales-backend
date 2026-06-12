from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import OrderViewSet, SubscriptionView, CartView, CartItemView, CartCouponView, CouponViewSet

router = DefaultRouter()
router.register(r'orders', OrderViewSet, basename='orders')
router.register(r'coupons', CouponViewSet, basename='coupons')

urlpatterns = [
    path('subscription/', SubscriptionView.as_view(), name='subscription'),
    path('cart/', CartView.as_view(), name='cart-detail'),
    path('cart/items/', CartItemView.as_view(), name='cart-item-add'),
    path('cart/items/<int:product_id>/', CartItemView.as_view(), name='cart-item-detail'),
    path('cart/coupon/', CartCouponView.as_view(), name='cart-coupon'),
    path('', include(router.urls)),
]
