from django.urls import path
from . import views

urlpatterns = [
    path('', views.gallery, name='gallery'),
    path('demo/<slug:slug>/', views.detail, name='detail'),
    path('thumb/<slug:slug>/', views.thumb, name='thumb'),
]