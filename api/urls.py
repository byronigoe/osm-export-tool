# -*- coding: utf-8 -*-
"""API url configuration."""

from django.urls import re_path
from rest_framework.routers import DefaultRouter
from .views import (ConfigurationViewSet, ExportRunViewSet,
                    HDXExportRegionViewSet, PartnerExportRegionViewSet, JobViewSet, permalink, get_overpass_timestamp,
                    cancel_run, get_user_permissions, request_geonames, get_overpass_status, get_groups, stats, run_stats, request_nominatim,machine_status)


router = DefaultRouter(trailing_slash=False)
router.register(r'jobs', JobViewSet, basename='jobs')
router.register(r'runs', ExportRunViewSet, basename='runs')
router.register(
    r'configurations', ConfigurationViewSet, basename='configurations')
router.register(
    r'hdx_export_regions',
    HDXExportRegionViewSet,
    basename='hdx_export_regions')
router.register(
    r'partner_export_regions',
    PartnerExportRegionViewSet,
    basename='partner_export_regions')

app_name = 'api'
urlpatterns = router.urls
urlpatterns += [

    url(r'^permalink/(?P<uid>[a-z0-9\-]+)$', permalink),
    url(r'^request_nominatim$', request_nominatim),
    url(r'^request_geonames$', request_geonames),
    url(r'^overpass_timestamp$', get_overpass_timestamp),
    url(r'^overpass_status$', get_overpass_status),
    url(r'^permissions$', get_user_permissions),
    url(r'^groups$',get_groups),
    url(r'^stats$', stats),
    url(r'^run_stats$', run_stats),
    url(r'^status$', machine_status),
    url(r'^cancel_run$', cancel_run),
]
