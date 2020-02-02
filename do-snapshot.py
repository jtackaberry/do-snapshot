#!/usr/bin/env python3
# Copyright (c) 2018 Jason Tackaberry
#
# Licensed under the MIT license.  See LICENSE.txt for details.
"""
do-snapshot.py - snapshot droplets on DigitalOcean
"""
import sys
import os
import stat
import itertools
import string
import logging
import logging.handlers
from datetime import datetime, timedelta
from argparse import ArgumentParser

import requests

DO_API_URL_PREFIX = 'https://api.digitalocean.com/v2/'

log = logging.getLogger('do-snapshot')


def parse_interval(interval):
    """
    Parses a string like '3d' and returns a timedelta object
    """
    suffixmap = {
        'd': lambda x: {'days': x},
        'h': lambda x: {'hours': x},
        'm': lambda x: {'days': x*30},
        'w': lambda x: {'weeks': x}
    }
    try:
        kwargs = suffixmap[interval[-1]](int(interval[:-1]))
    except KeyError:
        raise ValueError('unsupported interval suffix {}'.format(interval[-1]))
    except ValueError:
        raise ValueError('interval must be a number')
    else:
        return timedelta(**kwargs)


def api(method, token, path, payload=None, dryrun=False):
    """
    Simple wrapper for the DO v2 API.

    A 4xx return code will log the error but no exception is raised.  The response object is
    returned.
    """
    headers = {'Authorization': 'Bearer ' + token}
    url = DO_API_URL_PREFIX + path
    if dryrun:
        log.debug('dryrun: skipping API call: %s %s payload=%s', method, path, payload)
        return
    r = getattr(requests, method)(url, json=payload, headers=headers)
    if r.status_code >= 400 and r.status_code < 500:
        log.error('api call %s failed with status %s: %s', path, r.status_code, r.text)
    return r


def ensure_snapshot_regions(args, snapshot, regions):
    missing = set(regions) - set(snapshot['regions'])
    if not missing:
        log.debug('snapshot %s is already in required regions (%s)',
                  snapshot['name'], ', '.join(snapshot['regions']))
    for region in missing:
        log.info('transferring snapshot %s to region %s', snapshot['name'], region)
        api('post', args.token, 'images/{}/actions'.format(snapshot['id']),
            {'type': 'transfer', 'region': region}, dryrun=args.dryrun)


def apply_retention_policies(args, snapshots, policies, now):
    # Create a dictionary of snapshots by id -- we remove from it for each
    # deleted snapshot so we can return a final set of survivors
    snapshots_by_id = dict((snapshot['id'], snapshot) for snapshot in snapshots)
    # Sort snapshots from oldest to newest.
    snapshots.sort(key=lambda s: s['created_at'])
    # Group snapshots by applicable policy
    snapshots_by_policy = {}
    for snapshot in snapshots:
        snapshot_time = datetime.strptime(snapshot['created_at'][:19], '%Y-%m-%dT%H:%M:%S')
        snapshot_age = now - snapshot_time
        snapshot['created_dt'] = snapshot_time
        snapshot['age'] = snapshot_age
        for interval, age in policies:
            if snapshot_age >= age:
                # This snapshot is older than the age for this policy, so it applies.
                snapshots_by_policy.setdefault((interval, age), []).append(snapshot)
                break

    # Apply retention policies
    for (interval, age), snapshots in snapshots_by_policy.items():
        # We need to iterate over the snapshots from oldest to newest (the current order) so
        # we prefer older snapshots, which is necessary to allow snapshots to age through their
        # policy group.
        last_kept = None
        for snapshot in (snapshots):
            log.debug('considering snapshot %s: age %s', snapshot['name'],
                      snapshot['created_dt'] - last_kept if last_kept else None)
            if not interval or (last_kept and snapshot['created_dt'] - last_kept < interval):
                log.info('deleting snapshot %s by policy (keep snapshot every %s if older than %s)',
                         snapshot['name'], interval, age)
                api('delete', args.token, 'snapshots/{}'.format(snapshot['id']), dryrun=args.dryrun)
                del snapshots_by_id[snapshot['id']]
            else:
                log.debug('preserving snapshot %s', snapshot['name'])
                last_kept = snapshot['created_dt']

    # Return survivors
    return sorted(snapshots_by_id.values(), key=lambda s: s['created_at'])


def process_droplet(args, droplet, snapshots, policies, min_age, prefix, now):
    """
    Do all the things.  Delete obsolete snapshots, transfer remaining snapshots to
    desired regions, and take a new snapshot if it's time.

    Returns a tuple of remaining snapshots, and snapshot name if one was taken,
    otherwise None.
    """
    for snapshot in snapshots:
        log.debug('found snapshot %s (%s) for droplet %s', snapshot['name'], snapshot['id'], droplet['name'])

    # Remove obsolete snapshots
    survivors = apply_retention_policies(args, snapshots, policies, now)

    # Transfer snapshots missing from regions
    if args.region:
        for snapshot in survivors:
            ensure_snapshot_regions(args, snapshot, args.region)
            # In case we are running a simulation, update 
            snapshot['regions'] = list(set(snapshot['regions']).union(args.region))

    # Finally take a new snapshot if needed
    if not snapshots or snapshots[-1]['age'] >= min_age:
        snapshot_name = now.strftime(prefix + '%Y%m%dT%H%M%SZ')
        log.info('snapshotting droplet %s -> %s', droplet['name'], snapshot_name)
        r = api('post', args.token, 'droplets/{}/actions'.format(droplet['id']),
            {'type': 'snapshot', 'name': snapshot_name}, dryrun=args.dryrun)
        if r:
            log.debug('snapshot response: %s', r.json())
    else:
        log.info('skipping snapshot, most recent is %s old', snapshots[-1]['age'])
        snapshot_name = None
    return survivors, snapshot_name


def main():
    p = ArgumentParser()
    p.add_argument('-t', '--tag', dest='tag', default='autosnapshot',
                   help='snapshots droplets with the given tag (default: autosnapshot)')
    p.add_argument('-p', '--prefix', dest='prefix', default='$droplet-autosnapshot-',
                   help='prefix for snapshot names (default: $droplet-autosnapshot-')
    p.add_argument('-r', '--region', dest='region', action='append', default=[],
                   help='transfer snapshots to this additional region (supports multiple -r)')
    p.add_argument('-s', '--snapshot', dest='snapshot', metavar='AGE', required=True,
                   help='take a snapshot if the last one is older than AGE '
                        '(suffix m=months, w=weeks, d=days, h=hours)')
    p.add_argument('-k', '--keep', dest='keep', metavar='INTERVAL:AGE', action='append', default=[],
                   help='keeps one snapshot per INTERVAL if older than AGE, deleting others '
                        ' (supports multiple -k to build a retention policy)')
    p.add_argument('--dryrun', dest='dryrun', action='store_true', default=False,
                   help="don't actually take or delete or transfer any snapshots")
    p.add_argument('--token', dest='token', action='store',
                   help='API token or path to file containing the token '
                        '(also possible to pass via DO_TOKEN env var)')
    p.add_argument('--simulate', dest='simulate', metavar='INTERVAL:DURATION',
                   help='Test a retention policy (multiple -k) by runs of the tool'
                        'every INTERVAL for DURATION time.  (Implies --dryrun)')
    p.add_argument('--syslog', dest='syslog', action='store_true', default=False,
                   help='log to syslog instead of stderr')
    p.add_argument('-v', '--verbose', dest='verbose', action='store_true',
                   help='Increase verbosity')
    args = p.parse_args()

    if args.syslog:
        handler = logging.handlers.SysLogHandler('/dev/log')
        fmt = logging.Formatter('do-snapshot[%(process)d]: %(message)s')
        handler.setFormatter(fmt)
        log.addHandler(handler)
    else:
        logging.basicConfig(format='%(asctime)s [%(levelname)s] %(message)s')

    if args.dryrun or args.verbose:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)

    if args.simulate:
        args.token = 'dummy'
        args.dryrun = True
    if not args.token:
        args.token = os.getenv('DO_TOKEN')
    else:
        fname = os.path.expanduser(args.token)
        if os.path.exists(fname):
            # Token is actually a filename, so read it.
            args.token = open(fname).read().strip()
            if not sys.platform.startswith('win'):
                # Sanity check permissions on token file.
                mode = os.stat(fname).st_mode
                if mode & stat.S_IROTH or mode & stat.S_IRGRP:
                    log.warning('token file %s is readable by group or other', fname)

    if not args.token:
        return log.fatal('must pass API token via --token or DO_TOKEN environment variable')
    elif len(args.token) not in (32, 64) or not all(c in string.hexdigits for c in args.token):
        log.warning('token looks invalid (unexpected size or non-hex characters)')

    args.prefix = args.prefix.replace('$tag', args.tag).strip()
    min_age = parse_interval(args.snapshot)
    log.info('will snapshot if latest is older than %s', min_age)
    args.region = [r.strip() for r in args.region]
    args.tag = args.tag.strip()

    policies = []
    if args.keep:
        policies = []
        for keep in args.keep:
            try:
                interval, age = keep.split(':')
            except ValueError:
                raise ValueError('keep argument must be in the form INTERVAL:AGE')
            interval, age = parse_interval(interval), parse_interval(age)
            policies.append((interval, age))
            if interval:
                log.info('keeping only 1 snapshot every %s for snapshots older than %s', interval, age)
            else:
                log.info('keeping no snapshots older than %s', age)
            if age < min_age:
                log.warning('retention policy %s uses lower age than snapshot age (%s)', keep, args.snapshot)
        # Sort policies by age (oldest first)
        policies.sort(key=lambda p: p[1], reverse=True)

    now = datetime.utcnow().replace(microsecond=0)

    if not args.simulate:
        # Enumerate all droplets matching the supplied tag
        r = api('get', args.token, 'droplets?tag_name=' + args.tag)
        droplets = r.json()['droplets']
        log.info('%d droplets found with tag %s', len(droplets), args.tag)
        for droplet in droplets:
            r = api('get', args.token, 'droplets/{}/snapshots'.format(droplet['id']))
            # Filter out non-autosnapshots
            prefix = args.prefix.replace('$droplet', droplet['name'])
            snapshots = [snapshot for snapshot in r.json()['snapshots'] if prefix in snapshot['name']]
            log.info('%d autosnapshots found for droplet %s', len(snapshots), droplet['name'])
            process_droplet(args, droplet, snapshots, policies, min_age, prefix, now)
    else:
        # Dummy droplet for the simulation
        droplet = {'id': 0, 'name': 'simulated'}
        prefix = args.prefix.replace('$droplet', droplet['name'])
        try:
            interval, duration = args.simulate.split(':')
        except ValueError:
            raise ValueError('simulate argument must be in the form INTERVAL:DURATION')
        interval, duration = parse_interval(interval), parse_interval(duration)
        end = now + duration
        idgen = itertools.count()
        snapshots = []
        while now < end:
            snapshot_id = next(idgen)
            log.info('--- simulation %d at %s', snapshot_id, now)
            snapshots, newsnapshot = process_droplet(args, droplet, snapshots, policies, min_age, prefix, now)
            if newsnapshot:
                snapshots.append({
                    'id': snapshot_id,
                    'name': newsnapshot,
                    'created_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'regions': []
                })
            now += interval
        log.info('%s remaining snapshots (below) after %s', len(snapshots), duration)
        print('Time'.ljust(23), 'Name')
        print('-' * 23, '-' * 50)
        for snapshot in snapshots:
            print(snapshot['created_at'].ljust(23), snapshot['name'])


if __name__ == '__main__':
    try:
        main()
    except ValueError as e:
        # Raised by argument validation
        log.fatal(e)
    except Exception:
        log.exception('uncaught exception')
        sys.exit(1)
