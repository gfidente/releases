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

import os
import os.path
import subprocess

import requests

# Disable warnings about insecure connections.
from requests.packages import urllib3
urllib3.disable_warnings()

CGIT_SHA_TEMPLATE = 'http://git.openstack.org/cgit/%s/commit/?id=%s'
CGIT_TAG_TEMPLATE = 'http://git.openstack.org/cgit/%s/tag/?h=%s'


def find_modified_deliverable_files():
    "Return a list of files modified by the most recent commit."
    results = subprocess.check_output(
        ['git', 'diff', '--name-only', '--pretty=format:', 'HEAD^']
    )
    filenames = [
        l.strip()
        for l in results.splitlines()
        if l.startswith('deliverables/')
    ]
    return filenames


def commit_exists(repo, ref):
    """Return boolean specifying whether the reference exists in the repository.

    Uses a cgit query instead of looking locally to avoid cloning a
    repository or having Depends-On settings in a commit message allow
    someone to fool the check.

    """
    url = CGIT_SHA_TEMPLATE % (repo, ref)
    response = requests.get(url)
    missing_commit = (
        (response.status_code // 100 != 2) or 'Bad object id' in response.text
    )
    return not missing_commit


def tag_exists(repo, ref):
    """Return boolean specifying whether the reference exists in the repository.

    Uses a cgit query instead of looking locally to avoid cloning a
    repository or having Depends-On settings in a commit message allow
    someone to fool the check.

    """
    url = CGIT_TAG_TEMPLATE % (repo, ref)
    response = requests.get(url)
    missing_commit = (
        (response.status_code // 100 != 2) or 'Bad object id' in response.text
    )
    return not missing_commit


def clone_repo(workdir, repo):
    "Check out the code."
    dest = os.path.join(workdir, repo)
    if os.path.exists(dest):
        return
    cmd = [
        'zuul-cloner',
        '--workspace', workdir,
    ]
    cache_dir = os.environ.get('ZUUL_CACHE_DIR', '/opt/git')
    if cache_dir and os.path.exists(cache_dir):
        cmd.extend(['--cache-dir', cache_dir])
    cmd.extend([
        'git://git.openstack.org',
        repo,
    ])
    subprocess.check_call(cmd)
    # Force an update, just in case the local version is still out of
    # date.
    print('Updating newly cloned repository in %s' % dest)
    subprocess.check_call(
        ['git', 'fetch', '-v', '--tags'],
        cwd=dest,
    )


def sha_for_tag(workdir, repo, version):
    """Return the SHA for a given tag
    """
    # git log 2.3.11 -n 1 --pretty=format:%H
    try:
        actual_sha = subprocess.check_output(
            ['git', 'log', str(version), '-n', '1', '--pretty=format:%H'],
            cwd=os.path.join(workdir, repo),
            stderr=subprocess.STDOUT,
        )
        actual_sha = actual_sha.strip()
    except subprocess.CalledProcessError as e:
        print('ERROR getting SHA for tag %r: %s [%s]' %
              (version, e, e.output.strip()))
        actual_sha = ''
    return actual_sha


def check_ancestry(workdir, repo, old_version, sha):
    "Check if the SHA is in the ancestry of the previous version."
    try:
        ancestors = subprocess.check_output(
            ['git', 'log', '--oneline', '--ancestry-path',
             '%s..%s' % (old_version, sha)],
            cwd=os.path.join(workdir, repo),
        ).strip()
        return bool(ancestors)
    except subprocess.CalledProcessError as e:
        print('ERROR checking ancestry: %s [%s]' % (e, e.output.strip()))
        return False


def get_latest_tag(workdir, repo, sha=None):
    cmd = ['git', 'describe', '--abbrev=0']
    if sha is not None:
        cmd.append(sha)
    try:
        return subprocess.check_output(
            cmd,
            cwd=os.path.join(workdir, repo),
            stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError as e:
        print('WARNING failed to retrieve latest tag: %s [%s]' %
              (e, e.output.strip()))
        return None


def get_branches(workdir, repo):
    try:
        output = subprocess.check_output(
            ['git', 'branch', '-a'],
            cwd=os.path.join(workdir, repo),
            stderr=subprocess.STDOUT,
        ).strip()
        return [
            branch
            for branch in output.split()
        ]
    except subprocess.CalledProcessError as e:
        print('ERROR failed to retrieve list of branches: %s [%s]' %
              (e, e.output.strip()))
        return []
