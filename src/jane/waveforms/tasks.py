# -*- coding: utf-8 -*-

import io
import json
import gc
import os

from celery import shared_task
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from psycopg2.extras import DateTimeTZRange

import matplotlib
matplotlib.use("agg")  # NOQA
import matplotlib.pylab as plt

from obspy.core import read
from obspy.core.preview import createPreview

from jane.exceptions import JaneException
from jane.waveforms import models

from .utils import to_datetime


class JaneWaveformTaskException(JaneException):
    pass


@shared_task
def process_file(filename):
    """
    Process a single waveform file.
    """
    filename = os.path.normpath(os.path.abspath(filename))

    try:
        file = models.File.objects.get(
            path__name=os.path.dirname(filename),
            name=os.path.basename(filename))

        # This path is only reached if the file exists. Check size, mtime,
        # and ctime and if it all remains the same, return.
        stats = os.stat(filename)
        mtime = to_datetime(stats.st_mtime)
        ctime = to_datetime(stats.st_ctime)
        size = int(stats.st_size)
        if file.size == size and file.mtime == mtime and file.ctime == ctime:
            return
        else:
            file.delete()
    except models.File.DoesNotExist:
        pass

    # Path object
    path_obj = models.Path.objects.get_or_create(
        name=os.path.dirname(os.path.abspath(filename)))[0]

    # Will raise a proper exception if not a waveform file.
    stream = read(filename)

    if len(stream) == 0:
        msg = "'%s' is a valid waveform file but contains no actual data"
        raise JaneWaveformTaskException(msg % filename)
    models.File.objects.filter(
        path=path_obj, name=os.path.basename(filename)).delete()
    file_obj = models.File.objects.create(
        path=path_obj, name=os.path.basename(filename))

    # set format
    file_obj.format = stream[0].stats._format

    # get number of gaps and overlaps per file
    gap_list = stream.getGaps()
    file_obj.gaps = len([g for g in gap_list if g[6] >= 0])
    file_obj.overlaps = len([g for g in gap_list if g[6] < 0])
    file_obj.save()

    pos = 0
    for trace in stream:
        trace_obj = models.ContinuousTrace.objects.get_or_create(
            file=file_obj,
            timerange=DateTimeTZRange(
                lower=trace.stats.starttime.datetime,
                upper=trace.stats.endtime.datetime))[0]
        trace_obj.network = trace.stats.network.upper()
        trace_obj.station = trace.stats.station.upper()
        trace_obj.location = trace.stats.location.upper()
        trace_obj.channel = trace.stats.channel.upper()
        trace_obj.calib = trace.stats.calib
        trace_obj.sampling_rate = trace.stats.sampling_rate
        trace_obj.npts = trace.stats.npts
        trace_obj.duration = trace.stats.endtime - trace.stats.starttime
        try:
            trace_obj.quality = trace.stats.mseed.dataquality
        except:
            pass

        # preview image
        try:
            # Always attempt to close figures to get no memory leaks.
            try:
                plt.close("all")
            except:
                pass
            with io.BytesIO() as plot:
                trace.plot(format="png", outfile=plot)
                plot.seek(0, 0)
                trace_obj.preview_image = plot.read()
            # Always attempt to close figures to get no memory leaks.
            try:
                plt.close("all")
            except:
                pass
        except:
            pass

        # preview trace - replace any masked values with 0
        if hasattr(trace.data, 'filled'):
            trace.data.filled(0)
        try:
            preview_trace = createPreview(trace, 60)
            trace_obj.preview_trace = json.dumps(preview_trace.data.tolist())
        except:
            pass

        trace_obj.pos = pos
        trace_obj.save()
        pos += 1
        # Ease the work for the garbage collector. For some reason this
        # likes to leak when run with celery.
        del trace
        del preview_trace
    # Ease the work for the garbage collector. For some reason this
    # likes to leak when run with celery.
    del stream
    gc.collect()


def _format_return_value(event, message):
    return "Filemon event type: {event_type}, Result: {message}, Input: {" \
           "event}".format(event_type=event["event_type"], message=message,
                           event=str(event))


@shared_task
def filemon_event(event):
    """
    Handle file monitor events
    """
    # Possible event types: created, deleted, modified, moved
    event_type = event['event_type']
    is_directory = event["is_directory"]

    # Assertions to gain trust in the async flow.
    assert event_type in ("created", "modified", "deleted", "moved")
    assert is_directory in (True, False)

    # Deal with files first.
    if is_directory is False:
        src_path = event['src_path']
        src_file = os.path.basename(src_path)
        src_folder = os.path.dirname(src_path)

        if event_type in ("created", "modified"):
            # New or modified file.
            process_file.delay(filename=src_path)
            return _format_return_value(event, "File sent to processing.")
        # Delete file object if file has been deleted.
        elif event_type == "deleted":
            try:
                models.File.objects.get(path__name=src_folder,
                                        name=src_file).delete()
                return _format_return_value(event, "File deleted.")
            except ObjectDoesNotExist:
                return _format_return_value(event, "File already deleted.")
        elif event_type == "moved":
            dest_file = os.path.basename(event['dest_path'])
            dest_folder = os.path.dirname(event['dest_path'])

            # Nothing happened.
            if src_path == event["dest_path"]:
                return _format_return_value(event, "File not moved.")

            with transaction.atomic():
                dest_path_obj = models.Path.objects.get_or_create(
                    name=dest_folder)[0]
                dest_path_obj.save()

                src_file_obj = models.File.objects.get(
                    path__name=src_folder, name=src_file)

                src_file_obj.name = dest_file
                src_file_obj.path = dest_path_obj
                src_file_obj.save()

            # Check if the src_path has files left in it. If not, try to
            # delete it.
            try:
                src_path_obj = models.Path.objects.get(name=src_folder)
            except ObjectDoesNotExist:
                return _format_return_value(event, "File moved, path already "
                                                   "deleted.")

            if src_path_obj.files.count() == 0:
                try:
                    src_path_obj.delete()
                except AssertionError:
                    return _format_return_value(event, "File moved, deleting "
                                                       "path failed.")
                return _format_return_value(event, "File moved, old path "
                                                   "deleted.")
            return _format_return_value(event, "File moved, path untouched.")
        # Should not happen.
        else:
            raise JaneWaveformTaskException(
                "Invalid watchdog event type: '%s'" % event_type)
    # Deal with directories.
    else:
        src_folder = os.path.abspath(event['src_path'])
        if event_type == "deleted":
            try:
                models.Path.objects.get(name=src_folder).delete()
                return _format_return_value(event, "Deleted path.")
            except ObjectDoesNotExist:
                return _format_return_value(event, "Failed deleting path.")
        elif event_type == "moved":
            # Only deal with it if the directory actually exists in the
            # database.
            try:
                path_obj = models.Path.objects.get(name=src_folder)
            except ObjectDoesNotExist:
                return _format_return_value(event, "File could not be moved.")
            # If it does, just update the path.
            path_obj.name = os.path.abspath(event['dest_path'])
            path_obj.save()
            return _format_return_value(event, "Moved path.")
        # Should not happen. Modified and created directories are not passed
        # to the task queue.
        else:
            raise JaneWaveformTaskException(
                "Invalid watchdog event type: '%s'" % event_type)


@shared_task
def index_path(path, debug=False, delete_files=False):
    """
    Index given path
    """
    # convert to absolute path
    path = os.path.abspath(path)
    if delete_files:
        if debug:
            print("Purging %s ..." % (path))
        # delete all paths and files which start with path
        models.Path.objects.filter(name__startswith=path).delete()
    # indexing
    if debug:
        print("Indexing %s ..." % (path))
    for root, _, files in os.walk(path):
        # index each file
        for file in files:
            # direct
            if debug:
                print("\tFile %s..." % (os.path.join(root, file)))
                process_file(os.path.join(root, file))
            # use celery
            else:
                process_file.delay(os.path.join(root, file))
    # indexing
    if debug:
        print("Indexing finished")
