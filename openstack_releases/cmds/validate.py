# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Try to verify that the latest commit contains valid SHA values.

"""

from __future__ import print_function

import argparse
import atexit
import glob
import os
import os.path
import re
import shutil
import tempfile

import requests
import yaml

# Disable warnings about insecure connections.
from requests.packages import urllib3

from openstack_releases import defaults
from openstack_releases import gitutils
from openstack_releases import governance
from openstack_releases import project_config
from openstack_releases import versionutils

urllib3.disable_warnings()


def is_a_hash(val):
    "Return bool indicating if val looks like a valid hash."
    return re.search('^[a-f0-9]{40}$', val, re.I) is not None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--no-cleanup',
        dest='cleanup',
        default=True,
        action='store_false',
        help='do not remove temporary files',
    )
    parser.add_argument(
        'input',
        nargs='*',
        help=('YAML files to validate, defaults to '
              'files changed in the latest commit'),
    )
    args = parser.parse_args()

    filenames = args.input or gitutils.find_modified_deliverable_files()
    if not filenames:
        print('no modified deliverable files, validating all releases from %s'
              % defaults.RELEASE)
        filenames = glob.glob('deliverables/' + defaults.RELEASE + '/*.yaml')

    zuul_layout = project_config.get_zuul_layout_data()

    team_data = governance.get_team_data()
    independent_repos = set(
        r.name
        for r in governance.get_repositories(
            team_data,
            tags=['release:independent'],
        )
    )
    independent_checks = set()

    errors = []
    warnings = []

    workdir = tempfile.mkdtemp(prefix='releases-')
    print('creating temporary files in %s' % workdir)

    def cleanup_workdir():
        if args.cleanup:
            try:
                shutil.rmtree(workdir)
            except:
                pass
        else:
            print('not cleaning up %s' % workdir)
    atexit.register(cleanup_workdir)

    for filename in filenames:
        print('\nChecking %s' % filename)
        if not os.path.isfile(filename):
            print("File was deleted, skipping.")
            continue
        with open(filename, 'r') as f:
            deliverable_info = yaml.load(f.read())

        # Look for the launchpad project
        try:
            lp_name = deliverable_info['launchpad']
        except KeyError:
            errors.append('No launchpad project given in %s' % filename)
            print('no launchpad project name given')
        else:
            print('launchpad project %s ' % lp_name, end='')
            lp_resp = requests.get('https://api.launchpad.net/1.0/' + lp_name)
            if (lp_resp.status_code // 100) == 4:
                print('MISSING')
                errors.append('Launchpad project %s does not exist' % lp_name)
            else:
                print('found')

        # Look for the team name
        if 'team' not in deliverable_info:
            errors.append('No team name given in %s' % filename)
            print('no team name given')
        elif deliverable_info['team'] not in team_data:
            warnings.append('Team %r in %s not in governance data' %
                            (deliverable_info['team'], filename))

        # Look for the release-type
        release_type = deliverable_info.get('release-type', 'std')

        # Look for an email address to receive release announcements
        try:
            announce_to = deliverable_info['send-announcements-to']
        except KeyError:
            errors.append('No send-announcements-to in %s'
                          % filename)
            print('no send-announcements-to found')
        else:
            print('send announcements to %s' % announce_to)
            if ' ' in announce_to:
                print('Found space in send-announcements-to: %r' %
                      announce_to)
                errors.append('Space in send-announcements-to (%r) for %s' %
                              (announce_to, filename))

        # Make sure the release notes page exists, if it is specified.
        if 'release-notes' in deliverable_info:
            notes_link = deliverable_info['release-notes']
            if isinstance(notes_link, dict):
                links = list(notes_link.values())
            else:
                links = [notes_link]
            for link in links:
                rn_resp = requests.get(link)
                if (rn_resp.status_code // 100) == 2:
                    print('Release notes at %s found' % link)
                else:
                    errors.append('Could not fetch release notes page %s: %s' %
                                  (link, rn_resp.status_code))
                    print('Found bad release notes link %s: %s' %
                          (link, rn_resp.status_code))
        else:
            print('no release-notes specified')

        series_name = os.path.basename(
            os.path.dirname(filename)
        )

        # Remember which entries are new so we can verify that they
        # appear at the end of the file.
        new_releases = {}

        prev_version = None
        prev_projects = set()
        link_mode = deliverable_info.get('artifact-link-mode', 'tarball')
        for release in deliverable_info['releases']:

            for project in release['projects']:
                is_independent = (
                    (series_name, project['repo']) in independent_checks or
                    project['repo'] in independent_repos or
                    series_name == '_independent'
                )

                # Check for release jobs (if we ship a tarball)
                if link_mode != 'none':
                    pce = project_config.require_release_jobs_for_repo(
                        deliverable_info, zuul_layout, project['repo'],
                        release_type)
                    for msg, is_error in pce:
                        print(msg)
                        if is_error:
                            errors.append(msg)
                        else:
                            warnings.append(msg)

                # If the project is release:independent, make sure
                # that's where the deliverable file is.
                if is_independent:
                    if series_name != '_independent':
                        msg = ('%s uses the independent release model '
                               'and should be in the _independent '
                               'directory not in %s') % (project['repo'],
                                                         filename)
                        print(msg)
                        warnings.append(msg)
                    independent_checks.add((series_name, project['repo']))

                # Check the SHA specified for the tag.
                print('%s SHA %s ' % (project['repo'],
                                      project['hash']),
                      end='')

                if not is_a_hash(project['hash']):
                    print('NOT A SHA HASH')
                    errors.append(
                        ('%(repo)s version %(version)s release from '
                         '%(hash)r, which is not a hash') % {
                             'repo': project['repo'],
                             'hash': project['hash'],
                             'version': release['version'],
                             }
                    )
                else:
                    # Report if the SHA exists or not (an error if it
                    # does not).
                    sha_exists = gitutils.commit_exists(
                        project['repo'], project['hash'],
                    )
                    if not sha_exists:
                        print('MISSING', end='')
                        errors.append('No commit %(hash)r in %(repo)r'
                                      % project)
                    else:
                        print('found ', end='')
                    # Report if the version has already been
                    # tagged. We expect it to not exist, but neither
                    # case is an error because sometimes we want to
                    # import history and sometimes we want to make new
                    # releases.
                    print('version %s ' % release['version'], end='')
                    version_exists = gitutils.tag_exists(
                        project['repo'], release['version'],
                    )
                    gitutils.clone_repo(workdir, project['repo'])
                    if version_exists:
                        actual_sha = gitutils.sha_for_tag(
                            workdir,
                            project['repo'],
                            release['version'],
                        )
                        if actual_sha == project['hash']:
                            print('found and SHAs match, ')
                        else:
                            print('found DIFFERENT %r, ' % actual_sha)
                            errors.append(
                                ('Version %s in %s is on '
                                 'commit %s instead of %s') %
                                (release['version'],
                                 project['repo'],
                                 actual_sha,
                                 project['hash']))
                    else:
                        print('NEW VERSION, ', end='')
                        new_releases[release['version']] = release
                        if not prev_version:
                            print()
                        elif project['repo'] not in prev_projects:
                            print('not included in previous release for %s: %s' %
                                  (prev_version, ', '.join(sorted(prev_projects))))
                        else:

                            for e in versionutils.validate_version(
                                    release['version'],
                                    release_type=release_type):
                                msg = ('could not validate version %r '
                                       'for %s: %s' %
                                       (release['version'], filename, e))
                                print(msg)
                                errors.append(msg)

                            # Check to see if we are re-tagging the same
                            # commit with a new version.
                            old_sha = gitutils.sha_for_tag(
                                workdir,
                                project['repo'],
                                prev_version,
                            )
                            if old_sha == project['hash']:
                                print('RETAGGING')
                            elif not is_independent:
                                # Check to see if the commit for the new
                                # version is in the ancestors of the
                                # previous release, meaning it is actually
                                # merged into the branch.
                                is_ancestor = gitutils.check_ancestry(
                                    workdir,
                                    project['repo'],
                                    prev_version,
                                    project['hash'],
                                )
                                if is_ancestor:
                                    print('SHA found in descendants')
                                else:
                                    print('SHA NOT FOUND in descendants')
                                    if series_name == '_independent':
                                        save = warnings.append
                                    else:
                                        save = errors.append
                                    save(
                                        '%s %s receiving %s is not a descendant of %s' % (
                                            project['repo'],
                                            project['hash'],
                                            release['version'],
                                            prev_version,
                                        )
                                    )
                            else:
                                print('skipping descendant test for independent project, '
                                      'verify branch manually')
            prev_version = release['version']
            prev_projects = set(p['repo'] for p in release['projects'])

        # Make sure that new entries have been appended to the file.
        for v, nr in new_releases.items():
            if nr != deliverable_info['releases'][-1]:
                msg = ('new release %(version)s must be listed last, '
                       'with one new release per patch' % nr)
                print(msg)
                errors.append(msg)

        # Some rules only apply to the most current release.
        if series_name != defaults.RELEASE:
            continue

        # Rules for only the current release cycle.
        final_release = deliverable_info['releases'][-1]
        deliverable_name = os.path.basename(filename)[:-5]  # strip .yaml
        expected_repos = set(
            r.name
            for r in governance.get_repositories(
                team_data,
                deliverable_name=deliverable_name,
            )
        )
        if link_mode != 'none' and not expected_repos:
            msg = ('unable to find deliverable %s in the governance list' %
                   deliverable_name)
            print(msg)
            errors.append(msg)
        actual_repos = set(
            p['repo']
            for p in final_release.get('projects', [])
        )
        for extra in actual_repos.difference(expected_repos):
            msg = (
                '%s release %s includes repository %s '
                'that is not in the governance list' %
                (filename, final_release['version'], extra)
            )
            print(msg)
            warnings.append(msg)
        for missing in expected_repos.difference(actual_repos):
            msg = (
                '%s release %s is missing %s from the governance list' %
                (filename, final_release['version'], missing)
            )
            print(msg)
            warnings.append(msg)

    if warnings:
        print('\n\n%s warnings found' % len(warnings))
        for w in warnings:
            print(w)

    if errors:
        print('\n\n%s errors found' % len(errors))
        for e in errors:
            print(e)

    return 1 if errors else 0
