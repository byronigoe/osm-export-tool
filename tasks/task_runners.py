# noqa
# -*- coding: utf-8 -*-

import logging
import os
from os.path import join, exists, basename
import json
import ast
import shutil
import zipfile
import traceback
import configparser
import django
from dramatiq.middleware import TimeLimitExceeded

from dramatiq import get_broker
from dramatiq_abort import Abortable, backends
import redis

from django.apps import apps
from django.conf import settings
from django import db

if not apps.ready and not settings.configured:
    django.setup()

import dramatiq
from raven import Client

from django.utils import timezone
from django.utils.text import get_valid_filename

from jobs.models import Job, HDXExportRegion, PartnerExportRegion
from tasks.models import ExportRun, ExportTask
from hdx_exports.hdx_export_set import slugify, sync_region

import osm_export_tool
import osm_export_tool.tabular as tabular
import osm_export_tool.nontabular as nontabular
from osm_export_tool.mapping import Mapping
from osm_export_tool.geometry import load_geometry
from osm_export_tool.sources import Overpass, OsmiumTool, Galaxy
from osm_export_tool.package import create_package, create_posm_bundle

import shapely.geometry

from .email import (
    send_completion_notification,
    send_error_notification,
    send_hdx_completion_notification,
    send_hdx_error_notification,
)

from .pdc import run_pdc_task

client = Client()

LOG = logging.getLogger(__name__)

ZIP_README = """This thematic file was generated by the HOT Export Tool.
For more information, visit http://export.hotosm.org .
This theme includes features matching the filter:
{criteria}
clipped to the area defined by the included boundary.geojson.
This theme includes the following OpenStreetMap keys:
{columns}
(c) OpenStreetMap contributors.
This file is made available under the Open Database License: http://opendatacommons.org/licenses/odbl/1.0/. Any rights in individual contents of the database are licensed under the Database Contents License: http://opendatacommons.org/licenses/dbcl/1.0/
"""
redis_client = redis.Redis.from_url("redis://localhost:6379/0")
abortable = Abortable(backend=backends.RedisBackend(client=redis_client))
dramatiq.get_broker().add_middleware(abortable)


class ExportTaskRunner(object):
    def run_task(self, job_uid=None, user=None, ondemand=True):  # noqa
        LOG.debug("Running Job with id: {0}".format(job_uid))
        job = Job.objects.get(uid=job_uid)
        if not user:
            user = job.user
        if job.last_run_status != "SUBMITTED" or job.last_run_status != "RUNNING":
            run = ExportRun.objects.create(job=job, user=user, status="SUBMITTED")
            run.save()
            run_uid = str(run.uid)
            LOG.debug("Saved run with id: {0}".format(run_uid))

            for format_name in job.export_formats:
                ExportTask.objects.create(run=run, status="PENDING", name=format_name)
                LOG.debug("Saved task: {0}".format(format_name))

            if HDXExportRegion.objects.filter(job=job).exists():
                ondemand = False  # move hdx jobs to scheduled even though triggered from run now , so that they won't block ondemand queue
            if ondemand:
                # run_task_remote(run_uid)
                # db.close_old_connections()
                send_task = run_task_async_ondemand.send(run_uid)
                run.worker_message_id = send_task.message_id
                run.save()
                LOG.debug(
                    "Worker message saved with task_message_id:{0} ".format(
                        run.worker_message_id
                    )
                )
            else:
                # run_task_remote(run_uid)
                # db.close_old_connections()
                send_task = run_task_async_scheduled.send(run_uid)
                run.worker_message_id = send_task.message_id
                run.save()
                LOG.debug(
                    "Worker message saved with task_message_id:{0} ".format(
                        run.worker_message_id
                    )
                )

            return run
        else:
            LOG.warn(
                "Previous run is on operation already for job: {0}".format(job_uid)
            )
            return None


@dramatiq.actor(
    max_retries=0, queue_name="default", time_limit=1000 * 60 * 60 * 4
)  # 4 hour
def run_task_async_ondemand(run_uid):
    try:
        run_task_remote(run_uid)
    except TimeLimitExceeded:
        run = ExportRun.objects.get(uid=run_uid)
        client.captureException(extra={"run_uid": run_uid})
        LOG.warn("ExportRun {0} failed due to timeout".format(run_uid))
        run.status = "FAILED"
        run.finished_at = timezone.now()
        run.save()
    db.close_old_connections()


@dramatiq.actor(
    max_retries=0, queue_name="scheduled", time_limit=1000 * 60 * 60 * 12
)  #  12 hour
def run_task_async_scheduled(run_uid):
    try:
        run_task_remote(run_uid)
    except TimeLimitExceeded:
        run = ExportRun.objects.get(uid=run_uid)
        client.captureException(extra={"run_uid": run_uid})
        LOG.warn("ExportRun {0} failed due to timeout".format(run_uid))
        run.status = "FAILED"
        run.finished_at = timezone.now()
        run.save()
    db.close_old_connections()


def run_task_remote(run_uid):
    stage_dir = None
    try:
        run = ExportRun.objects.get(uid=run_uid)
        run.status = "RUNNING"
        run.started_at = timezone.now()
        run.save()
        stage_dir = join(settings.EXPORT_STAGING_ROOT, run_uid)
        download_dir = join(settings.EXPORT_DOWNLOAD_ROOT, run_uid)
        if not exists(stage_dir):
            os.makedirs(stage_dir)
        if not exists(download_dir):
            os.makedirs(download_dir)

        run_task(run_uid, run, stage_dir, download_dir)

    except (Job.DoesNotExist, ExportRun.DoesNotExist, ExportTask.DoesNotExist):
        LOG.warn("Job was deleted - exiting.")
    except Exception as ex:
        client.captureException(extra={"run_uid": run_uid})
        run = ExportRun.objects.get(uid=run_uid)
        run.status = "FAILED"
        run.finished_at = timezone.now()
        run.save()

        if HDXExportRegion.objects.filter(job_id=run.job_id).exists():
            send_hdx_error_notification(run, run.job.hdx_export_region_set.first())
        LOG.warn("ExportRun {0} failed: {1}".format(run_uid, ex))
        LOG.warn(traceback.format_exc())
    finally:
        if stage_dir:
            shutil.rmtree(stage_dir)


def run_task(run_uid, run, stage_dir, download_dir):
    LOG.debug("Running ExportRun with id: {0}".format(run_uid))
    job = run.job
    valid_name = get_valid_filename(job.name)

    geom = load_geometry(job.simplified_geom.json)
    export_formats = job.export_formats
    mapping = Mapping(job.feature_selection)

    def start_task(name):
        task = ExportTask.objects.get(run__uid=run_uid, name=name)
        task.status = "RUNNING"
        task.started_at = timezone.now()
        task.save()

    def stop_task(name):
        LOG.debug("Task Failed: {0} for run: {1}".format(name, run_uid))
        task = ExportTask.objects.get(run__uid=run_uid, name=name)
        task.status = "FAILED"
        task.finished_at = timezone.now()
        task.save()

    def format_response(res_item):
        if isinstance(res_item, str):
            return ast.literal_eval(res_item)
        return res_item

    def write_file_size(response):
        LOG.debug("Logging response %s", response)
        if response:
            for item in response:
                item = format_response(item)
                config = configparser.ConfigParser()
                config["FileInfo"] = {"FileSize": str(item["zip_file_size_bytes"])}
                size_path = join(
                    download_dir, f"{item['download_url'].split('/')[-1]}_size.ini"
                )
                with open(size_path, "w") as configfile:
                    config.write(configfile)

    def finish_task(name, created_files=None, response_back=None, planet_file=False):
        LOG.debug("Task Finish: {0} for run: {1}".format(name, run_uid))
        task = ExportTask.objects.get(run__uid=run_uid, name=name)
        task.status = "SUCCESS"
        task.finished_at = timezone.now()
        # assumes each file only has one part (all are zips or PBFs)
        if response_back:
            task.filenames = [
                format_response(item)["download_url"] for item in response_back
            ]
        else:
            task.filenames = [basename(file.parts[0]) for file in created_files]
        if planet_file is False:
            if response_back:
                total_bytes = 0
                for item in response_back:
                    item = format_response(item)
                    total_bytes += int(
                        str(item["zip_file_size_bytes"])
                    )  # getting filesize bytes
                task.filesize_bytes = total_bytes
            else:
                total_bytes = 0
                for file in created_files:
                    total_bytes += file.size()
                task.filesize_bytes = total_bytes
        task.save()

    is_hdx_export = HDXExportRegion.objects.filter(job_id=run.job_id).exists()
    is_partner_export = PartnerExportRegion.objects.filter(job_id=run.job_id).exists()

    planet_file = False
    polygon_centroid = False
    use_only_galaxy = False
    all_feature_filter_json = None

    galaxy_supported_outputs = [
        "geojson",
        "geopackage",
        "kml",
        "shp",
        "fgb",
        "csv",
        "sql",
        "mbtiles",
    ]
    if galaxy_supported_outputs == list(export_formats) or set(export_formats).issubset(
        set(galaxy_supported_outputs)
    ):
        use_only_galaxy = True
        LOG.debug("Using Only Raw Data API to Perform Request")

    if is_hdx_export:
        planet_file = HDXExportRegion.objects.get(job_id=run.job_id).planet_file

    if is_partner_export:
        export_region = PartnerExportRegion.objects.get(job_id=run.job_id)
        planet_file = export_region.planet_file
        polygon_centroid = export_region.polygon_centroid

        # Run PDC special task.
        if (
            export_region.group.name == "PDC"
            and planet_file is True
            and polygon_centroid is True
        ):
            params = {
                "PLANET_FILE": settings.PLANET_FILE,
                "MAPPING": mapping,
                "STAGE_DIR": stage_dir,
                "DOWNLOAD_DIR": download_dir,
                "VALID_NAME": valid_name,
            }

            if "geopackage" not in export_formats:
                raise ValueError("geopackage must be the export format")

            paths = run_pdc_task(params)

            start_task("geopackage")
            target = join(download_dir, "{}.gpkg".format(valid_name))
            shutil.move(paths["geopackage"], target)
            os.chmod(target, 0o644)

            finish_task(
                "geopackage", [osm_export_tool.File("gpkg", [target], "")], planet_file
            )

            send_completion_notification(run)

            run.status = "COMPLETED"
            run.finished_at = timezone.now()
            run.save()
            LOG.debug("Finished ExportRun with id: {0}".format(run_uid))

            return

    if is_hdx_export:
        geopackage = None
        shp = None
        kml = None
        geojson = None
        csv = None
        mapping_filter = mapping
        if job.unfiltered:
            mapping_filter = None

        if settings.USE_RAW_DATA_API_FOR_HDX is False:
            use_only_galaxy = (
                False  # run old format as it as so that galaxy won't interfere
            )

        hdx_supported_galaxy = ["geojson", "shp", "kml", "geopackage", "csv"]
        if hdx_supported_galaxy == list(export_formats) or set(export_formats).issubset(
            set(hdx_supported_galaxy)
        ):
            use_only_galaxy = True  # we don't want to run overpass

        tabular_outputs = []
        if "geojson" in export_formats:
            geojson = Galaxy(
                settings.RAW_DATA_API_URL,
                geom,
                mapping=mapping,
                file_name=valid_name,
                access_token=settings.RAW_DATA_ACCESS_TOKEN,
            )
            start_task("geojson")

        if "geopackage" in export_formats:
            if settings.USE_RAW_DATA_API_FOR_HDX:
                geopackage = Galaxy(
                    settings.RAW_DATA_API_URL,
                    geom,
                    mapping=mapping,
                    file_name=valid_name,
                    access_token=settings.RAW_DATA_ACCESS_TOKEN,
                )
            else:
                geopackage = tabular.MultiGeopackage(
                    join(stage_dir, valid_name), mapping
                )
                tabular_outputs.append(geopackage)
            start_task("geopackage")

        if "shp" in export_formats:
            if settings.USE_RAW_DATA_API_FOR_HDX:
                shp = Galaxy(
                    settings.RAW_DATA_API_URL,
                    geom,
                    mapping=mapping,
                    file_name=valid_name,
                    access_token=settings.RAW_DATA_ACCESS_TOKEN,
                )
            else:
                shp = tabular.Shapefile(join(stage_dir, valid_name), mapping)
                tabular_outputs.append(shp)
            start_task("shp")

        if "kml" in export_formats:
            if settings.USE_RAW_DATA_API_FOR_HDX:
                kml = Galaxy(
                    settings.RAW_DATA_API_URL,
                    geom,
                    mapping=mapping,
                    file_name=valid_name,
                    access_token=settings.RAW_DATA_ACCESS_TOKEN,
                )
            else:
                kml = tabular.Kml(join(stage_dir, valid_name), mapping)
                tabular_outputs.append(kml)
            start_task("kml")

        if "csv" in export_formats:
            csv = Galaxy(
                settings.RAW_DATA_API_URL,
                geom,
                mapping=mapping,
                file_name=valid_name,
                access_token=settings.RAW_DATA_ACCESS_TOKEN,
            )
            start_task("csv")

        if planet_file:
            h = tabular.Handler(
                tabular_outputs, mapping, polygon_centroid=polygon_centroid
            )
            source = OsmiumTool(
                "osmium",
                settings.PLANET_FILE,
                geom,
                join(stage_dir, "extract.osm.pbf"),
                tempdir=stage_dir,
            )

        else:
            if use_only_galaxy == False:
                h = tabular.Handler(
                    tabular_outputs,
                    mapping,
                    clipping_geom=geom,
                    polygon_centroid=polygon_centroid,
                )
                source = Overpass(
                    settings.OVERPASS_API_URL,
                    geom,
                    join(stage_dir, "overpass.osm.pbf"),
                    tempdir=stage_dir,
                    use_curl=True,
                    mapping=mapping_filter,
                )

        if use_only_galaxy == False:
            LOG.debug("Source start for run: {0}".format(run_uid))
            source_path = source.path()
            LOG.debug("Source end for run: {0}".format(run_uid))
            h.apply_file(source_path, locations=True, idx="sparse_file_array")

        all_zips = []

        def add_metadata(z, theme):
            columns = []
            for key in theme.keys:
                columns.append(
                    "{0} http://wiki.openstreetmap.org/wiki/Key:{0}".format(key)
                )
            columns = "\n".join(columns)
            readme = ZIP_README.format(criteria=theme.matcher.to_sql(), columns=columns)
            z.writestr("README.txt", readme)

        if geojson:
            try:
                LOG.debug(
                    "Raw Data API fetch started geojson for run: {0}".format(run_uid)
                )
                response_back = geojson.fetch("geojson", is_hdx_export=True)
                write_file_size(response_back)
                LOG.debug(
                    "Raw Data API fetch ended for geojson run: {0}".format(run_uid)
                )
                finish_task("geojson", response_back=response_back)
                all_zips += response_back
            except Exception as ex:
                stop_task("geojson")
                raise ex

        if csv:
            try:
                LOG.debug("Raw Data API fetch started for csv run: {0}".format(run_uid))
                response_back = csv.fetch("csv", is_hdx_export=True)
                write_file_size(response_back)
                LOG.debug("Raw Data API fetch ended for csv run: {0}".format(run_uid))
                finish_task("csv", response_back=response_back)
                all_zips += response_back

            except Exception as ex:
                stop_task("csv")
                raise ex

        if geopackage:
            try:
                if settings.USE_RAW_DATA_API_FOR_HDX:
                    LOG.debug(
                        "Raw Data API fetch started for geopackage run: {0}".format(
                            run_uid
                        )
                    )
                    response_back = geopackage.fetch("gpkg", is_hdx_export=True)
                    write_file_size(response_back)
                    LOG.debug(
                        "Raw Data API fetch ended for geopackage run: {0}".format(
                            run_uid
                        )
                    )
                    finish_task("geopackage", response_back=response_back)
                    all_zips += response_back

                else:
                    geopackage.finalize()
                    zips = []
                    for theme in mapping.themes:
                        destination = join(
                            download_dir,
                            valid_name + "_" + slugify(theme.name) + "_gpkg.zip",
                        )
                        matching_files = [
                            f
                            for f in geopackage.files
                            if "theme" in f.extra and f.extra["theme"] == theme.name
                        ]
                        with zipfile.ZipFile(
                            destination, "w", zipfile.ZIP_DEFLATED, True
                        ) as z:
                            add_metadata(z, theme)
                            for file in matching_files:
                                for part in file.parts:
                                    z.write(part, os.path.basename(part))
                        zips.append(
                            osm_export_tool.File(
                                "geopackage", [destination], {"theme": theme.name}
                            )
                        )
                    finish_task("geopackage", zips)
                    all_zips += zips
            except Exception as ex:
                stop_task("geopackage")
                raise ex

        if shp:
            try:
                if settings.USE_RAW_DATA_API_FOR_HDX:
                    LOG.debug(
                        "Raw Data API fetch started for shp run: {0}".format(run_uid)
                    )

                    response_back = shp.fetch("shp", is_hdx_export=True)
                    write_file_size(response_back)
                    LOG.debug(
                        "Raw Data API fetch ended  for shp run: {0}".format(run_uid)
                    )
                    finish_task("shp", response_back=response_back)
                    all_zips += response_back
                else:
                    shp.finalize()
                    zips = []
                    for file in shp.files:
                        # for HDX geopreview to work
                        # each file (_polygons, _lines) is a separate zip resource
                        # the zipfile must end with only .zip (not .shp.zip)
                        destination = join(
                            download_dir,
                            os.path.basename(file.parts[0]).replace(".", "_") + ".zip",
                        )
                        with zipfile.ZipFile(
                            destination, "w", zipfile.ZIP_DEFLATED, True
                        ) as z:
                            theme = [
                                t
                                for t in mapping.themes
                                if t.name == file.extra["theme"]
                            ][0]
                            add_metadata(z, theme)
                            for part in file.parts:
                                z.write(part, os.path.basename(part))
                        zips.append(
                            osm_export_tool.File(
                                "shp", [destination], {"theme": file.extra["theme"]}
                            )
                        )
                    finish_task("shp", zips)
                    all_zips += zips
            except Exception as ex:
                stop_task("shp")
                raise ex
        if kml:
            try:
                if settings.USE_RAW_DATA_API_FOR_HDX:
                    LOG.debug(
                        "Raw Data API fetch started for kml run: {0}".format(run_uid)
                    )
                    response_back = kml.fetch("kml", is_hdx_export=True)
                    write_file_size(response_back)
                    LOG.debug(
                        "Raw Data API fetch ended for kml run: {0}".format(run_uid)
                    )
                    finish_task("kml", response_back=response_back)
                    all_zips += response_back

                else:  # use overpass
                    kml.finalize()
                    zips = []
                    for file in kml.files:
                        destination = join(
                            download_dir,
                            os.path.basename(file.parts[0]).replace(".", "_") + ".zip",
                        )
                        with zipfile.ZipFile(
                            destination, "w", zipfile.ZIP_DEFLATED, True
                        ) as z:
                            theme = [
                                t
                                for t in mapping.themes
                                if t.name == file.extra["theme"]
                            ][0]
                            add_metadata(z, theme)
                            for part in file.parts:
                                z.write(part, os.path.basename(part))
                        zips.append(
                            osm_export_tool.File(
                                "kml", [destination], {"theme": file.extra["theme"]}
                            )
                        )
                    finish_task("kml", zips)
                    all_zips += zips
            except Exception as ex:
                stop_task("kml")
                raise ex

        if "garmin_img" in export_formats:
            start_task("garmin_img")
            try:
                garmin_files = nontabular.garmin(
                    source_path,
                    settings.GARMIN_SPLITTER,
                    settings.GARMIN_MKGMAP,
                    tempdir=stage_dir,
                )
                zipped = create_package(
                    join(download_dir, valid_name + "_gmapsupp_img.zip"),
                    garmin_files,
                    boundary_geom=geom,
                    output_name="garmin_img",
                )
                all_zips.append(zipped)
                finish_task("garmin_img", [zipped])
            except Exception as ex:
                stop_task("garmin_img")
                raise ex

        if settings.SYNC_TO_HDX:
            LOG.debug("Syncing to HDX for run: {0}".format(run_uid))
            region = HDXExportRegion.objects.get(job_id=run.job_id)
            try:
                public_dir = settings.HOSTNAME + join(
                    settings.EXPORT_MEDIA_ROOT, run_uid
                )
                sync_region(region, all_zips, public_dir)
                run.hdx_sync_status = True
            except Exception as ex:
                run.sync_status = False
                LOG.error(ex)
        send_hdx_completion_notification(run, run.job.hdx_export_region_set.first())
    else:
        geopackage = None
        shp = None
        kml = None
        geojson = None
        fgb = None
        csv = None
        sql = None
        tabular_outputs = []
        mapping_filter = mapping
        if job.unfiltered:
            mapping_filter = None

        if "geojson" in export_formats:
            preserved_geom = geom
            if job.preserve_geom:
                preserved_geom = load_geometry(job.the_geom.json)
            geojson = Galaxy(
                settings.RAW_DATA_API_URL,
                preserved_geom,
                mapping=mapping_filter,
                file_name=valid_name,
                access_token=settings.RAW_DATA_ACCESS_TOKEN,
            )
            start_task("geojson")

        if "fgb" in export_formats:
            fgb = Galaxy(
                settings.RAW_DATA_API_URL,
                geom,
                mapping=mapping_filter,
                file_name=valid_name,
                access_token=settings.RAW_DATA_ACCESS_TOKEN,
            )
            start_task("fgb")

        if "csv" in export_formats:
            csv = Galaxy(
                settings.RAW_DATA_API_URL,
                geom,
                mapping=mapping_filter,
                file_name=valid_name,
                access_token=settings.RAW_DATA_ACCESS_TOKEN,
            )
            start_task("csv")

        if "sql" in export_formats:
            sql = Galaxy(
                settings.RAW_DATA_API_URL,
                geom,
                mapping=mapping_filter,
                file_name=valid_name,
                access_token=settings.RAW_DATA_ACCESS_TOKEN,
            )
            start_task("sql")

        if "geopackage" in export_formats:
            geopackage = Galaxy(
                settings.RAW_DATA_API_URL,
                geom,
                mapping=mapping_filter,
                file_name=valid_name,
                access_token=settings.RAW_DATA_ACCESS_TOKEN,
            )
            # geopackage = tabular.Geopackage(join(stage_dir,valid_name),mapping)
            # tabular_outputs.append(geopackage)
            start_task("geopackage")

        if "shp" in export_formats:
            shp = Galaxy(
                settings.RAW_DATA_API_URL,
                geom,
                mapping=mapping_filter,
                file_name=valid_name,
                access_token=settings.RAW_DATA_ACCESS_TOKEN,
            )
            start_task("shp")

        if "kml" in export_formats:
            kml = Galaxy(
                settings.RAW_DATA_API_URL,
                geom,
                mapping=mapping_filter,
                file_name=valid_name,
                access_token=settings.RAW_DATA_ACCESS_TOKEN,
            )
            # kml = tabular.Kml(join(stage_dir,valid_name),mapping)
            # tabular_outputs.append(kml)
            start_task("kml")
        if planet_file:
            h = tabular.Handler(
                tabular_outputs, mapping, polygon_centroid=polygon_centroid
            )
            source = OsmiumTool(
                "osmium",
                settings.PLANET_FILE,
                geom,
                join(stage_dir, "extract.osm.pbf"),
                tempdir=stage_dir,
                mapping=mapping,
            )
        else:
            if use_only_galaxy == False:
                h = tabular.Handler(
                    tabular_outputs,
                    mapping,
                    clipping_geom=geom,
                    polygon_centroid=polygon_centroid,
                )
                source = Overpass(
                    settings.OVERPASS_API_URL,
                    geom,
                    join(stage_dir, "overpass.osm.pbf"),
                    tempdir=stage_dir,
                    use_curl=True,
                    mapping=mapping_filter,
                )

        bundle_files = []

        if geojson:
            try:
                LOG.debug(
                    "Raw Data API fetch started for geojson run: {0}".format(run_uid)
                )
                all_feature_filter_json = join(
                    os.getcwd(), "tasks/tests/fixtures/all_features_filters.json"
                )
                response_back = geojson.fetch(
                    "geojson", all_feature_filter_json=all_feature_filter_json
                )
                write_file_size(response_back)

                LOG.debug(
                    "Raw Data API fetch ended for geojson run: {0}".format(run_uid)
                )
                finish_task("geojson", response_back=response_back)
            except Exception as ex:
                stop_task("geojson")
                raise ex

        if fgb:
            try:
                LOG.debug("Raw Data API fetch started for fgb run: {0}".format(run_uid))
                all_feature_filter_json = join(
                    os.getcwd(), "tasks/tests/fixtures/all_features_filters.json"
                )
                response_back = fgb.fetch(
                    "fgb", all_feature_filter_json=all_feature_filter_json
                )
                write_file_size(response_back)
                LOG.debug("Raw Data API fetch ended for fgb run: {0}".format(run_uid))
                finish_task("fgb", response_back=response_back)
            except Exception as ex:
                stop_task("fgb")
                raise ex

        if csv:
            try:
                LOG.debug("Raw Data API fetch started for csv run: {0}".format(run_uid))
                all_feature_filter_json = join(
                    os.getcwd(), "tasks/tests/fixtures/all_features_filters.json"
                )
                response_back = csv.fetch(
                    "csv", all_feature_filter_json=all_feature_filter_json
                )
                write_file_size(response_back)
                LOG.debug("Raw Data API fetch ended for csv run: {0}".format(run_uid))
                finish_task("csv", response_back=response_back)
            except Exception as ex:
                stop_task("csv")
                raise ex

        if sql:
            try:
                LOG.debug("Raw Data API fetch started for sql run: {0}".format(run_uid))
                all_feature_filter_json = join(
                    os.getcwd(), "tasks/tests/fixtures/all_features_filters.json"
                )
                response_back = sql.fetch(
                    "sql", all_feature_filter_json=all_feature_filter_json
                )
                write_file_size(response_back)
                LOG.debug("Raw Data API fetch ended for sql run: {0}".format(run_uid))
                finish_task("sql", response_back=response_back)
            except Exception as ex:
                stop_task("sql")
                raise ex

        if geopackage:
            try:
                LOG.debug(
                    "Raw Data API fetch started for geopackage run: {0}".format(run_uid)
                )
                all_feature_filter_json = join(
                    os.getcwd(), "tasks/tests/fixtures/all_features_filters.json"
                )
                response_back = geopackage.fetch(
                    "gpkg", all_feature_filter_json=all_feature_filter_json
                )
                write_file_size(response_back)
                LOG.debug(
                    "Raw Data API fetch ended for geopackage run: {0}".format(run_uid)
                )
                finish_task("geopackage", response_back=response_back)
            except Exception as ex:
                stop_task("geopackage")
                raise ex

        if shp:
            try:
                LOG.debug(
                    "Raw Data API fetch started for shp run:  {0}".format(run_uid)
                )
                response_back = shp.fetch(
                    "shp", all_feature_filter_json=all_feature_filter_json
                )
                write_file_size(response_back)
                LOG.debug("Raw Data API fetch ended for shp run:  {0}".format(run_uid))
                finish_task("shp", response_back=response_back)
            except Exception as ex:
                stop_task("shp")
                raise ex

        if kml:
            try:
                LOG.debug("Raw Data API fetch started for kml run: {0}".format(run_uid))
                all_feature_filter_json = join(
                    os.getcwd(), "tasks/tests/fixtures/all_features_filters.json"
                )
                response_back = kml.fetch(
                    "kml", all_feature_filter_json=all_feature_filter_json
                )
                write_file_size(response_back)
                LOG.debug("Raw Data API fetch ended for kml run: {0}".format(run_uid))
                finish_task("kml", response_back=response_back)

            except Exception as ex:
                stop_task("kml")
                raise ex

        if "mbtiles" in export_formats:
            try:
                mbtiles = Galaxy(
                    settings.RAW_DATA_API_URL,
                    geom,
                    mapping=mapping_filter,
                    file_name=valid_name,
                    access_token=settings.RAW_DATA_ACCESS_TOKEN,
                )
                start_task("mbtiles")
                LOG.debug(
                    "Raw Data API fetch started for mbtiles run: {0}".format(run_uid)
                )
                all_feature_filter_json = join(
                    os.getcwd(), "tasks/tests/fixtures/all_features_filters.json"
                )
                response_back = mbtiles.fetch(
                    "mbtiles",
                    all_feature_filter_json=all_feature_filter_json,
                    min_zoom=job.mbtiles_minzoom,
                    max_zoom=job.mbtiles_maxzoom,
                )
                write_file_size(response_back)
                LOG.debug(
                    "Raw Data API fetch ended for mbtiles run: {0}".format(run_uid)
                )
                finish_task("mbtiles", response_back=response_back)

            except Exception as ex:
                stop_task("mbtiles")
                raise ex

        if use_only_galaxy == False:
            LOG.debug("Source start for run: {0}".format(run_uid))
            source_path = source.path()
            LOG.debug("Source end for run: {0}".format(run_uid))

            h.apply_file(source_path, locations=True, idx="sparse_file_array")

        if "garmin_img" in export_formats:
            start_task("garmin_img")
            try:
                garmin_files = nontabular.garmin(
                    source_path,
                    settings.GARMIN_SPLITTER,
                    settings.GARMIN_MKGMAP,
                    tempdir=stage_dir,
                )
                bundle_files += garmin_files
                zipped = create_package(
                    join(download_dir, valid_name + "_gmapsupp_img.zip"),
                    garmin_files,
                    boundary_geom=geom,
                )
                finish_task("garmin_img", [zipped])
            except Exception as ex:
                stop_task("garmin_img")
                raise ex

        if "mwm" in export_formats:
            start_task("mwm")
            try:
                mwm_dir = join(stage_dir, "mwm")
                if not exists(mwm_dir):
                    os.makedirs(mwm_dir)
                mwm_files = nontabular.mwm(
                    source_path, mwm_dir, settings.GENERATE_MWM, settings.GENERATOR_TOOL
                )
                bundle_files += mwm_files
                zipped = create_package(
                    join(download_dir, valid_name + "_mwm.zip"),
                    mwm_files,
                    boundary_geom=geom,
                )
                finish_task("mwm", [zipped])
            except Exception as ex:
                stop_task("garmin_img")
                raise ex

        if "osmand_obf" in export_formats:
            start_task("osmand_obf")
            try:
                osmand_files = nontabular.osmand(
                    source_path, settings.OSMAND_MAP_CREATOR_DIR, tempdir=stage_dir
                )
                bundle_files += osmand_files
                zipped = create_package(
                    join(download_dir, valid_name + "_Osmand2_obf.zip"),
                    osmand_files,
                    boundary_geom=geom,
                )
                finish_task("osmand_obf", [zipped])
            except Exception as ex:
                stop_task("osmand_obf")
                raise ex

        if "osm_pbf" in export_formats:
            bundle_files += [osm_export_tool.File("osm_pbf", [source_path], "")]

        if "bundle" in export_formats:
            start_task("bundle")
            try:
                zipped = create_posm_bundle(
                    join(download_dir, valid_name + "-bundle.tar.gz"),
                    bundle_files,
                    job.name,
                    valid_name,
                    job.description,
                    geom,
                )
                finish_task("bundle", [zipped])
            except Exception as ex:
                stop_task("bundle")
                raise ex

        # do this last so we can do a mv instead of a copy
        if "osm_pbf" in export_formats:
            start_task("osm_pbf")
            try:
                target = join(download_dir, valid_name + ".osm.pbf")
                shutil.move(source_path, target)
                os.chmod(target, 0o644)
                finish_task(
                    "osm_pbf", [osm_export_tool.File("pbf", [target], "")], planet_file
                )
            except Exception as ex:
                stop_task("osm_pbf")
                raise ex
        send_completion_notification(run)

    run.status = "COMPLETED"
    run.finished_at = timezone.now()
    run.save()
    LOG.debug("Finished ExportRun with id: {0}".format(run_uid))
