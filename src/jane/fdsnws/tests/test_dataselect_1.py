# -*- coding: utf-8 -*-

import base64
import io
import os

import django
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.test import TestCase, LiveServerTestCase
import numpy
from obspy import read, UTCDateTime
from obspy.clients.fdsn import Client as FDSNClient

from jane.waveforms.models import Restriction
from jane.waveforms.process_waveforms import process_file


django.setup()


PATH = os.path.join(os.path.dirname(__file__), 'data')
FILES = [
    os.path.join(PATH, 'RJOB_061005_072159.ehz.new.mseed'),
    os.path.join(PATH, 'TA.A25A.mseed')
]


class DataSelect1TestCase(TestCase):

    def setUp(self):
        # index waveform files
        [process_file(f) for f in FILES]
        User.objects.get_or_create(username='random',
                                   password=make_password('random'))

        credentials = base64.b64encode(b'random:random')
        self.valid_auth_headers = {
            'HTTP_AUTHORIZATION': 'Basic ' + credentials.decode("ISO-8859-1")
        }

        credentials = base64.b64encode(b'random:random2')
        self.invalid_auth_headers = {
            'HTTP_AUTHORIZATION': 'Basic ' + credentials.decode("ISO-8859-1")
        }

    def test_version(self):
        # 1 - HTTP OK
        response = self.client.get('/fdsnws/dataselect/1/version')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'], 'text/plain')
        self.assertEqual(response.content, b'1.1.1')
        # 2 - incorrect trailing slash will work too
        response = self.client.get('/fdsnws/dataselect/1/version/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'], 'text/plain')
        self.assertEqual(response.content, b'1.1.1')

    def test_wadl(self):
        # 1 - HTTP OK
        response = self.client.get('/fdsnws/dataselect/1/application.wadl')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'],
                         'application/xml; charset=utf-8')
        self.assertTrue(response.content.startswith(b'<?xml'))
        # 2 - incorrect trailing slash will work too
        response = self.client.get('/fdsnws/dataselect/1/application.wadl/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'],
                         'application/xml; charset=utf-8')
        self.assertTrue(response.content.startswith(b'<?xml'))

    def test_index(self):
        # 1 - redirect if APPEND_SLASH = True
        response = self.client.get('/fdsnws/dataselect/1')
        self.assertEqual(response.status_code, 301)
        # 2 - HTTP OK
        response = self.client.get('/fdsnws/dataselect/1/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['content-type'], 'text/html; charset=utf-8')

    def test_query(self):
        # 1 - start time must be specified
        param = '?'
        response = self.client.get('/fdsnws/dataselect/1/query' + param)
        self.assertEqual(response.status_code, 400)
        self.assertTrue('Start time must be specifi' in response.reason_phrase)
        # 2 - start time must be parseable
        param = '?start=0'
        response = self.client.get('/fdsnws/dataselect/1/query' + param)
        self.assertEqual(response.status_code, 400)
        self.assertTrue('Error parsing starttime' in response.reason_phrase)
        # 3 - end time must be specified
        param = '?start=2012-01-01'
        response = self.client.get('/fdsnws/dataselect/1/query' + param)
        self.assertEqual(response.status_code, 400)
        self.assertTrue('End time must be specified' in response.reason_phrase)
        # 4 - end time must be parseable
        param = '?start=2012-01-01&end=0'
        response = self.client.get('/fdsnws/dataselect/1/query' + param)
        self.assertEqual(response.status_code, 400)
        self.assertTrue('Error parsing endtime' in response.reason_phrase)
        # 5 - start time must before endtime
        param = '?start=2012-01-01&end=2012-01-01'
        response = self.client.get('/fdsnws/dataselect/1/query' + param)
        self.assertEqual(response.status_code, 400)
        self.assertTrue('Start time must be before end time' in
                        response.reason_phrase)
        param = '?start=2012-01-02&end=2012-01-01'
        response = self.client.get('/fdsnws/dataselect/1/query' + param)
        self.assertEqual(response.status_code, 400)
        self.assertTrue('Start time must be before end time' in
                        response.reason_phrase)

    def test_query_nodata(self):
        # not existing - error 204
        param = '?start=2012-01-01&end=2012-01-02&net=GE&sta=APE&cha=EHE'
        response = self.client.get('/fdsnws/dataselect/1/query' + param)
        self.assertEqual(response.status_code, 204)
        self.assertTrue('Not Found: No data' in response.reason_phrase)
        # not existing - error 404
        param += '&nodata=404'
        response = self.client.get('/fdsnws/dataselect/1/query' + param)
        self.assertEqual(response.status_code, 404)
        self.assertTrue('Not Found: No data' in response.reason_phrase)

    def test_query_data(self):
        expected = read(FILES[0])[0]
        params = {
            'station': expected.meta.station,
            'cha': expected.meta.channel,
            'start': expected.meta.starttime,
            'end': expected.meta.endtime
        }
        # 1 - query using HTTP GET
        response = self.client.get('/fdsnws/dataselect/1/query', params)
        self.assertEqual(response.status_code, 200)
        self.assertTrue('OK' in response.reason_phrase)
        # compare streams
        got = read(io.BytesIO(response.getvalue()))[0]
        numpy.testing.assert_equal(got.data, expected.data)
        self.assertEqual(got, expected)
        # 2 - query using HTTP POST
        response = self.client.post('/fdsnws/dataselect/1/query', params)
        self.assertEqual(response.status_code, 200)
        self.assertTrue('OK' in response.reason_phrase)
        # compare streams
        got = read(io.BytesIO(response.getvalue()))[0]
        numpy.testing.assert_equal(got.data, expected.data)
        self.assertEqual(got, expected)

    def test_queryauth_nodata(self):
        param = '?start=2012-01-01&end=2012-01-02&net=GE&sta=APE&cha=EHE'

        # 1 - no credentials - error 401
        response = self.client.get('/fdsnws/dataselect/1/queryauth' + param)
        self.assertEqual(response.status_code, 401)

        # 2 - invalid credentials - error 401
        response = self.client.get('/fdsnws/dataselect/1/queryauth' + param,
                                   **self.invalid_auth_headers)
        self.assertEqual(response.status_code, 401)

        # 3 - valid credentials - not existing - error 204
        response = self.client.get('/fdsnws/dataselect/1/queryauth' + param,
                                   **self.valid_auth_headers)
        self.assertEqual(response.status_code, 204)
        self.assertTrue('Not Found: No data' in response.reason_phrase)

        # 4 - valid credentials - not existing - error 404
        param += '&nodata=404'
        response = self.client.get('/fdsnws/dataselect/1/queryauth' + param,
                                   **self.valid_auth_headers)
        self.assertEqual(response.status_code, 404)
        self.assertTrue('Not Found: No data' in response.reason_phrase)

    def test_queryauth_data(self):
        expected = read(FILES[0])[0]
        params = {
            'station': expected.meta.station,
            'cha': expected.meta.channel,
            'start': expected.meta.starttime,
            'end': expected.meta.endtime,
        }

        # 1 - no credentials GET - error 401
        response = self.client.get('/fdsnws/dataselect/1/queryauth', params)
        self.assertEqual(response.status_code, 401)

        # 2 - invalid credentials GET - error 401
        response = self.client.get('/fdsnws/dataselect/1/queryauth', params,
                                   **self.invalid_auth_headers)
        self.assertEqual(response.status_code, 401)

        # 3 - no credentials POST - error 401
        response = self.client.post('/fdsnws/dataselect/1/queryauth', params)
        self.assertEqual(response.status_code, 401)

        # 4 - invalid credentials POST - error 401
        response = self.client.post('/fdsnws/dataselect/1/queryauth', params,
                                    **self.invalid_auth_headers)
        self.assertEqual(response.status_code, 401)

        # 5 - query using HTTP GET
        response = self.client.get('/fdsnws/dataselect/1/queryauth', params,
                                   **self.valid_auth_headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue('OK' in response.reason_phrase)

        # compare streams
        got = read(io.BytesIO(response.getvalue()))[0]
        numpy.testing.assert_equal(got.data, expected.data)
        self.assertEqual(got, expected)

        # 6 - query using HTTP POST
        response = self.client.post('/fdsnws/dataselect/1/queryauth', params,
                                    **self.valid_auth_headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue('OK' in response.reason_phrase)

        # compare streams
        got = read(io.BytesIO(response.getvalue()))[0]
        numpy.testing.assert_equal(got.data, expected.data)
        self.assertEqual(got, expected)

    def test_query_data_wildcards(self):
        # query using wildcards
        param = '?endtime=2010-03-25T00:00:30&network=TA&channel=BH%2A' + \
            '&starttime=2010-03-25&station=A25A'
        response = self.client.get('/fdsnws/dataselect/1/query' + param)
        self.assertEqual(response.status_code, 200)
        self.assertTrue('OK' in response.reason_phrase)

    def test_restrictions(self):
        """
        Tests if the waveform restrictions actually work as expected.
        """
        params = {
            'station': 'A25A',
            'cha': 'BHE',
            'start': '2010-03-25T00:00:00',
            'end': '2010-03-26T00:00:00'
        }

        # No restrictions currently apply - we should get something.
        response = self.client.get('/fdsnws/dataselect/1/query', params)
        self.assertEqual(response.status_code, 200)
        self.assertTrue('OK' in response.reason_phrase)
        st = read(io.BytesIO(response.getvalue()))
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0].id, "TA.A25A..BHE")

        # Now add restrictions to this one station.
        # create anonymous user
        r = Restriction.objects.get_or_create(network="TA", station="A25A")[0]
        r.users.add(User.objects.filter(username='random')[0])
        r.save()

        # Now the same query should no longer return something as the
        # station has been restricted.
        response = self.client.get('/fdsnws/dataselect/1/query', params)
        self.assertEqual(response.status_code, 204)

        # RJOB data can still be retrieved.
        params["station"] = "RJOB"
        params["cha"] = "Z"
        params["start"] = "2005-01-01T00:00:00"
        response = self.client.get('/fdsnws/dataselect/1/query', params)
        self.assertEqual(response.status_code, 200)
        self.assertTrue('OK' in response.reason_phrase)
        st = read(io.BytesIO(response.getvalue()))
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0].id, ".RJOB..Z")

        # The correct user can still everything
        params = {
            'station': 'A25A',
            'cha': 'BHE',
            'start': '2010-03-25T00:00:00',
            'end': '2010-03-26T00:00:00'
        }

        p = {}
        p.update(params)
        p.update(self.valid_auth_headers)
        response = self.client.get('/fdsnws/dataselect/1/queryauth', **p)
        self.assertEqual(response.status_code, 200)
        self.assertTrue('OK' in response.reason_phrase)
        st = read(io.BytesIO(response.getvalue()))
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0].id, "TA.A25A..BHE")

        # Make another user that has not been added to this restriction - he
        # should not be able to retrieve it.
        self.client.logout()
        User.objects.get_or_create(
            username='some_dude', password=make_password('some_dude'))[0]
        credentials = base64.b64encode(b'some_dude:some_dude')
        auth_headers = {
            'HTTP_AUTHORIZATION': 'Basic ' + credentials.decode("ISO-8859-1")
        }
        p = {}
        p.update(params)
        p.update(auth_headers)
        response = self.client.get('/fdsnws/dataselect/1/queryauth', **p)
        self.assertEqual(response.status_code, 204)


class DataSelect1LiveServerTestCase(LiveServerTestCase):
    """
    Launches a live Django server in the background on setup, and shuts it down
    on teardown. This allows the use of automated test clients other than the
    Django dummy client such as obspy.clients.fdsn.Client.
    """

    def setUp(self):
        # index waveform files
        [process_file(f) for f in FILES]

    def test_query_data(self):
        # query using ObsPy
        t1 = UTCDateTime("2005-10-06T07:21:59.850000")
        t2 = UTCDateTime("2005-10-06T07:24:59.845000")
        client = FDSNClient(self.live_server_url)
        got = client.get_waveforms("", "RJOB", "", "Z", t1, t2)[0]
        expected = read(FILES[0])[0]
        numpy.testing.assert_equal(got.data, expected.data)
        self.assertEqual(got, expected)

    def test_query_data_wildcards(self):
        # query using wildcards
        t = UTCDateTime(2010, 3, 25, 0, 0)
        client = FDSNClient(self.live_server_url)
        # 1
        st = client.get_waveforms("TA", "A25A", "", "BHZ", t, t + 30)
        self.assertEqual(len(st), 1)
        self.assertEqual(len(st[0].data), 1201)
        self.assertEqual(st[0].id, 'TA.A25A..BHZ')
        # 2
        st = client.get_waveforms("TA", "A25A", "", "BHZ,BHN,BHE", t, t + 30)
        self.assertEqual(len(st), 3)
        # 3
        st = client.get_waveforms("TA", "A25A", "", "BH*", t, t + 30)
        self.assertEqual(len(st), 3)
        # 4
        st = client.get_waveforms("TA", "A25A", "", "BH?", t, t + 30)
        self.assertEqual(len(st), 3)
        # 5
        st = client.get_waveforms("TA", "A25A", "", "BH?,VCO", t, t + 30)
        self.assertEqual(len(st), 4)
        # 6
        st = client.get_waveforms("TA", "A25A", "", "BH?,-BHZ", t, t + 30)
        self.assertEqual(len(st), 2)
        # 7
        st = client.get_waveforms("TA", "A25A", "", "*", t, t + 30)
        self.assertEqual(len(st), 19)  # xxx: why 19 - should be 22!
        st = client.get_waveforms("TA", "A25A", "", "*,-BHZ", t, t + 30)
        self.assertEqual(len(st), 18)

    def test_no_wildcards(self):
        t1 = UTCDateTime(2010, 3, 25, 0, 0)
        t2 = t1 + 30
        client = FDSNClient(self.live_server_url)

        def msg(st, key, value):
            return ("\nGot the following stream that contains unexpected SEED "
                    "ID when querying '{}=\"{}\"':\n{}".format(
                        key, value, st.__str__(extended=True)))

        # execute some queries that match more than expected data if matched in
        # a regex behavior (similar to SQL LIKE)
        station_ids = ("A25A", "A2", "25", "5A")
        for id_ in station_ids:
            st = client.get_waveforms("*", id_, "*", "*", t1, t2)
            self.assertEqual(
                set(tr.stats.station for tr in st),
                set([id_]), msg=msg(st, value=id_, key="station"))
        network_ids = ("TA", "T", "A", "X", "")
        for id_ in network_ids:
            st = client.get_waveforms(id_, "*", "*", "*", t1, t2)
            self.assertEqual(
                set(tr.stats.network for tr in st),
                set([id_]), msg=msg(st, value=id_, key="network"))
        location_ids = ("", "00", "  ")
        for id_ in location_ids:
            st = client.get_waveforms("*", "*", id_, "*", t1, t2)
            self.assertEqual(
                set(tr.stats.location for tr in st),
                set([id_]), msg=msg(st, value=id_, key="location"))
        channel_ids = ("", "BHZ", "B", "Z", "H")
        for id_ in channel_ids:
            st = client.get_waveforms("*", "*", "*", id_, t1, t2)
            self.assertEqual(
                set(tr.stats.channel for tr in st),
                set([id_]), msg=msg(st, value=id_, key="channel"))
