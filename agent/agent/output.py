#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import time
import gzip
import base64
import StringIO

from . import __version__
from ..logger import Logging
from util import JSONCls
from exception import OutputError

__author__ = 'tong'

logger = Logging()


class OutputBase(object):
    def __init__(self):
        self.agent = None

    def catch(self, agent):
        self.agent = agent

    def throw(self):
        self.agent = None


class Kafka(OutputBase):
    def __init__(self, topic, server, client=None, **kwargs):
        try:
            import kafka
        except ImportError:
            raise OutputError('Lack of kafka module, try to execute `pip install kafka-python>=1.3.1` install it')

        client = client or kafka.SimpleClient
        self._producer = None
        self._topic = topic
        try:
            self._kafka = client(server, **kwargs)
        except Exception, e:
            raise OutputError('kafka client init failed: %s' % e)
        self.producer(kafka.SimpleProducer)
        super(Kafka, self).__init__()

    def producer(self, producer, **kwargs):
        try:
            self._producer = producer(self._kafka, **kwargs)
        except Exception, e:
            raise OutputError('kafka producer init failed: %s' % e)

    def send(self, event):
        if not self._producer:
            raise OutputError('No producer init')
        logger.info('OUTPUT INSERT Kafka 1: %s' % self._producer.send_messages(self._topic, event.data))

    def sendmany(self, events):
        if not self._producer:
            raise OutputError('No producer init')
        logger.info('OUTPUT INSERT Kafka %s: %s' %
                    (len(events), self._producer.send_messages(self._topic, *[e.data for e in events])))

    def close(self):
        if self._producer:
            del self._producer
            self._producer = None


class HTTPRequest(OutputBase):
    def __init__(self, server, headers=None, method='GET'):
        self.server = server
        self.method = method.upper()
        self.headers = headers or {}
        self.headers.setdefault('User-Agent', 'python-Agent %s HTTPRequest' % __version__)
        super(HTTPRequest, self).__init__()

    def send(self, event):
        import requests
        if self.method == 'GET':
            ret = requests.get(self.server, params=event.raw_data, headers=self.headers)
            logger.info('OUTPUT INSERT Request 1: %s' % ret)
        elif self.method == 'POST':
            ret = requests.post(self.server, data=self.data(event.raw_data), headers=self.headers)
            logger.info('OUTPUT INSERT Request 1: %s' % ret)

    def sendmany(self, events):
        import requests
        data = {'data': self.package(events), 'gzip': '1'}
        if self.method == 'GET':
            ret = requests.get(self.server, params=data, headers=self.headers)
            logger.info('OUTPUT INSERT Request %s: %s' % (len(events), ret))
        elif self.method == 'POST':
            ret = requests.post(self.server, data=self.data(data), headers=self.headers)
            logger.info('OUTPUT INSERT Request %s: %s' % (len(events), ret))

    def data(self, data):
        ctype = self.headers.get('Content-Type')
        if ctype == 'application/json':
            return json.dumps(data, separators=(',', ':'))
        return data

    @staticmethod
    def package(events):
        data = json.dumps([item.raw_data for item in events])
        if isinstance(data, unicode):
            data = data.encode('utf8')

        buf = StringIO.StringIO()
        fd = gzip.GzipFile(fileobj=buf, mode="w")
        fd.write(data)
        fd.close()
        result = buf.getvalue()
        result = base64.b64encode(result)
        return result


class Csv(OutputBase):
    def __init__(self, filename, fieldnames=None, **kwargs):
        self.fp = open(filename, 'w')
        self.filename = filename
        self.fieldnames = fieldnames
        self.kwargs = kwargs
        self.writer = None
        super(Csv, self).__init__()

    def catch(self, agent):
        from csv import DictWriter
        super(Csv, self).catch(agent)
        self.fieldnames = self.fieldnames or agent.parser.fieldnames
        self.writer = DictWriter(self.fp, self.fieldnames, **self.kwargs)
        self.writer.writeheader()

    def send(self, event):
        self.writer.writerow(event.raw_data)
        self.fp.flush()
        logger.info('OUTPUT INSERT CSV 1')

    def sendmany(self, events):
        self.writer.writerows([event.raw_data for event in events])
        self.fp.flush()
        logger.info('OUTPUT INSERT CSV %s' % len(events))

    def close(self):
        self.fp.close()

    def archive(self, filename):
        if filename == self.filename:
            raise Exception('archive name is same as old (%s)' % filename)

        from csv import DictWriter
        os.renames(self.filename, filename)
        self.fp.close()
        self.fp = open(self.filename, 'w')
        self.writer = DictWriter(self.fp, self.fieldnames, **self.kwargs)
        self.writer.writeheader()


class SQLAlchemy(OutputBase):
    def __init__(self, table, *args, **kwargs):
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
        except ImportError:
            raise OutputError('Lack of SQLAlchemy module, try to execute `pip install SQLAlchemy` install it')
        self.quote = kwargs.pop('quote', '`')
        self.fields = kwargs.pop('fieldnames', None)
        self.engine = create_engine(*args, **kwargs)
        self.DB_Session = sessionmaker(bind=self.engine)
        self._session = None
        self._timer = None
        self._table = table
        super(SQLAlchemy, self).__init__()

    def catch(self, agent):
        super(SQLAlchemy, self).catch(agent)
        fieldnames = self.fields or agent.parser.fieldnames
        action = 'INSERT INTO'
        keys = ','.join(['%s%s%s' % (self.quote, key, self.quote) for key in fieldnames])
        values = ','.join([':%s' % key for key in fieldnames])
        self.fields = fieldnames
        self.sql = '%s %s (%s) VALUES (%s)' % (action, self.table, keys, values)

    @property
    def session(self):
        try:
            if not self._session:
                self._timer = time.time()
                self._session = self.DB_Session()
            elif time.time() - self._timer > 900:
                self._session.close()
                self._session = self.DB_Session()
                self._timer = time.time()
        except:
            self._timer = time.time()
            self._session = self.DB_Session()
        return self._session

    @property
    def table(self):
        return '%s%s%s' % (self.quote, self._table, self.quote)

    def send(self, event):
        data = event.raw_data
        self.session.execute(self.sql, data)
        self.session.commit()
        logger.info('SQL send 1')

    def sendmany(self, events):
        self.session.execute(self.sql, [event.raw_data for event in events])
        self.session.commit()
        logger.info('SQL send %s' % len(events))


class Screen(OutputBase):
    def __init__(self, *args, **kwargs):
        self.counter = 0
        self.args = args
        self.kwargs = kwargs
        super(Screen, self).__init__()

    def __getattr__(self, item):
        return lambda *args, **kwargs: item

    def send(self, event):
        self.counter += 1
        print '=== %s ===' % self.counter
        print 'Type: %s' % event.type
        if isinstance(event.raw_data, basestring):
            print 'Result: %s' % event.raw_data.strip()
        else:
            print json.dumps(event.raw_data, indent=2, cls=JSONCls)
        print

    def sendmany(self, events):
        for event in events:
            self.send(event)


class Null(OutputBase):
    def __init__(self, *args, **kwargs):
        super(Null, self).__init__()

    def __getattr__(self, item):
        return lambda *args, **kwargs: item

    def send(self, event):
        pass

    def sendmany(self, events):
        pass
