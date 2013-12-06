from datetime import datetime
import re
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship, backref
from sqlalchemy.orm.exc import DetachedInstanceError
from sqlalchemy import DateTime
from pecan import conf
from paddles.models import Base
from paddles.models.jobs import Job

suite_names = ['big',
               'ceph-deploy',
               'dummy',
               'experimental',
               'fs',
               'hadoop',
               'iozone',
               'kcephfs',
               'krbd',
               'marginal',
               'mixed-clients',
               'nfs',
               'powercycle',
               'rados',
               'rbd',
               'rgw',
               'smoke',
               'stress',
               'tgt',
               'upgrade-cuttlefish',
               'upgrade-dumpling',
               'upgrade-fs',
               'upgrade-mixed-cluster',
               'upgrade-mixed-mons',
               'upgrade-parallel',
               'upgrade-rados-double',
               'upgrade-rados',
               'upgrade-rbd-double',
               'upgrade-rbd',
               'upgrade-rgw-double',
               'upgrade-rgw',
               'upgrade-small',
               'upgrade',
               ]


def get_name_regexes(timestamp_regex, suite_names):
    """
    Build a regex used for getting timestamp, suite and branch info out of a
    test name.  Typical run names are of the format:
        user-timestamp-suite-branch-flavor-machine_type

    But sometimes suite, or branch, or both, are hyphenated. Unfortunately the
    delimiter is the same character, so for now we build this regex using the
    list of current suites. If this regex doesn't match, Run._parse_name() uses
    a backup regex.
    """
    regex_templ_no_mtype = \
        '.*-(?P<scheduled>{time})-(?P<suite>{suites})-(?P<branch>.*)-.*?-.*?'  # noqa
    regex_templ = regex_templ_no_mtype + '-.*?'
    suites_str = '(%s)' % '|'.join(suite_names)
    return [templ.format(time=timestamp_regex, suites=suites_str)
            for templ in (regex_templ, regex_templ_no_mtype)]


class Run(Base):
    timestamp_regex = \
        '[0-9]{1,4}-[0-9]{1,2}-[0-9]{1,2}_[0-9]{1,2}:[0-9]{1,2}:[0-9]{1,2}'
    timestamp_format = '%Y-%m-%d_%H:%M:%S'
    name_regexes = get_name_regexes(timestamp_regex, suite_names)
    backup_name_regex = '.*-(?P<scheduled>%s)-(?P<suite>.*)-(?P<branch>.*)-.*?-.*?-.*?' % timestamp_regex  # noqa

    __tablename__ = 'runs'
    id = Column(Integer, primary_key=True)
    name = Column(String(512))
    suite = Column(String(64), index=True)
    branch = Column(String(64), index=True)
    posted = Column(DateTime, index=True)
    scheduled = Column(DateTime, index=True)
    jobs = relationship('Job',
                        backref=backref('run'),
                        cascade='all,delete',
                        lazy='dynamic',
                        order_by='Job.id',
                        )

    def __init__(self, name):
        self.name = name
        self.posted = datetime.utcnow()
        parsed_name = self._parse_name()
        self.scheduled = parsed_name.get('scheduled', self.posted)
        self.suite = parsed_name.get('suite', '')
        self.branch = parsed_name.get('branch', '')

    def __repr__(self):
        try:
            return '<Run %r>' % self.name
        except DetachedInstanceError:
            return '<Run detached>'

    def __json__(self):
        results = self.get_results()
        status = 'running' if results['running'] else 'finished'
        return dict(
            name=self.name,
            href=self.href,
            status=status,
            results=results,
            jobs_count=results['total'],
            posted=self.posted,
            scheduled=self.scheduled,
            branch=self.branch,
            suite=self.suite,
        )

    def _parse_name(self):
        name_match = re.match(self.name_regexes[0], self.name) or \
            re.match(self.name_regexes[1], self.name) or \
            re.match(self.backup_name_regex, self.name)
        if name_match:
            match_dict = name_match.groupdict()
            scheduled = datetime.strptime(match_dict['scheduled'],
                                          self.timestamp_format)
            return dict(
                scheduled=scheduled,
                suite=match_dict['suite'].strip(' -'),
                branch=match_dict['branch'].strip(' -'),
                )
        return dict()

    def get_jobs(self):
        return [job for job in self.jobs]

    def get_jobs_by_description(self):
        jobs = self.get_jobs()
        by_desc = {}
        for job in jobs:
            by_desc[job.description] = job
        return by_desc

    @property
    def updated(self):
        if self.jobs.count():
            last_updated_job = self.jobs.order_by(Job.updated)[-1]
            return last_updated_job.updated
        else:
            return max(self.scheduled, self.posted)

    @property
    def href(self):
        return "%s/runs/%s/" % (conf.address, self.name),

    def get_results(self):
        jobs_status = [job.status for job in self.jobs]
        passing = jobs_status.count('pass')
        running = jobs_status.count('running')
        fail = jobs_status.count('fail')
        dead = jobs_status.count('dead')
        unknown = jobs_status.count(None)
        total = self.jobs.count()
        return {
            'pass': passing,
            'running': running,
            'fail': fail,
            'dead': dead,
            'unknown': unknown,
            'total': total
        }

    @property
    def status(self):
        running = self.jobs.filter_by(success=None).count()
        if running:
            return "running"
        return "finished"
