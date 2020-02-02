# DigitalOcean Droplet Snapshot Tool

do-snapshot.py is a simple Python script to snapshot [DigitalOcean droplets](https://www.digitalocean.com/products/droplets/), transfer them to desired regions, and maintain a flexible retention policy.

## Example

Before we get into it, here's an example:

```bash
python3 do-snapshot.py --token ~/.dotoken -s 1d -r lon1 -k 3d:1d -k 1w:1w -k 1m:2m -k 0d:6m
```

This will:
* read the API token from ~/.dotoken
* take a snapshot of all droplets tagged `autosnapshot` (the default tag) once a day
* Transfer *any* snapshots taken by the tool to the `lon1` region (if they're not already there)
* Define this retention policy
    * Keep a snapshot every 3 days for snapshots older than a day
    * Keep one snapshot per week for snapshots older than a week
    * Keep one snapshot per month for snapshots older than a month
    * Delete all snapshots older than 6 months


## Gotchas

Due to DO API limitations:

1. In-progress snapshots aren't visible, so if you run the tool in quick succession, it can trigger another snapshot even though one is pending.  **Workaround**: don't run it more frequently than once an hour or so.
2. Only completed snapshots can be transferred to other regions, so if one invocation takes a snapshot, the transfer step will happen in the next invocation.  **Workaround**: run the tool more frequently than you want to snapshot.  (e.g. if you pass `-s 1d`, you can run it once every 4 hours or so.  Just mind gotcha #1.)

Other limitations I can't blame on DigitalOcean:
* Only online snapshots are supported presently

## DigitalOcean API Token
First, fetch a [personal access token](https://www.digitalocean.com/docs/api/create-personal-access-token/).

You can pass this token to do-snapshot.py directly on the command line, but if you do that, the token (which is equivalent to a password) is visible in `ps` output.  So it's recommended instead to pass a filename containing the token as the argument.

Or you can export it with the `DO_TOKEN` environment variable.

On proper operating systems:

```bash
export DO_TOKEN=<your token here>
```

Or on Windows:

```cmd
set DO_TOKEN=<your token here>
```

## Retention Policies

Sophisticated retention policies can be constructed by stacking multiple `-k` switches.  Policies are enforced in order of youngest age to oldest, which means that the number of snapshots the older policies have to work with are dictated by the younger ones.

This means that your intervals generally need to increase as the age increases.

For example, consider the arguments `-k 2w:1w -k 1w:1m`, which asks that:
1. we keep one snapshot every 2 weeks for snapshots older than a week
2. keep one snapshot per week for snapshots older than a month.

Policy #1 is evaluated first, so as older snapshots flow into the second policy (those older than 1 month), we'll only see one every two weeks.  It's therefore not possible to satisfy this policy.


### Simulation

You can simulate how a retention policy will behave by the `--simulate` option, where you simulate execution of the tool every `interval` (e.g. 4 hours) for a certain `duration` (e.g. 3 months).

Let's simulate the broken policy mentioned above to see why it doesn't work:

```
$ python3 do-snapshot.py -s 1d -k 2w:1w -k 1w:1m --simulate 4h:3m
[... log output snipped ...]
Time                    Name
----------------------- --------------------------------------------------
2018-10-09T01:47:36Z    simulated-autosnapshot-20181009T014736Z
2018-10-23T01:47:36Z    simulated-autosnapshot-20181023T014736Z
2018-11-06T01:47:36Z    simulated-autosnapshot-20181106T014736Z
2018-11-20T01:47:36Z    simulated-autosnapshot-20181120T014736Z
2018-12-04T01:47:36Z    simulated-autosnapshot-20181204T014736Z
2018-12-18T01:47:36Z    simulated-autosnapshot-20181218T014736Z
2018-12-31T01:47:36Z    simulated-autosnapshot-20181231T014736Z
2019-01-01T01:47:36Z    simulated-autosnapshot-20190101T014736Z
2019-01-02T01:47:36Z    simulated-autosnapshot-20190102T014736Z
2019-01-03T01:47:36Z    simulated-autosnapshot-20190103T014736Z
2019-01-04T01:47:36Z    simulated-autosnapshot-20190104T014736Z
2019-01-05T01:47:36Z    simulated-autosnapshot-20190105T014736Z
2019-01-06T01:47:36Z    simulated-autosnapshot-20190106T014736Z
```

We're taking 1 snapshot per day (`-s 1d`) and our youngest policy doesn't apply to snapshots until 1 week, so we see daily snapshots within the latest week (the first week of January).  For the previous month, December, we have 1 snapshot every 2 weeks.  We're deleting all other snapshots.  And so we see the expected result in November and October, where we're only able to keep a snapshot every 2 weeks even though the policy wants a snapshot per week.



## Usage

```
usage: do-snapshot.py [-h] [-t TAG] [-p PREFIX] [-r REGION] -s AGE
                      [-k INTERVAL:AGE] [--dryrun] [--token TOKEN]
                      [--simulate INTERVAL:DURATION] [--syslog] [-v]

optional arguments:
  -h, --help            show this help message and exit
  -t TAG, --tag TAG     snapshots droplets with the given tag (default:
                        autosnapshot)
  -p PREFIX, --prefix PREFIX
                        prefix for snapshot names (default: $droplet-
                        autosnapshot-
  -r REGION, --region REGION
                        transfer snapshots to this additional region (supports
                        multiple -r)
  -s AGE, --snapshot AGE
                        take a snapshot if the last one is older than AGE
                        (suffix m=months, w=weeks, d=days, h=hours)
  -k INTERVAL:AGE, --keep INTERVAL:AGE
                        keeps one snapshot per INTERVAL if older than AGE,
                        deleting others (supports multiple -k to build a
                        retention policy)
  --dryrun              don't actually take or delete or transfer any
                        snapshots
  --token TOKEN         API token or path to file containing the token (also
                        possible to pass via DO_TOKEN env var)
  --simulate INTERVAL:DURATION
                        Test a retention policy (multiple -k) by runs of the
                        toolevery INTERVAL for DURATION time. (Implies
                        --dryrun)
  --syslog              log to syslog instead of stderr
  -v, --verbose         Increase verbosity
```


do-snapshot.py operates on all droplets with a particular tag of your choosing (e.g. `prod` or `autosnapshot`), so if you haven't already, you'll want to apply a suitable tag to the droplet(s) you wish to snapshot.



## Dependencies

do-snapshot.py is tested on Python 3 but works with Python 2.  The only batteries-not-included dependency is the [requests library](http://docs.python-requests.org/en/master/).  On Ubuntu or Debian, this can be installed with:

```bash
sudo apt install python3-requests
```

Or on other distributions or platforms, assuming pip is installed:

```bash
pip install requests
```
