from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    OrderViewSet, SubscriptionView, CartView, CartItemView, CartCouponView, CouponViewSet,
    PlanViewSet, SubscriptionCheckoutView, CancelMySubscriptionView, PaymentHistoryView,
    AdminSubscriptionListView, AdminSubscriptionActionView,
)

router = DefaultRouter()
router.register(r'orders', OrderViewSet, basename='orders')
router.register(r'coupons', CouponViewSet, basename='coupons')
router.register(r'plans', PlanViewSet, basename='plans')

urlpatterns = [
    path('subscription/', SubscriptionView.as_view(), name='subscription'),
    path('subscription/checkout/', SubscriptionCheckoutView.as_view(), name='subscription-checkout'),
    path('subscription/cancel/', CancelMySubscriptionView.as_view(), name='subscription-cancel'),
    path('payments/', PaymentHistoryView.as_view(), name='payments'),
    path('admin/subscriptions/', AdminSubscriptionListView.as_view(), name='admin-subscriptions'),
    path('admin/subscriptions/<int:user_id>/action/', AdminSubscriptionActionView.as_view(), name='admin-subscription-action'),
    path('cart/', CartView.as_view(), name='cart-detail'),
    path('cart/items/', CartItemView.as_view(), name='cart-item-add'),
    path('cart/items/<int:product_id>/', CartItemView.as_view(), name='cart-item-detail'),
    path('cart/coupon/', CartCouponView.as_view(), name='cart-coupon'),
    path('', include(router.urls)),
]
