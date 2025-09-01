from django.urls import path
from . import views

urlpatterns = [
    path('', views.gallery, name='gallery'),
    path('demo/<slug:slug>/', views.detail, name='detail'),
    path('demo/<slug:slug>/download-all/', views.download_all, name='download_all'),
    path('thumb/<slug:slug>/', views.thumb, name='thumb'),
    path('resync/', views.resync, name='resync'),                       # global
    path('resync/<slug:slug>/', views.resync_demo, name='resync_demo'), # per-demo
]