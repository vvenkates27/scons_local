#!/usr/bin/python
# Copyright (c) 2016-2019 Intel Corporation
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# -*- coding: utf-8 -*-
"""Classes for building external prerequisite components"""

# pylint: disable=too-few-public-methods
# pylint: disable=too-many-instance-attributes
# pylint: disable=broad-except
# pylint: disable=bare-except
# pylint: disable=exec-used
# pylint: disable=too-many-statements

import os
import traceback
import hashlib
import time
import subprocess
try:
    from subprocess import DEVNULL
except ImportError:
    DEVNULL = open(os.devnull, "wb")
import tarfile
import re
import copy
import ConfigParser
from build_info import BuildInfo
from SCons.Variables import PathVariable
from SCons.Variables import EnumVariable
from SCons.Script import Dir
from SCons.Script import GetOption
from SCons.Script import SetOption
from SCons.Script import Configure
from SCons.Script import AddOption
from SCons.Script import Builder
from SCons.Script import SConscript
# pylint: disable=no-name-in-module
# pylint: disable=import-error
from SCons.Errors import UserError
# pylint: enable=no-name-in-module
# pylint: enable=import-error
from prereq_tools import mocked_tests


class DownloadFailure(Exception):
    """Exception raised when source can't be downloaded

    Attributes:
        repo      -- Specified location
        component -- Component
    """

    def __init__(self, repo, component):
        super(DownloadFailure, self).__init__()
        self.repo = repo
        self.component = component

    def __str__(self):
        """ Exception string """
        return "Failed to get %s from %s" % (self.component, self.repo)


class ExtractionError(Exception):
    """Exception raised when source couldn't be extracted

    Attributes:
        component -- Component
        reason    -- Reason for problem
    """

    def __init__(self, component):
        super(ExtractionError, self).__init__()
        self.component = component

    def __str__(self):
        """ Exception string """

        return "Failed to extract %s" % self.component


class UnsupportedCompression(Exception):
    """Exception raised when library doesn't support extraction method

    Attributes:
        component -- Component
    """

    def __init__(self, component):
        super(UnsupportedCompression, self).__init__()
        self.component = component

    def __str__(self):
        """ Exception string """
        return "Don't know how to extract %s" % self.component


class BadScript(Exception):
    """Exception raised when a preload script has errors

    Attributes:
        script -- path to script
        trace  -- traceback
    """

    def __init__(self, script, trace):
        super(BadScript, self).__init__()
        self.script = script
        self.trace = trace

    def __str__(self):
        """ Exception string """
        return "Failed to execute %s:\n%s %s\n\nTraceback" % (self.script,
                                                              self.script,
                                                              self.trace)


class MissingDefinition(Exception):
    """Exception raised when component has no definition

    Attributes:
        component    -- component that is missing definition
    """

    def __init__(self, component):
        super(MissingDefinition, self).__init__()
        self.component = component

    def __str__(self):
        """ Exception string """
        return "No definition for %s" % self.component


class MissingPath(Exception):
    """Exception raised when user speficies a path that doesn't exist

    Attributes:
        variable    -- Variable specified
    """

    def __init__(self, variable):
        super(MissingPath, self).__init__()
        self.variable = variable

    def __str__(self):
        """ Exception string """
        return "%s specifies a path that doesn't exist" % self.variable


class BuildFailure(Exception):
    """Exception raised when component fails to build

    Attributes:
        component    -- component that failed to build
    """

    def __init__(self, component):
        super(BuildFailure, self).__init__()
        self.component = component

    def __str__(self):
        """ Exception string """
        return "%s failed to build" % self.component


class MissingTargets(Exception):
    """Exception raised when expected targets missing after component build

    Attributes:
        component    -- component that has missing targets
    """

    def __init__(self, component, package):
        super(MissingTargets, self).__init__()
        self.component = component
        self.package = package

    def __str__(self):
        """ Exception string """
        if self.package is None:
            return "%s has missing targets after build.  " \
                   "See config.log for details" % self.component
        return "Package %s is required. " \
               "Check config.log" % self.package


class MissingSystemLibs(Exception):
    """Exception raised when libraries required for target build are not met

    Attributes:
        component    -- component that has missing targets
    """

    def __init__(self, component):
        super(MissingSystemLibs, self).__init__()
        self.component = component

    def __str__(self):
        """ Exception string """
        return "%s has unmet dependancies required for build" % self.component


class DownloadRequired(Exception):
    """Exception raised when component is missing but downloads aren't enabled

    Attributes:
        component    -- component that is missing
    """

    def __init__(self, component):
        super(DownloadRequired, self).__init__()
        self.component = component

    def __str__(self):
        """ Exception string """
        return "%s needs to be built, use --build-deps=yes" % self.component


class BuildRequired(Exception):
    """Exception raised when component is missing but builds aren't enabled

    Attributes:
        component    -- component that is missing
    """

    def __init__(self, component):
        super(BuildRequired, self).__init__()
        self.component = component

    def __str__(self):
        """ Exception string """
        return "%s needs to be built, use --build-deps=yes" % self.component


class Runner(object):
    """Runs commands in a specified environment"""
    def __init__(self):
        self.env = None
        self.__dry_run = False

    def initialize(self, env):
        """Initialize the environment in the runner"""
        self.env = env
        self.__dry_run = env.GetOption('no_exec')

    def run_commands(self, commands, subdir=None):
        """Runs a set of commands in specified directory"""
        if not self.env:
            raise Exception("PreReqComponent not initialized")
        retval = True
        old = os.getcwd()
        if subdir:
            if self.__dry_run:
                print 'Would change dir to %s' % subdir
            else:
                os.chdir(subdir)

        print 'Running commands in %s' % os.getcwd()
        for command in commands:
            command = self.env.subst(command)
            if self.__dry_run:
                print 'Would RUN: %s' % command
                retval = True
            else:
                print 'RUN: %s' % command
                if subprocess.call(command, shell=True,
                                   env=self.env['ENV']) != 0:
                    retval = False
                    break
        if subdir:
            os.chdir(old)
        return retval


RUNNER = Runner()

def default_libpath():
    """On debian systems, the default library path can be queried"""
    if not os.path.isfile('/etc/debian_version'):
        return []
    try:
        pipe = subprocess.Popen(['dpkg-architecture', '-qDEB_HOST_MULTIARCH'],
                                stdout=subprocess.PIPE, stderr=DEVNULL)
        (stdo, _) = pipe.communicate()
        if pipe.returncode == 0:
            archpath = stdo.decode().strip()
            return ['lib/' + archpath]
    except Exception:
        pass
    return []

def check_test(target, source, env, mode):
    """Check the results of the test"""
    val_str = ""
    error_str = ""
    with open(target[0].path, "r") as fobj:
        for line in fobj.readlines():
            if re.search("FAILED", line):
                error_str = """
Please see %s for the errors and fix
the issues causing the TESTS to fail.
""" % target[0].path
                break
        fobj.close()
    if mode == "memcheck" or mode == "helgrind":
        from xml.etree import ElementTree
        for fname in target:
            if str(fname).endswith(".xml"):
                with open(str(fname), "r") as xmlfile:
                    tree = ElementTree.parse(xmlfile)
                error_types = {}
                for node in tree.iter('error'):
                    kind = node.find('./kind')
                    if kind.text not in error_types:
                        error_types[kind.text] = 0
                    error_types[kind.text] += 1
                if error_types:
                    val_str += """
Valgrind %s check failed.  See %s:""" % (mode, str(fname))
                    for err in error_types:
                        val_str += "\n%-3d %s errors" % (error_types[err], err)
    if val_str != "":
        print """
#########################################################%s
#########################################################
""" % val_str

    if error_str:
        return """
#########################################################
Libraries built successfully but some unit TESTS failed.
%s
#########################################################
""" % error_str
    return None


def define_check_test(mode=None):
    """Define a function to create test checker"""
    return lambda target, source, env: check_test(target, source, env, mode)


def run_test(source, target, env, for_signature, mode=None):
    """Create test actions."""
    count = 1
    sup_dir = os.path.dirname(source[0].srcnode().abspath)
    sup_file = os.path.join(sup_dir, "%s.sup" % mode)
    action = ['touch %s' % target[0]]
    for test in source:
        valgrind_str = ""
        if mode in ["memcheck", "helgrind"]:
            sup = ""
            if os.path.exists(sup_file):
                sup = "--suppressions=%s" % sup_file
            valgrind_str = "valgrind --xml=yes --xml-file=%s " \
                           "--child-silent-after-fork=yes " \
                           "%s " % (target[count], sup)
            if mode == "memcheck":
                # Memory analysis
                valgrind_str += "--partial-loads-ok=yes --leak-check=full "
            elif mode == "helgrind":
                # Thread analysis
                valgrind_str += "--tool=helgrind "
        count += 1
        action.append("%s%s >> %s" % (valgrind_str,
                                      str(test),
                                      target[0]))
    action.append("cat %s" % target[0])
    action.append(define_check_test(mode=mode))
    return action


def modify_targets(target, source, env, mode=None):
    """Emit the target list for the unit test builder"""
    target = ["test_output"]
    if mode == "memcheck" or mode == "helgrind":
        for src in source:
            basename = os.path.basename(str(src))
            xml = "valgrind-%s-%s.xml" % (mode, basename)
            target.append(xml)
    return target, source


def define_run_test(mode=None):
    """Define a function to create test actions"""
    return lambda source, target, env, for_signature: run_test(source,
                                                               target,
                                                               env,
                                                               for_signature,
                                                               mode)


def define_modify_targets(mode=None):
    """Define a function to create test targets"""
    return lambda target, source, env: modify_targets(target, source,
                                                      env, mode)


class GitRepoRetriever(object):
    """Identify a git repository from which to download sources"""

    def __init__(self, url, has_submodules=False, branch=None):

        self.url = url
        self.has_submodules = has_submodules
        self.branch = branch
        self.commit_sha = None
        self.patch = None

    def checkout_commit(self, subdir):
        """ checkout a certain commit SHA or branch """
        if self.commit_sha is not None:
            commands = ['git checkout %s' % (self.commit_sha)]
            if not RUNNER.run_commands(commands, subdir=subdir):
                raise DownloadFailure(self.url, subdir)

    def apply_patch(self, subdir):
        """ git-apply a certain hash """
        if self.patch is not None:
            print "Applying patch %s" % (self.patch)
            commands = ['git apply %s' % (self.patch)]
            if not RUNNER.run_commands(commands, subdir=subdir):
                raise DownloadFailure(self.url, subdir)

    def update_submodules(self, subdir):
        """ update the git submodules """
        if self.has_submodules:
            commands = ['git submodule init', 'git submodule update']
            if not RUNNER.run_commands(commands, subdir=subdir):
                raise DownloadFailure(self.url, subdir)

    def get(self, subdir, **kw):
        """Downloads sources from a git repository into subdir"""
        commands = ['git clone %s %s' % (self.url, subdir)]
        if not RUNNER.run_commands(commands):
            raise DownloadFailure(self.url, subdir)
        self.get_specific(subdir, **kw)

    def get_specific(self, subdir, **kw):
        """Checkout the configured commit"""
        # If the config overrides the branch, use it.  If a branch is
        # specified, check it out first.
        branch = kw.get("branch", None)
        if branch is None:
            branch = self.branch
        self.branch = branch
        if self.branch:
            self.commit_sha = self.branch
            self.checkout_commit(subdir)

        # Now checkout the commit_sha if specified
        passed_commit_sha = kw.get("commit_sha", None)
        if passed_commit_sha is not None:
            self.commit_sha = passed_commit_sha
            self.checkout_commit(subdir)

        # Now apply any patches specified
        self.patch = kw.get("patch", None)
        self.apply_patch(subdir)
        self.update_submodules(subdir)

    def update(self, subdir, **kw):
        """ update a repository """
        #Fetch all changes and then reset head to origin/master
        commands = ['git fetch --all',
                    'git reset --hard']
        if not RUNNER.run_commands(commands, subdir=subdir):
            raise DownloadFailure(self.url, subdir)
        self.get_specific(subdir, **kw)


class WebRetriever(object):
    """Identify a location from where to download a source package"""

    def __init__(self, url, md5):
        self.url = url
        self.md5 = md5
        self.__dry_run = GetOption('check_only')
        if self.__dry_run:
            SetOption('no_exec', True)
        self.__dry_run = GetOption('no_exec')

    def check_md5(self, filename):
        """Return True if md5 matches"""

        if not os.path.exists(filename):
            return False

        with open(filename, "r") as src:
            hexdigest = hashlib.md5(src.read()).hexdigest()

        if hexdigest != self.md5:
            print "Removing exising file %s: md5 %s != %s" % (filename,
                                                              self.md5,
                                                              hexdigest)
            os.remove(filename)
            return False

        print "File %s matches md5 %s" % (filename, self.md5)
        return True

    def download(self, basename):
        """Download the file"""
        initial_sleep = 1
        retries = 3
        # Retry download a few times if it fails
        for i in range(0, retries + 1):
            command = ['curl -L -O %s' % self.url]

            failure_reason = "Download command failed"
            if RUNNER.run_commands(command):
                if self.check_md5(basename):
                    print "Successfully downloaded %s" % self.url
                    return True

                failure_reason = "md5 mismatch"

            print "Try #%d to get %s failed: %s" % (i + 1, self.url,
                                                    failure_reason)

            if i != retries:
                time.sleep(initial_sleep)
                initial_sleep *= 2

        return False

    def get(self, subdir, **kw):
        """Downloads and extracts sources from a url into subdir"""

        basename = os.path.basename(self.url)

        if os.path.exists(subdir):
            # assume that nothing has changed
            return

        if not self.check_md5(basename) and not self.download(basename):
            raise DownloadFailure(self.url, subdir)

        if self.url.endswith('.tar.gz') or self.url.endswith('.tgz'):
            if self.__dry_run:
                print 'Would unpack gziped tar file: %s' % basename
                return
            try:
                tfile = tarfile.open(basename, 'r:gz')
                members = tfile.getnames()
                prefix = os.path.commonprefix(members)
                tfile.extractall()
                if not RUNNER.run_commands(['mv %s %s' % (prefix, subdir)]):
                    raise ExtractionError(subdir)
            except (IOError, tarfile.TarError):
                print traceback.format_exc()
                raise ExtractionError(subdir)
        else:
            raise UnsupportedCompression(subdir)

    def update(self, subdir, **kw):
        """ update the code if the url has changed """
        # Will download the file if the name has changed
        self.get(subdir, **kw)


def check_flag_helper(context, compiler, ext, flag):
    """Helper function to allow checking for compiler flags"""
    test_flag = flag
    werror = "-Werror"
    if compiler in ["icc", "icpc"]:
        werror = "-diag-error=10006"
        # bug in older scons, need CFLAGS to exist, -O2 is default.
        context.env.Replace(CFLAGS=['-O2'])
    if compiler in ["gcc", "g++"]:
        #remove -no- for test
        test_flag = flag.replace("-Wno-", "-W")
    context.Message("Checking %s %s " % (compiler, flag))
    context.env.Replace(CCFLAGS=[werror, test_flag])
    ret = context.TryCompile("""
int main() {
    return 0;
}
""", ext)
    context.Result(ret)
    return ret

def check_flag(context, flag):
    """Check C specific compiler flags"""
    return check_flag_helper(context, context.env.get("CC"), ".c", flag)

def check_flag_cc(context, flag):
    """Check C++ specific compiler flags"""
    return check_flag_helper(context, context.env.get("CXX"), ".cpp", flag)

def check_flags(env, config, key, value):
    """Check and append all supported flags"""
    checked = []
    for flag in value:
        if flag in checked:
            continue
        insert = False
        if not flag.startswith('-W'):
            insert = True
        else:
            if key == "CCFLAGS":
                if config.CheckFlag(flag) and config.CheckFlagCC(flag):
                    insert = True
            elif key == "CFLAGS":
                if config.CheckFlag(flag):
                    insert = True
            elif config.CheckFlagCC(flag):
                insert = True
        if insert:
            env.AppendUnique(**{key : [flag]})
        checked.append(flag)

def append_if_supported(env, **kwargs):
    """Check and append flags for construction variables"""
    cenv = env.Clone()
    config = Configure(cenv, custom_tests={'CheckFlag' : check_flag,
                                           'CheckFlagCC' : check_flag_cc})
    for key, value in kwargs.iteritems():
        if key not in ["CFLAGS", "CXXFLAGS", "CCFLAGS"]:
            env.AppendUnique(**{key : value})
            continue
        check_flags(env, config, key, value)

    config.Finish()

class ProgramBinary(object):
    """Define possible names for a required executable"""
    def __init__(self, name, possible_names):
        """ Define a binary allowing for unique names on various platforms """
        self.name = name
        self.options = possible_names

    def configure(self, config, prereqs):
        """ Do configure checks for binary name options to get a match """
        for option in self.options:
            if config.CheckProg(option):
                args = {self.name: option}
                prereqs.replace_env(**args)
                return True
        return False

# pylint: disable=too-many-public-methods
class PreReqComponent(object):
    """A class for defining and managing external components required
       by a project.

    If provided arch is a string to embed in any generated directories
    to allow compilation from from multiple systems in one source tree
    """

# pylint: disable=too-many-branches

    def __init__(self, env, variables, config_file=None, arch=None):
        self.__defined = {}
        self.__required = {}
        self.__errors = {}
        self.__env = env
        self.__opts = variables
        self.configs = None

        real_env = self.__env['ENV']

        for var in ["HOME", "TERM", "SSH_AUTH_SOCK",
                    "http_proxy", "https_proxy"]:
            value = os.environ.get(var)
            if value:
                real_env[var] = value

        libtoolize = 'libtoolize'
        if self.__env['PLATFORM'] == 'darwin':
            libtoolize = 'glibtoolize'

        self.add_options()
        self.__setup_unit_test_builders()
        self.__env.AddMethod(append_if_supported, "AppendIfSupported")
        self.__env.AddMethod(mocked_tests.build_mock_unit_tests,
                             'BuildMockingUnitTests')
        self.__require_optional = GetOption('require_optional')
        self.__update = GetOption('update_prereq')
        self.download_deps = False
        self.build_deps = False
        self.__parse_build_deps()
        self.replace_env(LIBTOOLIZE=libtoolize)
        self.__env.Replace(ENV=real_env)
        warning_level = GetOption('warning_level')
        if warning_level == 'error':
            env.Append(CCFLAGS=['-Werror'])
        pre_path = GetOption('prepend_path')
        if pre_path:
            old_path = self.__env['ENV']['PATH']
            self.__env['ENV']['PATH'] = pre_path + os.pathsep + old_path
        locale_name = GetOption('locale_name')
        if locale_name:
            self.__env['ENV']['LC_ALL'] = locale_name
        self.__check_only = GetOption('check_only')
        if self.__check_only:
            # This is mostly a no_exec request.
            SetOption('no_exec', True)
        self.__dry_run = GetOption('no_exec')
        if config_file is None:
            config_file = GetOption('build_config')

        RUNNER.initialize(self.__env)

        self.__top_dir = Dir('#').abspath

        build_dir_name = '_build.external'
        install_dir = os.path.join(self.__top_dir, 'install')
        if arch:
            build_dir_name = '_build.external-%s' % arch
            install_dir = os.path.join('install', str(arch))

            # Overwrite default file locations to allow multiple builds in the
            # same tree.
            env.SConsignFile('.sconsign-%s' % arch)
            env.Replace(CONFIGUREDIR='#/.sconf-temp-%s' % arch,
                        CONFIGURELOG='#/config-%s.log' % arch)

        self.system_env = env.Clone()

        self.__build_dir = os.path.realpath(os.path.join(self.__top_dir,
                                                         build_dir_name))
        try:
            if self.__dry_run:
                print 'Would mkdir -p %s' % self.__build_dir
            else:
                os.makedirs(self.__build_dir)

        except Exception:
            pass
        self.__prebuilt_path = {}
        self.__src_path = {}

        self.__opts.Add('USE_INSTALLED',
                        'Comma separated list of preinstalled dependencies',
                        'none')
        self.add_opts(PathVariable('PREFIX', 'Installation path', install_dir,
                                   PathVariable.PathIsDirCreate),
                      ('PREBUILT_PREFIX',
                       'Colon separated list of paths to look for prebuilt '
                       'components.',
                       None),
                      ('SRC_PREFIX',
                       'Colon separated list of paths to look for source '
                       'of prebuilt components.',
                       None),
                      PathVariable('TARGET_PREFIX',
                                   'Installation root for prebuilt components',
                                   None, PathVariable.PathIsDirCreate),
                      PathVariable('GOPATH',
                                   'Location of your GOPATH for the build',
                                   "%s/go" % self.__build_dir,
                                   PathVariable.PathIsDirCreate))
        self.setup_path_var('PREFIX')
        self.setup_path_var('PREBUILT_PREFIX', True)
        self.setup_path_var('TARGET_PREFIX')
        self.setup_path_var('SRC_PREFIX', True)
        self.setup_path_var('GOPATH')
        self.setup_patch_prefix()
        self.__build_info = BuildInfo()
        self.__build_info.update("PREFIX", self.__env.subst("$PREFIX"))

        self._setup_compiler()
        self.setup_parallel_build()

        self.add_opts(PathVariable('ENV_SCRIPT',
                                   "Location of environment script",
                                   os.path.expanduser('~/.scons_localrc'),
                                   PathVariable.PathAccept))

        env_script = self.__env.get("ENV_SCRIPT")
        if os.path.exists(env_script):
            SConscript(env_script, exports=['env'])

        self.config_file = config_file
        if config_file is not None:
            self.configs = ConfigParser.ConfigParser()
            self.configs.read(config_file)

        self.installed = env.subst("$USE_INSTALLED").split(",")
# pylint: enable=too-many-branches

    def _setup_intelc(self):
        """Setup environment to use intel compilers"""
        env = self.__env.Clone(tools=['intelc'])
        self.__env.Replace(AR=env.get("AR"))
        self.__env.Replace(ENV=env.get("ENV"))
        self.__env.Replace(CC=env.get("CC"))
        self.__env.Replace(CXX=env.get("CXX"))
        version = env.get("INTEL_C_COMPILER_VERSION")
        self.__env.Replace(INTEL_C_COMPILER_VERSION=version)
        self.__env.Replace(LINK=env.get("LINK"))
        # Link intel symbols statically with no external visibility and
        # disable the warning about Cilk since we don't use it
        self.__env.AppendUnique(LINKFLAGS=["-Wl,--exclude-libs,ALL",
                                           "-static-intel",
                                           "-diag-disable=10237"])


    def _setup_compiler(self):
        """Setup the compiler to use"""
        compiler_map = {'gcc': {'CC' : 'gcc', 'CXX' : 'g++'},
                        'covc' : {'CC' : '/opt/BullseyeCoverage/bin/gcc',
                                  'CXX' : '/opt/BullseyeCoverage/bin/g++',
                                  'COV01' : '/opt/BullseyeCoverage/bin/cov01'},
                        'clang' : {'CC' : 'clang', 'CXX' : 'clang++'},
                        'icc' : {'CC' : 'icc', 'CXX' : 'icpc'},
                       }
        self.add_opts(EnumVariable('COMPILER', "Set the compiler family to use",
                                   'gcc', ['gcc', 'covc', 'clang', 'icc'],
                                   ignorecase=1))

        if GetOption('clean'):
            return

        compiler = self.__env.get('COMPILER').lower()
        if compiler == 'icc':
            self._setup_intelc()
        env = self.__env.Clone()
        config = Configure(env)

        if self.__check_only:
            # Have to temporarily turn off dry run to allow this check.
            env.SetOption('no_exec', False)

        for name, prog in compiler_map[compiler].iteritems():
            if not config.CheckProg(prog):
                print "%s must be installed when COMPILER=%s" % (prog, compiler)
                if self.__check_only:
                    continue
                config.Finish()
                raise MissingSystemLibs(prog)
            args = {name : prog}
            self.__env.Replace(**args)

        if compiler == 'covc':
            covfile = self.__top_dir + "/test.cov"
            if os.path.isfile(covfile):
                os.remove(covfile)
            commands = ['$COV01 -1', '$COV01 -s']
            if not RUNNER.run_commands(commands):
                raise BuildFailure("cov01")

        config.Finish()
        if self.__check_only:
            # Restore the dry run state
            env.SetOption('no_exec', True)

    def setup_parallel_build(self):
        """Set the JOBS_OPT variable for builds"""
        jobs_opt = GetOption('num_jobs')
        self.__env["JOBS_OPT"] = "-j %d" % jobs_opt

    def get_build_info(self):
        """Retrieve the BuildInfo"""
        return self.__build_info

    def get_config_file(self):
        """Retrieve the Config File"""
        return self.config_file

    @staticmethod
    def add_options():
        """Add common options to environment"""

        AddOption('--update-prereq',
                  dest='update_prereq',
                  type='string',
                  nargs=1,
                  action='append',
                  default=[],
                  metavar='COMPONENT',
                  help='Force an update of a prerequisite COMPONENT.  Use '
                       '\'all\' to update all components')

        AddOption('--require-optional',
                  dest='require_optional',
                  action='store_true',
                  default=False,
                  help='Fail the build if check_component fails')

        # There is another case here which isn't handled, we want Jenkins
        # builds to download tar packages but not git source code.  For now
        # just have a single flag and set this for the Jenkins builds which
        # need this.
        AddOption('--build-deps',
                  dest='build_deps',
                  type='choice',
                  choices=['yes', 'no', 'build-only'],
                  default='no',
                  help="Automatically download and build sources.  " \
                       "(yes|no|build-only) [no]")

        # We want to be able to check what dependencies are needed with out
        # doing a build, similar to --dry-run.  We can not use --dry-run
        # on the command line because it disables running the tests for the
        # the dependencies.  So we need a new option
        AddOption('--check-only',
                  dest='check_only',
                  action='store_true',
                  default=False,
                  help="Check dependencies only, do not download or build.")

        # Need to be able to look for an alternate build.config file.
        AddOption('--build-config',
                  dest='build_config',
                  default=os.path.join(Dir('#').abspath,
                                       'utils', 'build.config'),
                  help='build config file to use. [%default]')

        # We need to sometimes use alternate tools for building and need
        # to add them to the PATH in the environment.
        AddOption('--prepend-path',
                  dest='prepend_path',
                  default=None,
                  help="String to prepend to PATH environment variable.")

        # Allow specifying the locale to be used.  Default "en_US.UTF8"
        AddOption('--locale-name',
                  dest='locale_name',
                  default='en_US.UTF8',
                  help='locale to use for building. [%default]')

        # This option sets a hint as to if -Werror should be used.
        AddOption('--warning-level',
                  dest='warning_level',
                  type='choice',
                  choices=['warning', 'error'],
                  default='error',
                  help='Treatment for a compiler warning.  ' \
                       '(warning|error} [error]')
        SetOption("implicit_cache", True)

    def setup_patch_prefix(self):
        """Discovers the location of the patches directory and adds it to
           the construction environment."""
        patch_dirs = [os.path.join(self.__top_dir,
                                   "scons_local", "sl_patches"),
                      os.path.join(self.__top_dir, "sl_patches")]
        for patch_dir in patch_dirs:
            if os.path.exists(patch_dir):
                self.replace_env(**{'PATCH_PREFIX': patch_dir})
                break

    def __setup_unit_test_builders(self):
        """Setup unit test builders for general use"""
        AddOption('--utest-mode',
                  dest='utest_mode',
                  type='choice',
                  choices=['native', 'memcheck', 'helgrind'],
                  default='native',
                  help="Specifies mode for running unit tests. " \
                       "(native|memcheck|helgrind) [native]")

        mode = GetOption("utest_mode")
        test_run = Builder(generator=define_run_test(mode),
                           emitter=define_modify_targets(mode))

        self.__env.Append(BUILDERS={"RunTests": test_run})

    def __parse_build_deps(self):
        """Parse the build dependances command line flag"""
        build_deps = GetOption('build_deps')
        if build_deps == 'yes':
            self.download_deps = True
            self.build_deps = True
        elif build_deps == 'build-only':
            self.build_deps = True

        # If the --update-prereq option is given then it is OK to build
        # code but not to download it unless --build-deps=yes is also
        # given.
        if self.__update:
            self.build_deps = True

    def setup_path_var(self, var, multiple=False):
        """Create a command line variable for a path"""
        tmp = self.__env.get(var)
        if tmp:
            realpath = lambda x: os.path.realpath(os.path.join(self.__top_dir,
                                                               x))
            if multiple:
                value = os.pathsep.join(map(realpath, tmp.split(os.pathsep)))
            else:
                value = realpath(tmp)
            self.__env[var] = value
            self.__opts.args[var] = value

    def update_src_path(self, name, value):
        """Update a variable in the default construction environment"""
        opt_name = '%s_SRC' % name.upper()
        self.__env[opt_name] = value
        self.__opts.args[opt_name] = value

    def add_opts(self, *variables):
        """Add options to the command line"""
        for var in variables:
            self.__opts.Add(var)
        try:
            self.__opts.Update(self.__env)
        except UserError:
            if self.__dry_run:
                pass
            else:
                raise

    def define(self,
               name,
               **kw):
        """Define an external prerequisite component

    Args:
        name -- The name of the component definition

    Keyword arguments:
        libs -- A list of libraries to add to dependent components
        libs_cc -- Optional CC command to test libs with.
        headers -- A list of expected headers
        requires -- A list of names of required component definitions
        required_libs -- A list of system libraries to be checked for
        defines -- Defines needed to use the component
        package -- Name of package to install
        patch -- Patch to apply to sources after checkout and before building
        commands -- A list of commands to run to build the component
        retriever -- A retriever object to download component
        extra_lib_path -- Subdirectories to add to dependent component path
        extra_include_path -- Subdirectories to add to dependent component path
        out_of_src_build -- Build from a different directory if set to True
        """
        update = False
        if 'all' in self.__update or name in self.__update:
            update = True
        comp = _Component(self,
                          name,
                          update,
                          **kw)
        self.__defined[name] = comp

    def load_definitions(self, **kw):
        """Load default definitions

        Keyword arguments:
            prebuild -- A list of components to prebuild
        """

        try:
            from components import define_components
            define_components(self)
        except Exception:
            raise BadScript("components", traceback.format_exc())

        # Go ahead and prebuild some components

        prebuild = kw.get("prebuild", [])
        for comp in prebuild:
            env = self.__env.Clone()
            self.require(env, comp)

    def modify_prefix(self, comp_def, env):
        """Overwrite the prefix in cases where we may be using the default"""
        prebuilt1 = os.path.join(env.subst("$PREBUILT_PREFIX"),
                                 comp_def.name)
        opt_name = "%s_PREBUILT" % comp_def.name.upper()
        # prebuilt2 can be None so add a default
        prebuilt2 = self.__env.get(opt_name, "/__fake__")

        if comp_def.src_path and \
           not os.path.exists(comp_def.src_path) and \
           not os.path.exists(prebuilt1) and \
           not os.path.exists(prebuilt2):
            self.save_component_prefix('%s_PREFIX' %
                                       comp_def.name.upper(),
                                       "/usr")

    def require(self,
                env,
                *comps,
                **kw):
        """Ensure a component is built, if necessary, and add necessary
           libraries and paths to the construction environment.

        Args:
            env -- The construction environment to modify
            comps -- component names to configure
            kw -- Keyword arguments

        Keyword arguments:
            headers_only -- if set to True, skip library configuration
            [comp]_libs --  Override the default libs for a package.  Ignored
                            if headers_only is set
        """
        changes = False
        headers_only = kw.get('headers_only', False)
        for comp in comps:
            if comp not in self.__defined:
                raise MissingDefinition(comp)
            if comp in self.__errors:
                raise self.__errors[comp]
            comp_def = self.__defined[comp]
            if headers_only:
                needed_libs = None
            else:
                needed_libs = kw.get('%s_libs' % comp, comp_def.libs)
            if comp in self.__required:
                if GetOption('help'):
                    continue
                # checkout and build done previously
                comp_def.set_environment(env, needed_libs)
                if GetOption('clean'):
                    continue
                if self.__required[comp]:
                    changes = True
                continue
            self.__required[comp] = False
            try:
                comp_def.configure()
                if comp_def.build(env, needed_libs):
                    self.__required[comp] = False
                    changes = True
                else:
                    self.modify_prefix(comp_def, env)
            except Exception as error:
                # Save the exception in case the component is requested again
                self.__errors[comp] = error
                raise error

        return changes

    def check_component(self, *comps, **kw):
        """Returns True if a component is available"""
        env = self.__env.Clone()
        try:
            self.require(env, *comps, **kw)
        except Exception as error:
            if self.__require_optional:
                raise error
            return False
        return True

    def get_env(self, var):
        """Get a variable from the construction environment"""
        return self.__env[var]

    def replace_env(self, **kw):
        """Replace a values in the construction environment

        Keyword arguments:
            kw -- Arbitrary variables to replace
        """
        self.__env.Replace(**kw)

    def get_build_dir(self):
        """Get the build directory for external components"""
        return self.__build_dir

    def get_prebuilt_path(self, name, prebuilt_default):
        """Get the path for a prebuilt component"""
        if name in self.__prebuilt_path:
            return self.__prebuilt_path[name]

        opt_name = '%s_PREBUILT' % name.upper()
        self.add_opts(PathVariable(opt_name,
                                   'Alternate installation '
                                   'prefix for %s' % name,
                                   prebuilt_default, PathVariable.PathIsDir))
        self.setup_path_var(opt_name)
        prebuilt = self.__env.get(opt_name)
        if prebuilt and not os.path.exists(prebuilt):
            raise MissingPath(opt_name)

        if not prebuilt:
            # check the global prebuilt area
            prebuilt_path = self.__env.get('PREBUILT_PREFIX')
            if prebuilt_path:
                for path in prebuilt_path.split(os.pathsep):
                    prebuilt = os.path.join(path, name)
                    if os.path.exists(prebuilt):
                        break
                    prebuilt = None

        self.__prebuilt_path[name] = prebuilt
        return prebuilt

    def get_defined_components(self):
        """Get a list of all defined component names"""
        return copy.copy(self.__defined.keys())

    def get_defined(self):
        """Get a dictionary of defined components"""
        return copy.copy(self.__defined)

    def save_component_prefix(self, var, value):
        """Save the component prefix in the environment and
           in build info"""
        self.replace_env(**{var: value})
        self.__build_info.update(var, value)

    def get_prefixes(self, name, prebuilt_path):
        """Get the location of the scons prefix as well as the external
           component prefix."""
        prefix = self.__env.get('PREFIX')
        comp_prefix = '%s_PREFIX' % name.upper()
        if prebuilt_path:
            self.save_component_prefix(comp_prefix, prebuilt_path)
            return (prebuilt_path, prefix)
        target_prefix = self.__env.get('TARGET_PREFIX')
        if target_prefix:
            target_prefix = os.path.join(target_prefix, name)
            self.save_component_prefix(comp_prefix, target_prefix)
            return (target_prefix, prefix)
        self.save_component_prefix(comp_prefix, prefix)
        return (prefix, prefix)

    def get_src_path(self, name):
        """Get the location of the sources for an external component"""
        if name in self.__src_path:
            return self.__src_path[name]
        opt_name = '%s_SRC' % name.upper()
        default_src_path = os.path.join(self.__build_dir, name)
        self.add_opts(PathVariable(opt_name,
                                   'Alternate path for %s source' % name,
                                   default_src_path, PathVariable.PathAccept))
        self.setup_path_var(opt_name)

        src_path = self.__env.get(opt_name)
        if src_path != default_src_path and not os.path.exists(src_path):
            if not self.__dry_run:
                raise MissingPath(opt_name)

        if src_path == default_src_path:
            # check the global source area
            src_path_var = self.__env.get('SRC_PREFIX')
            if src_path_var:
                for path in src_path_var.split(os.pathsep):
                    new_src_path = os.path.join(path, name)
                    if os.path.exists(new_src_path):
                        src_path = new_src_path
                        self.update_src_path(name, src_path)
                        break

        self.__src_path[name] = src_path
        return src_path

    def get_config(self, section, name):
        """Get commit/patch versions"""
        if self.configs is None:
            return None
        if not self.configs.has_section(section):
            return None

        if not self.configs.has_option(section, name):
            return None
        return self.configs.get(section, name)

    def load_config(self, comp, src_opt):
        """If the component has a config file to load, load it"""
        config_path = self.get_config("configs", comp)
        if config_path is None:
            return
        full_path = self.__env.subst("$%s/%s" % (src_opt, config_path))
        print "Reading config file for %s from %s" % (comp, full_path)
        self.configs.read(full_path)
# pylint: enable=too-many-public-methods

class _Component(object):
    """A class to define attributes of an external component

    Args:
        prereqs -- A PreReqComponent object
        name -- The name of the component definition

    Keyword arguments:
        libs -- A list of libraries to add to dependent components
        libs_cc -- Optional compiler for testing libs
        headers -- A list of expected headers
        requires -- A list of names of required component definitions
        commands -- A list of commands to run to build the component
        package -- Name of package to install
        patch -- Patch to apply to sources after checkout and before building
        retriever -- A retriever object to download component
        extra_lib_path -- Subdirectories to add to dependent component path
        extra_include_path -- Subdirectories to add to dependent component path
        out_of_src_build -- Build from a different directory if set to True
    """

    def __init__(self,
                 prereqs,
                 name,
                 update,
                 **kw):

        self.__check_only = GetOption('check_only')
        self.__dry_run = GetOption('no_exec')
        self.targets_found = False
        self.update = update
        self.build_path = None
        self.prebuilt_path = None
        self.src_path = None
        self.prefix = None
        self.component_prefix = None
        self.package = kw.get("package", None)
        self.progs = kw.get("progs", [])
        self.libs = kw.get("libs", [])
        self.libs_cc = kw.get("libs_cc", None)
        self.required_libs = kw.get("required_libs", [])
        self.required_progs = kw.get("required_progs", [])
        self.defines = kw.get("defines", [])
        self.headers = kw.get("headers", [])
        self.requires = kw.get("requires", [])
        self.patch = kw.get("patch", None)
        self.prereqs = prereqs
        self.name = name
        self.build_commands = kw.get("commands", [])
        self.retriever = kw.get("retriever", None)
        self.lib_path = ['lib', 'lib64']
        self.include_path = ['include']
        self.lib_path.extend(default_libpath())
        self.lib_path.extend(kw.get("extra_lib_path", []))
        self.include_path.extend(kw.get("extra_include_path", []))
        self.out_of_src_build = kw.get("out_of_src_build", False)
        self.src_opt = '%s_SRC' % name.upper()
        self.prebuilt_opt = '%s_PREBUILT' % name.upper()
        self.crc_file = os.path.join(self.prereqs.get_build_dir(),
                                     '_%s.crc' % self.name)

    def src_exists(self):
        """Check if the source directory exists"""
        if self.src_path and os.path.exists(self.src_path):
            return True
        return False

    def _delete_old_file(self, path):
        """delete the old file"""
        if os.path.exists(path):
            if self.__dry_run:
                print 'Would unlink %s' % path
            else:
                os.unlink(path)

    def get(self):
        """Download the component sources, if necessary"""
        if self.prebuilt_path:
            print 'Using prebuilt binaries for %s' % self.name
            return
        branch = self.prereqs.get_config("branches", self.name)
        commit_sha = self.prereqs.get_config("commit_versions", self.name)
        patch = self.prereqs.get_config("patch_versions", self.name)
        if patch is None:
            patch = self.patch
        if self.src_exists():
            self.prereqs.update_src_path(self.name, self.src_path)
            print 'Using existing sources at %s for %s' \
                % (self.src_path, self.name)
            if self.update:
                defpath = os.path.join(self.prereqs.get_build_dir(), self.name)
                # only do this if the source was checked out by this script
                if self.src_path == defpath:
                    self.retriever.update(self.src_path, commit_sha=commit_sha,
                                          patch=patch, branch=branch)
            elif self.prereqs.build_deps and self.patch is not None:
                # Apply patch to existing source.
                print "Applying patch %s" % (self.patch)
                commands = ['patch -p 1 -N -t < %s ; if [ $? -gt 1 ]; then '
                            'false; else true; fi;' % (self.patch)]
                if not RUNNER.run_commands(commands, subdir=self.src_path):
                    raise BuildFailure(self.patch)
            return

        if not self.retriever:
            print 'Using installed version of %s' % self.name
            return

        # Source code is retrieved using retriever

        if not self.prereqs.download_deps:
            raise DownloadRequired(self.name)

        print 'Downloading source for %s' % self.name
        self._delete_old_file(self.crc_file)
        self.retriever.get(self.src_path, commit_sha=commit_sha, patch=patch,
                           branch=branch)

        self.prereqs.update_src_path(self.name, self.src_path)

    def calculate_crc(self):
        """Calculate a CRC on the sources to detect changes"""
        new_crc = ''
        if not self.src_path:
            return new_crc
        for (root, _, files) in os.walk(self.src_path):
            for fname in files:
                (_, ext) = os.path.splitext(fname)

                # not fool proof but may be good enough

                if ext in ['.c',
                           '.h',
                           '.cpp',
                           '.cc',
                           '.hpp',
                           '.ac',
                           '.in',
                           '.py']:
                    with open(os.path.join(root, fname), 'r') as src:
                        new_crc += hashlib.md5(src.read()).hexdigest()
        return new_crc

    def has_changes(self):
        """Check the sources for changes since the last build"""

        old_crc = ''
        try:
            with open(self.crc_file, 'r') as crcfile:
                old_crc = crcfile.read()
        except IOError:
            pass
        if old_crc == '':
            return True
        # only do CRC check if the sources have been updated
        if self.update:
            print 'Checking %s sources for changes' % self.name
            if old_crc != self.calculate_crc():
                return True
        return False

    def has_missing_system_deps(self, env):
        """Check for required system libs"""

        if self.__check_only:
            # Have to temporarily turn off dry run to allow this check.
            env.SetOption('no_exec', False)

        if env.GetOption('no_exec'):
            # Can not override no-check in the command line.
            print 'Would check for missing system libraries'
            return False

        config = Configure(env)

        for lib in self.required_libs:
            if not config.CheckLib(lib):
                config.Finish()
                if self.__check_only:
                    env.SetOption('no_exec', True)
                return True

        try:
            for prog in self.required_progs:
                if isinstance(prog, ProgramBinary):
                    has_bin = prog.configure(config, self.prereqs)
                else:
                    has_bin = config.CheckProg(prog)
                if not has_bin:
                    config.Finish()
                    if self.__check_only:
                        env.SetOption('no_exec', True)
                    return True
        except AttributeError:
            # This feature is new in scons 2.4
            pass

        config.Finish()
        if self.__check_only:
            env.SetOption('no_exec', True)
        return False

    # pylint: disable=too-many-branches
    def has_missing_targets(self, env):
        """Check for expected build targets (e.g. libraries or headers)"""
        if self.targets_found:
            return False

        if self.__check_only:
            # Temporarily turn off dry-run.
            env.SetOption('no_exec', False)

        if env.GetOption('no_exec'):
            # Can not turn off dry run set by command line.
            print "Would check for missing build targets"
            return True

        config = Configure(env)
        for prog in self.progs:
            if not config.CheckProg(prog):
                config.Finish()
                if self.__check_only:
                    env.SetOption('no_exec', True)
                return True

        for header in self.headers:
            if not config.CheckHeader(header):
                config.Finish()
                if self.__check_only:
                    env.SetOption('no_exec', True)
                return True

        for lib in self.libs:
            old_cc = env.subst('$CC')
            if self.libs_cc:
                arg_key = "%s_PREFIX" % self.name.upper()
                args = {arg_key: self.component_prefix}
                env.Replace(**args)
                env.Replace(CC=self.libs_cc)
            result = config.CheckLib(lib)
            env.Replace(CC=old_cc)
            if not result:
                config.Finish()
                if self.__check_only:
                    env.SetOption('no_exec', True)
                return True

        config.Finish()
        self.targets_found = True
        if self.__check_only:
            env.SetOption('no_exec', True)
        return False
    # pylint: enable=too-many-branches

    def configure(self):
        """Setup paths for a required component"""
        self.prereqs.setup_path_var(self.src_opt)
        self.prereqs.setup_path_var(self.prebuilt_opt)
        prebuilt_default = None
        if not self.retriever:
            prebuilt_default = "/usr"
        self.prebuilt_path = self.prereqs.get_prebuilt_path(self.name,
                                                            prebuilt_default)

        (self.component_prefix, self.prefix) = \
            self.prereqs.get_prefixes(self.name, self.prebuilt_path)
        self.src_path = None
        if self.retriever:
            self.src_path = self.prereqs.get_src_path(self.name)
        self.build_path = self.src_path
        if self.out_of_src_build:
            self.build_path = \
                os.path.join(self.prereqs.get_build_dir(), '%s.build'
                             % self.name)
            try:
                if self.__dry_run:
                    print 'Would mkdir -p %s' % self.build_path
                else:
                    os.makedirs(self.build_path)
            except:
                pass

    def set_environment(self, env, needed_libs):
        """Modify the specified construction environment to build with
           the external component"""
        lib_paths = []

        # Make sure CheckProg() looks in the component's bin/ dir
        env.AppendENVPath('PATH', os.path.join(self.component_prefix, 'bin'))

        for path in self.include_path:
            env.AppendUnique(CPPPATH=[os.path.join(self.component_prefix,
                                                   path)])
        # The same rules that apply to headers apply to RPATH.   If a build
        # uses a component, that build needs the RPATH of the dependencies.
        for path in self.lib_path:
            if self.component_prefix == '/usr':
                continue
            full_path = os.path.join(self.component_prefix, path)
            if not os.path.exists(full_path):
                continue
            lib_paths.append(full_path)
            env.AppendUnique(RPATH=[full_path])
        # Ensure RUNPATH is used rather than RPATH.  RPATH is deprecated
        # and this allows LD_LIBRARY_PATH to override RPATH
        env.AppendUnique(LINKFLAGS=["-Wl,--enable-new-dtags"])

        for define in self.defines:
            env.AppendUnique(CPPDEFINES=[define])

        if needed_libs is None:
            return

        for path in lib_paths:
            env.AppendUnique(LIBPATH=[path])
        for lib in needed_libs:
            env.AppendUnique(LIBS=[lib])

    def check_installed_package(self, env):
        """Check installed targets"""
        if self.has_missing_targets(env):
            if self.__dry_run:
                if self.package is None:
                    print 'Missing %s' % self.name
                else:
                    print 'Missing package %s %s' % (self.package, self.name)
                return
            if self.package is None:
                raise MissingTargets(self.name, self.name)
            else:
                raise MissingTargets(self.name, self.package)

    def check_user_options(self, env, needed_libs):
        """check help and clean options"""
        if GetOption('help'):
            if self.requires:
                self.prereqs.require(env, *self.requires)
            return True

        self.set_environment(env, needed_libs)
        if GetOption('clean'):
            return True
        return False

    def _rm_old_dir(self, path):
        """remove the old dir"""
        if self.__dry_run:
            print 'Would empty %s' % path
        else:
            os.system("rm -rf %s" % path)
            os.mkdir(path)

    def _has_changes(self):
        """check for changes"""
        has_changes = self.prereqs.build_deps
        if "all" in self.prereqs.installed:
            has_changes = False
        if self.name in self.prereqs.installed:
            has_changes = False

        if self.src_exists():
            self.get()
            has_changes = self.has_changes()
        return has_changes

    def _check_prereqs_build_deps(self):
        """check for build dependencies"""
        if not self.prereqs.build_deps:
            if self.__dry_run:
                print 'Would do required build of %s' % self.name
            else:
                raise BuildRequired(self.name)

    def _update_crc_file(self):
        """update the crc"""
        new_crc = self.calculate_crc()
        if self.__dry_run:
            print 'Would create a new crc file % s' % self.crc_file
        else:
            with open(self.crc_file, 'w') as crcfile:
                crcfile.write(new_crc)

    def build(self, env, needed_libs):
        """Build the component, if necessary

        :param env: Scons Environment for building.
        :type env: environment
        :param needed_libs: Only configure libs in list
        :return: True if the component needed building.
        """
        # Ensure requirements are met
        changes = False
        envcopy = self.prereqs.system_env.Clone()

        if self.check_user_options(env, needed_libs):
            return True
        self.set_environment(envcopy, self.libs)
        if self.prebuilt_path:
            self.check_installed_package(envcopy)
            return False

        # Default to has_changes = True which will cause all deps
        # to be built first time scons is invoked.
        has_changes = self._has_changes()

        if changes or has_changes or self.has_missing_targets(envcopy):

            self._check_prereqs_build_deps()

            if not self.src_exists():
                self.get()

            self.prereqs.load_config(self.name, self.src_opt)

            if self.requires:
                changes = self.prereqs.require(envcopy, *self.requires,
                                               needed_libs=None)
                self.set_environment(envcopy, self.libs)

            if self.has_missing_system_deps(self.prereqs.system_env):
                raise MissingSystemLibs(self.name)

            changes = True
            if has_changes and self.out_of_src_build:
                self._rm_old_dir(self.build_path)
            if not RUNNER.run_commands(self.build_commands,
                                       subdir=self.build_path):
                raise BuildFailure(self.name)

        # set environment one more time as new directories may be present
        if self.requires:
            self.prereqs.require(envcopy, *self.requires, needed_libs=None)
        self.set_environment(envcopy, self.libs)
        if self.has_missing_targets(envcopy) and not self.__dry_run:
            raise MissingTargets(self.name, None)
        self._update_crc_file()
        return changes


__all__ = ["GitRepoRetriever", "WebRetriever",
           "DownloadFailure", "ExtractionError",
           "UnsupportedCompression", "BadScript",
           "MissingPath", "BuildFailure",
           "MissingDefinition", "MissingTargets",
           "MissingSystemLibs", "DownloadRequired",
           "PreReqComponent", "BuildRequired",
           "ProgramBinary"]
