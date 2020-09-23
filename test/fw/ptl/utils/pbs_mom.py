# coding: utf-8

# Copyright (C) 1994-2020 Altair Engineering, Inc.
# For more information, contact Altair at www.altair.com.
#
# This file is part of both the OpenPBS software ("OpenPBS")
# and the PBS Professional ("PBS Pro") software.
#
# Open Source License Information:
#
# OpenPBS is free software. You can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# OpenPBS is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public
# License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Commercial License Information:
#
# PBS Pro is commercially licensed software that shares a common core with
# the OpenPBS software.  For a copy of the commercial license terms and
# conditions, go to: (http://www.pbspro.com/agreement.html) or contact the
# Altair Legal Department.
#
# Altair's dual-license business model allows companies, individuals, and
# organizations to create proprietary derivative works of OpenPBS and
# distribute them - whether embedded or bundled with other software -
# under a commercial license agreement.
#
# Use of Altair's trademarks, including but not limited to "PBS™",
# "OpenPBS®", "PBS Professional®", and "PBS Pro™" and Altair's logos is
# subject to Altair's trademark licensing policies.

import ptl
import os
import logging
import time
import tempfile

from ptl.utils._pbs_mom import MoM
from ptl.utils.pbs_dshutils import DshUtils


def get_mom_obj(name=None, attrs={}, pbsconf_file=None, snapmap={},
                snap=None, server=None, db_access=None):

    du = DshUtils()
    platform = du.get_platform(hostname=name, pyexec="python")
    print("-------Inside get_mom_obj-----------")
    print("-----Name: %s; Platform: %s" %(name, platform))

    if "win32" in platform:
        return WinMoM(name, attrs, pbsconf_file, snapmap, snap, server, db_access)
    else:
        return MoM(name, attrs, pbsconf_file, snapmap, snap, server, db_access)


from ptl.lib.pbs_testlib import *


class WinMoM(MoM):

    """
    Container for MoM properties.
    Provides various MoM operations, such as creation, insertion,
    deletion of vnodes.

    :param name: The hostname of the server. Defaults to calling
                 pbs_default()
    :type name: str or None
    :param attrs: Dictionary of attributes to set, these will
                  override defaults.
    :type attrs: Dictionary
    :param pbsconf_file: path to config file to parse for
                         ``PBS_HOME``, ``PBS_EXEC``, etc
    :type pbsconf_file: str or None
    :param snapmap: A dictionary of PBS objects ``(node,server,etc)``
                    to mapped files from PBS snap directory
    :type snapmap: Dictionary
    :param snap: path to PBS snap directory (This will overrides
                 snapmap)
    :type snap: str or None
    :param server: A PBS server instance to which this mom is associated
    :param db_acccess: set to either file containing credentials to DB
                       access or dictionary containing
                       {'dbname':...,'user':...,'port':...}
    :type db_access: str or dictionary
    """
    dflt_attributes = {}
    conf_to_cmd_map = {'PBS_MOM_SERVICE_PORT': '-M',
                       'PBS_MANAGER_SERVICE_PORT': '-R',
                       'PBS_HOME': '-d'}

    def __init__(self, name=None, attrs={}, pbsconf_file=None, snapmap={},
                 snap=None, server=None, db_access=None):
        
        self.path_separator = '\\'
        if server is not None:
            self.server = server
            if snap is None and self.server.snap is not None:
                snap = self.server.snap
            if (len(snapmap) == 0) and (len(self.server.snapmap) != 0):
                snapmap = self.server.snapmap
        else:
            self.server = Server(name, pbsconf_file=pbsconf_file,
                                 db_access=db_access, snap=snap,
                                 snapmap=snapmap)

        dflt_conf = "C:\Program Files (x86)\PBS\pbs.conf"

        pc = ('"import os;print([False, os.environ[\'PBS_CONF_FILE\']]'
              '[\'PBS_CONF_FILE\' in os.environ])"')
        pyexec = 'python'
        cmd = [pyexec, '-c', pc]
        self.du = DshUtils()
        ret = self.du.run_cmd(name, cmd, logerr=False)
        if ((ret['rc'] == 0) and (len(ret['out']) > 0) and
                (ret['out'][0] != 'False')):
            dflt_conf = ret['out'][0]
        pbsconf_file = dflt_conf
        self.logger.info("-----Inside Mom's _init_; pbsconf_file:%s" %(pbsconf_file))

        # PBSService.__init__(self, name, attrs, self.dflt_attributes,
                            # pbsconf_file, snap=snap, snapmap=snapmap)
        self.pbsservice_init(name, attrs, self.dflt_attributes,
                            pbsconf_file, snap=snap, snapmap=snapmap)

        _m = ['mom ', self.shortname]
        if pbsconf_file is not None:
            _m += ['@', pbsconf_file]
        _m += [': ']
        self.logprefix = "".join(_m)
        
        # Windows Implementation of InitServices
        self.pi = PBSInitMoM(hostname=self.hostname,
                                  conf=self.pbs_conf_file)
        self.configd = self.path_separator.join([self.pbs_conf['PBS_HOME'], 'mom_priv',
                                    'config.d'])
        self.config = {}
        self.dflt_config = {'$clienthost': self.server.hostname}
        self.version = None
        self._is_cpuset_mom = None

        # If this is true, the mom will revert to default.
        # This is true by default, but can be set to False if
        # required by a test
        self.revert_to_default = True
        self.du = DshUtils()
        self.processes = {}
        self.sleep_cmd = 'pbs_sleep'
        self.user_home_dir = self.get_user_dir(self.hostname, 'home')
        self.user_temp_dir = self.get_user_dir(self.hostname, 'temp')

    def __del__(self):
        del self.__dict__
    
    def pbsservice_init(self, name=None, attrs=None, defaults=None, pbsconf_file=None,
                        snapmap=None, snap=None):
        if attrs is None:
            attrs = {}
        if defaults is None:
            defaults = {}
        if name is None:
            self.hostname = socket.gethostname()
        else:
            self.hostname = name
        

        self.fqdn = self.hostname
        self.shortname = self.hostname.split('.')[0]
        self.platform = self.du.get_platform()

        self.logutils = None
        self.logfile = None
        self.acctlogfile = None
        self.pbs_conf = {}
        self.pbs_env = {}
        # setting _is_local to False by default as MoM will
        # be a different machine always
        self._is_local = False
        self.launcher = None
        self.dyn_created_files = []
        self.saved_config = {}

        PBSObject.__init__(self, name, attrs, defaults)

        self.pbs_conf_file = pbsconf_file

        if self.pbs_conf_file == '/etc/pbs.conf':
            self.default_pbs_conf = True
        elif (('PBS_CONF_FILE' not in os.environ) or
              (os.environ['PBS_CONF_FILE'] != self.pbs_conf_file)):
            self.default_pbs_conf = False
        else:
            self.default_pbs_conf = True

        # default pbs_server_name to hostname, it will get set again once the
        # config file is processed
        self.pbs_server_name = self.hostname

        rv = self.print_file(self.hostname, self.pbs_conf_file)
        # implementation of _parse_file
        try:
            props = {}
            for l in rv['out']:
                if l.find('=') != -1 and l[0] != '#':
                    c = l.split('=')
                    props[c[0]] = c[1].strip()
        except BaseException:
            self.logger.error('error parsing file ' + str(file))
            self.logger.error(traceback.print_exc())
        self.pbs_conf = props

        if self.pbs_conf is None or len(self.pbs_conf) == 0:
            self.pbs_conf = {'PBS_HOME': "", 'PBS_EXEC': ""}
        else:
            ef = self.path_separator.join([self.pbs_conf['PBS_HOME'], 'pbs_environment'])
            # self.pbs_env = self.du.parse_pbs_environment(self.hostname, ef)
            self.pbs_server_name = self.du.get_pbs_server_name(
                self.pbs_conf)

        # Implement init_logfile_path 
        # Initialize path to log files for Mom
        # This will want the date for Server and MoM machine to be synced
        # No need for accounting log as it is part of server
        if self.pbs_conf is not None and 'PBS_HOME' in self.pbs_conf:
            tm = time.strftime("%Y%m%d", time.localtime())
            self.logfile = self.path_separator.join([self.pbs_conf['PBS_HOME'], 'mom_logs', tm])

    def get_user_dir(self, hostname=None, directory=None, user=None):
        """
        """
        if hostname is None:
            hostname = self.hostname
        
        if directory is None:
            self.logger.error("Can't get user directory")
        
        cmd = ['ssh', hostname]
        if directory == 'temp':
            cmd += ['echo %TEMP%']
        elif directory == 'home':
            cmd += ['echo %USERPROFILE%']
        
        ret = self.du.run_cmd(cmd=cmd, runas=user)
        if ret['rc'] == 0:
            return ret['out'][0]
        else:
            return ret['err']


    def _init_processes(self):
        self.processes = {}
    
    def print_file(self, hostname=None, path=None):
        """
        """
        if hostname is None:
            hostname = self.hostname
        if path is None:
            self.logger.info("No path provided to print the file")
            return None
        path = '"' + path + '"'
        cmd = ['type', path]
        rv = self.du.run_cmd(self.hostname, cmd=cmd)
        return rv

        
    def delete_file(self, hostname=None, path=None, sudo=False, runas=None,
           recursive=False, force=False, cwd=None, logerr=True,
           as_script=False, level=logging.INFOCLI2):
        """
        Generic function of deleting file

        :param hostname: hostname (default Mom's host)
        :type hostname: str or None
        :param path: the path to the files or directories to remove
                     for more than one files or directories pass as
                     list
        :type path: str or None
        :param sudo: whether to remove files or directories as root
                     or not.Defaults to False
        :type sudo: boolean
        :param runas: remove files or directories as given user
                      Defaults to calling user
        :param recursive: remove files or directories and their
                          contents recursively
        :type recursive: boolean
        :param force: force remove files or directories
        :type force: boolean
        :param cwd: working directory on local host from which
                    command is run
        :param logerr: whether to log error messages or not.
                       Defaults to True.
        :type logerr: boolean
        :param as_script: if True, run the rm in a script created
                          as a temporary file that gets deleted after
                          being run. This is used mainly to handle
                          wildcard in path list. Defaults to False.
        :type as_script: boolean
        :param level: logging level, defaults to INFOCLI2
        :returns: True on success otherwise False
        """
        if (path is None) or (len(path) == 0):
            return True

        cmd = ['del /Q']
        if recursive:
            cmd += ['/S']
        if force:
            cmd += ['/F']

        if isinstance(path, list):
            for p in path:
                if p == '/':
                    msg = 'encountered a dangerous package path ' + p
                    self.logger.error(msg)
                    return False
            cmd += path
        else:
            if path == '/':
                msg = 'encountered a dangerous package path ' + path
                self.logger.error(msg)
                return False
            cmd += [path]

        ret = self.du.run_cmd(hostname, cmd=cmd, sudo=sudo, logerr=logerr,
                           runas=runas, cwd=cwd, level=level,
                           as_script=as_script)
        if ret['rc'] != 0:
            return False
        return True
    
    def create_temp_file(self, hostname=None, suffix='', prefix='PtlPbs',
                         dirname=None, text=False, asuser=None, body=None):
        """
        Create a temp file by calling tempfile.mkstemp

        :param hostname: the hostname on which to query tempdir from
        :type hostname: str or None
        :param suffix: the file name will end with this suffix
        :type suffix: str
        :param prefix: the file name will begin with this prefix
        :type prefix: str
        :param dirname: the file will be created in this directory
        :type dirname: str or None
        :param text: the file is opened in text mode is this is true
                     else in binary mode
        :type text: boolean
        :param asuser: Optional username or uid of temp file owner
        :type asuser: str or None
        :param body: Optional content to write to the temporary file
        :type body: str or None
        :param level: logging level, defaults to INFOCLI2
        :type level: int
        """

        # create a temp file as current user
        (fd, tmpfile) = tempfile.mkstemp(suffix, prefix, dirname, text)

        # write user provided contents to file
        if body is not None:
            if isinstance(body, list):
                os.write(fd, "\n".join(body).encode())
            else:
                os.write(fd, body.encode())
        os.close(fd)

        if not hostname and asuser:
            asuser = PbsUser.get_user(asuser)
            if asuser.host:
                hostname = asuser.host

        destination = None
        # if temp file to be created on remote host
        if not self.du.is_localhost(hostname):
            destination = self.get_user_dir(hostname, 'temp', asuser)
            filename = tmpfile.split('/')[-1]
            destination = destination + '\\' + filename
            if asuser is not None:
                # by default mkstemp creates file with 0600 permission
                # to create file as different user first change the file
                # permission to 0644 so that other user has read permission
                self.du.chmod(path=tmpfile, mode=0o644)
                # Changing the ownership here as when stageout happens
                # the scp fails saying permission error
                self.du.chown(path=tmpfile, uid=asuser, sudo=True)
                # copy temp file created  on local host to remote host
                # as different user
                self.du.run_copy(hostname, src=tmpfile, dest=destination,
                              runas=asuser, preserve_permission=False)
            else:
                # copy temp file created on localhost to remote as current user
                self.du.run_copy(hostname, src=tmpfile, dest=destination,
                              preserve_permission=False)
                # remove local temp file
                os.unlink(tmpfile)
        '''if asuser is not None:
            # by default mkstemp creates file with 0600 permission
            # to create file as different user first change the file
            # permission to 0644 so that other user has read permission
            self.du.chmod(hostname, tmpfile, mode=0o644)
            # since we need to create as differnt user than current user
            # create a temp file just to get temp file name with absolute path
            (_, tmpfile2) = tempfile.mkstemp(suffix, prefix, dirname, text)
            # remove the newly created temp file
            os.unlink(tmpfile2)
            # copy the orginal temp as new temp file
            self.du.run_copy(hostname, src=tmpfile, dest=tmpfile2, runas=asuser,
                          preserve_permission=False)
            # remove original temp file
            os.unlink(tmpfile)
            #self.tmpfilelist.append(tmpfile2)
            return tmpfile2'''
        #self.tmpfilelist.append(tmpfile)
        return destination

    def _get_proc_info(self, hostname=None, name=None,
                       pid=None, regexp=False):
        """
        Helper function to ``get_proc_info``
        """
        platform = None

        (ps_cmd, ps_arg) = ('tasklist', '')
        if name is not None:
            if not regexp:
                cr = self.du.run_cmd(hostname, (ps_cmd + [ps_arg, name]),
                                     level=logging.DEBUG2)
            else:
                cr = self.du.run_cmd(hostname, ps_cmd, level=logging.DEBUG2)
        elif pid is not None:
            cr = self.du.run_cmd(hostname, ps_cmd + ['-p', pid],
                                 level=logging.DEBUG2)
        else:
            return

        if cr['rc'] == 0 and cr['out']:
            for proc in cr['out']:
                _pi = None
                try:
                    _s = proc.split()
                    p = _s[1]
                    rss = None
                    vsz = None
                    pcpu = None
                    command = _s[0]
                except:
                    continue
                if ((pid is not None and p == str(pid)) or
                    (name is not None and (
                        (regexp and re.search(name, command) is not None) or
                        (not regexp and name in command)))):
                    _pi = ProcInfo(name=command)
                    _pi.pid = p
                    _pi.rss = rss
                    _pi.vsz = vsz
                    _pi.pcpu = pcpu
                    _pi.command = command

                if _pi is not None:
                    if command in self.processes:
                        self.processes[command].append(_pi)
                    else:
                        self.processes[command] = [_pi]
        return self.processes

    def get_proc_info(self, hostname=None, name=None, pid=None, regexp=False):
        """
        Return process information from a process name, or pid,
        on a given host
        :param hostname: The hostname on which to query the process
                         info. On Windows,only localhost is queried.
        :type hostname: str or none
        :param name: The name of the process to query.
        :type name: str or None
        :param pid: The pid of the process to query
        :type pid: int or None
        :param regexp: Match processes by regular expression. Defaults
                       to True. Does not apply to matching by PID.
        :type regexp: bool
        :returns: A list of ProcInfo objects, one for each matching
                  process.
        .. note:: If both, name and pid, are specified, name is used.
        """
        self._init_processes()
        return self._get_proc_info(hostname, name, pid, regexp)

    def _all_instance_pids(self):
        """
        Return a list of all ``PIDS`` that match the
        instance name or None.
        """
        cmd = 'pbs_mom'
        self.get_proc_info(self.hostname, ".*" + cmd + ".*",
                           regexp=True)
        _procs = self.processes.values()
        if _procs:
            _pids = []
            for _p in _procs:
                _pids.extend([x.pid for x in _p])
            return _pids
        return None

    def _get_pid(self):
        """
        Get the ``PID`` associated to this instance.
        Implementation note, the pid is read from the
        daemon's lock file.

        This is different than _all_instance_pids in that
        the PID of the last running instance can be retrieved
        with ``_get_pid`` but not with ``_all_instance_pids``
        """
        path = self.path_separator.join([self.pbs_conf['PBS_HOME'], 'mom_priv', 'mom.lock'])
        rv = self.print_file(self.hostname, path)
        if ((rv['rc'] == 0) and (len(rv['out']) > 0)):
            pid = rv['out'][0].strip()
        else:
            pid = None
        return pid
    
    def _validate_pid(self):
        """
        Get pid and validate
        :param inst: inst to update pid
        :type inst: object
        """
        for i in range(30):
            live_pids = self._all_instance_pids()
            pid = self._get_pid()
            if live_pids is not None and pid in live_pids:
                return pid
            time.sleep(1)
        return None
    
    def get_stagein_cmd(self, execution_info={}, storage_info={}, asuser=None):
        """
        """
        if storage_info['hostname'] is None:
            storage_host = self.server.hostname
        else:
            storage_host = storage_info['hostname']

        storage_path = self.du.create_temp_file(storage_host, storage_info['suffix'], storage_info['prefix'],
                                     asuser=asuser)

        execution_path = self.get_user_dir(self.hostname, 'temp', asuser)
        
        cmd = '"%s@%s:%s"' % (execution_path, storage_host, storage_path)
        return cmd
    
    def get_stageout_cmd(self, execution_info={}, storage_info={}, asuser=None):
        """
        """
        if storage_info['hostname'] is None:
            storage_host = self.server.hostname
        else:
            storage_host = storage_info['hostname']

        storage_path = tempfile.gettempdir()

        if execution_info['hostname'] is None:
            execution_host = self.hostname
        else:
            execution_host = execution_info['hostname']
        execution_path = self.create_temp_file(execution_host, execution_info['suffix'], execution_info['prefix'],
                                     asuser=asuser)
        
        cmd = '"%s@%s:%s"' % (execution_path, storage_host, storage_path)
        return cmd

    def run_printjob(self, hostname=None, job_id=None):
        """
        Run the printjob command for the given job id
        :param hostname: mom hostname
        :type hostname: string
        :param job_id: job's id for which to run printjob cmd
        :type job_id: string
        """
        if hostname is None:
            hostname = self.hostname
        
        if job_id is None:
            return None
        
        printjob = self.path_separator.join([self.pbs_conf['PBS_EXEC'], 'bin',
                                'printjob_host'])
        jbfile = self.path_separator.join([self.pbs_conf['PBS_HOME'], 'mom_priv',
                             'jobs', job_id + '.JB'])
        printjob = '"' + printjob + '"'
        jbfile = '"' + jbfile + '"'
        ret = self.du.run_cmd(hostname, cmd=[printjob, jbfile])
        return ret
    
    def is_suspended_state(self, hostname=None, pid=None):
        """
        """

        if hostname is None:
            hostname = self.hostname
        
        if pid is None:
            self.logger.error("Could not get pid to check the state")
            return False
        
        cmd = 'powershell -command \"(Get-Process -Id ' + pid + ').Threads | select WaitReason | Format-List\"'
        ret = self.du.run_cmd(hostname, cmd=cmd)
        if ret['rc'] == 0:
            for rv in ret['out']:
                self.logger.info("-----Inside is_suspended_state-----rv:%s" %(rv))
                if rv.startswith('WaitReason :') and rv.split(':')[1] != ' Suspended':
                    return False
        else:
            self.logger.error("Could not get process state from Mom")
            return False
        return True

    def _isUp(self, inst):
        """
        returns True if MoM service is up and False otherwise
        """
        live_pids = self._all_instance_pids()
        pid = self._get_pid()
        if live_pids is not None and pid in live_pids:
            return True
        return False

    def isUp(self, max_attempts=None):
        """
        Check for PBS mom up
        """
        # Poll for few seconds to see if mom is up and node is free
        if max_attempts is None:
            max_attempts = self.ptl_conf['max_attempts']
        for _ in range(max_attempts):
            rv = self._isUp(self)
            if rv:
                break
            time.sleep(1)
        if rv:
            try:
                nodes = self.server.status(NODE, id=self.shortname)
                if nodes:
                    attr = {'state': (MATCH_RE,
                                      'free|provisioning|offline|job-busy')}
                    self.server.expect(NODE, attr, id=self.shortname)
            # Ignore PbsStatusError if mom daemon is up but there aren't
            # any mom nodes
            except PbsStatusError:
                pass
            except PtlExpectError:
                rv = False
        return rv

    def _signal(self, sig, inst=None, procname=None):
        """
        Send signal ``sig`` to service. sig is the signal name
        as it would be sent to the program kill, e.g. -HUP.

        Return the ``out/err/rc`` from the command run to send
        the signal. See DshUtils.run_cmd

        :param inst: Instance
        :type inst: str
        :param procname: Process name
        :type procname: str or None
        """

        pid = self._get_pid()

        cmd = ['taskkill', '/F', '/PID']
        if procname is not None:
            pi = self.get_proc_info(self.hostname, procname)
            if pi is not None and pi.values() and list(pi.values())[0]:
                for _p in list(pi.values())[0]:
                    cmd += [_p.pid]
                    if sig is '-HUP':
                        cmd += ['&', 'net start pbs_mom']
                    ret = self.du.run_cmd(self.hostname, cmd)
                return ret

        if pid is None:
            return {'rc': 0, 'err': '', 'out': 'no pid to signal'}

        cmd += [pid]
        if sig == '-HUP':
            cmd += ['&&', 'net start pbs_mom']

        ret = self.du.run_cmd(self.hostname, cmd)
        if ret['rc'] == 0:
            # Doing this because of read permission error of mom.lock file
            cmd = ['net stop pbs_mom && net start pbs_mom']
            ret = self.du.run_cmd(self.hostname, cmd)
        return ret
        

    def signal(self, sig):
        """
        Send signal to PBS mom
        """
        self.logger.info(self.logprefix + 'sent signal ' + sig)
        return self._signal(sig)

    def get_pid(self):
        """
        Get the PBS mom pid
        """
        # Windows Implementation
        return super(MoM, self)._get_pid()

    def all_instance_pids(self):
        """
        Get all pids of a instance
        """
        return super(MoM, self)._all_instance_pids(inst=self)

    def start(self, args=None, launcher=None):
        """
        Start the PBS mom

        :param args: Arguments to start the mom
        :type args: str or None
        :param launcher: Optional utility to invoke the launch of the service
        :type launcher: str or list or None
        """
        
        try:
            rv = self.pi.start_mom()
            # We can add below logic when the permission error of lock file is solved
            # pid = self._validate_pid()
            if pid is None:
                raise PbsServiceError(rv=False, rc=-1,
                                      msg="Could not find PID")
        except PbsInitServicesError as e:
            raise PbsServiceError(rc=e.rc, rv=e.rv, msg=e.msg)
        return rv

    def stop(self, sig=None):
        """
        Stop the PBS mom

        :param sig: Signal to stop the PBS mom
        :type sig: str
        """
        if sig is not None:
            self.logger.info(self.logprefix + 'stopping MoM on host ' +
                             self.hostname)
            return super(MoM, self)._stop(sig, inst=self)
        else:
            try:
                self.pi.stop_mom()
            except PbsInitServicesError as e:
                raise PbsServiceError(rc=e.rc, rv=e.rv, msg=e.msg)
            return True

    def restart(self):
        """
        Restart the PBS mom
        """
        # if self.isUp():
            # if not self.stop():
                # return False
        # return self.start()
        # We can add above logic when the permission error of lock file is solved
        cmd = ['net stop pbs_mom && net start pbs_mom']
        self.du.run_cmd(self.hostname, cmd=cmd)

    def log_lines(self, logtype, id=None, n=50, tail=True, starttime=None,
                  endtime=None, host=None):
        """
        Return the last ``<n>`` lines of a PBS log file, which
        can be one of ``server``, ``scheduler``, ``MoM``, or
        ``tracejob``

        :param logtype: The entity requested, an instance of a
                        Scheduler, Server or MoM object, or the
                        string 'tracejob' for tracejob
        :type logtype: str or object
        :param id: The id of the object to trace. Only used for
                   tracejob
        :param n: One of 'ALL' of the number of lines to
                  process/display, defaults to 50.
        :type n: str or int
        :param tail: if True, parse log from the end to the start,
                     otherwise parse from the start to the end.
                     Defaults to True.
        :type tail: bool
        :param day: Optional day in ``YYYMMDD`` format. Defaults
                    to current day
        :type day: int
        :param starttime: date timestamp to start matching
        :param endtime: date timestamp to end matching
        :param host: Hostname
        :type host: str
        :returns: Last ``<n>`` lines of logfile for ``Server``,
                  ``Scheduler``, ``MoM or tracejob``
        """
        logval = None
        lines = []
        sudo = False
        if endtime is None:
            endtime = time.time()
        if starttime is None:
            starttime = self.ctime
        if host is None:
            host = self.hostname
        try:
            if logtype == 'tracejob':
                if id is None:
                    return None
                cmd = [self.path_separator.join([
                       self.pbs_conf['PBS_EXEC'],
                       'bin',
                       'tracejob'])]
                cmd += [str(id)]
                lines = self.du.run_cmd(host, cmd)['out']
                if n != 'ALL':
                    lines = lines[-n:]
            else:
                daystart = time.strftime("%Y%m%d", time.localtime(starttime))
                dayend = time.strftime("%Y%m%d", time.localtime(endtime))
                firstday_obj = datetime.datetime.strptime(daystart, '%Y%m%d')
                lastday_obj = datetime.datetime.strptime(dayend, '%Y%m%d')
                logval = 'mom_logs'
                logdir = self.path_separator.join([self.pbs_conf['PBS_HOME'], logval])
                while firstday_obj <= lastday_obj:
                    day = firstday_obj.strftime("%Y%m%d")
                    filename = self.path_separator.join([logdir, day])
                    self.logger.info("-----Inside log_lines, filename=%s" %(filename))
                    if n == 'ALL':
                        day_lines = self.print_file(self.hostname, filename)['out']
                    else:
                        if tail:
                            cmd = ['/usr/bin/tail']
                        else:
                            cmd = ['/usr/bin/head']

                        cmd += ['-n']
                        cmd += [str(n), filename]
                        day_lines = self.du.run_cmd(
                            host, cmd, sudo=sudo,
                            level=logging.DEBUG2)['out']
                    lines.extend(day_lines)
                    firstday_obj = firstday_obj + datetime.timedelta(days=1)
                    if n == 'ALL':
                        continue
                    n = n - len(day_lines)
                    if n <= 0:
                        break
        except (Exception, IOError, PtlLogMatchError):
            self.logger.error('error in log_lines ')
            self.logger.error(traceback.print_exc())
            return None

        return lines

    def _log_match(self, logtype, msg, id=None, n=50, tail=True,
                   allmatch=False, regexp=False, max_attempts=None,
                   interval=None, starttime=None, endtime=None,
                   level=logging.INFO, existence=True):
        """
        Match given ``msg`` in given ``n`` lines of log file

        :param logtype: The entity requested, an instance of a
                        Scheduler, Server, or MoM object, or the
                        strings 'tracejob' for tracejob or
                        'accounting' for accounting logs.
        :type logtype: object
        :param msg: log message to match, can be regex also when
                    ``regexp`` is True
        :type msg: str
        :param id: The id of the object to trace. Only used for
                   tracejob
        :type id: str
        :param n: 'ALL' or the number of lines to search through,
                  defaults to 50
        :type n: str or int
        :param tail: If true (default), starts from the end of
                     the file
        :type tail: bool
        :param allmatch: If True all matching lines out of then
                         parsed are returned as a list. Defaults
                         to False
        :type allmatch: bool
        :param regexp: If true msg is a Python regular expression.
                       Defaults to False
        :type regexp: bool
        :param max_attempts: the number of attempts to make to find
                             a matching entry
        :type max_attempts: int
        :param interval: the interval between attempts
        :type interval: int
        :param starttime: If set ignore matches that occur before
                          specified time
        :type starttime: float
        :param endtime: If set ignore matches that occur after
                        specified time
        :type endtime: float
        :param level: The logging level, defaults to INFO
        :type level: int
        :param existence: If True (default), check for existence of
                        given msg, else check for non-existence of
                        given msg.
        :type existence: bool

        :return: (x,y) where x is the matching line
                 number and y the line itself. If allmatch is True,
                 a list of tuples is returned.
        :rtype: tuple
        :raises PtlLogMatchError:
                When ``existence`` is True and given
                ``msg`` is not found in ``n`` line
                Or
                When ``existence`` is False and given
                ``msg`` found in ``n`` line.

        .. note:: The matching line number is relative to the record
                  number, not the absolute line number in the file.
        """
        try:
            from ptl.utils.pbs_logutils import PBSLogUtils
        except:
            _msg = 'error loading ptl.utils.pbs_logutils'
            raise ImportError(_msg)

        if self.logutils is None:
            self.logutils = PBSLogUtils()
        if max_attempts is None:
            max_attempts = self.ptl_conf['max_attempts']
        if interval is None:
            interval = self.ptl_conf['attempt_interval']
        rv = (None, None)
        attempt = 1
        lines = None
        name = self._instance_to_servicename(logtype)
        infomsg = (name + ' ' + self.shortname +
                   ' log match: searching for "' + msg + '"')
        if regexp:
            infomsg += ' - using regular expression '
        if allmatch:
            infomsg += ' - on all matches '
        if existence:
            infomsg += ' - with existence'
        else:
            infomsg += ' - with non-existence'
        attemptmsg = ' - No match'
        while attempt <= max_attempts:
            if attempt > 1:
                attemptmsg = ' - attempt ' + str(attempt)
            lines = self.log_lines(logtype, id, n=n, tail=tail,
                                   starttime=starttime, endtime=endtime)
            rv = self.logutils.match_msg(lines, msg, allmatch=allmatch,
                                         regexp=regexp, starttime=starttime,
                                         endtime=endtime)
            if not existence:
                if rv:
                    _msg = infomsg + ' - but exists'
                    #raise PtlLogMatchError(rc=1, rv=False, msg=_msg)
                else:
                    self.logger.log(level, infomsg + attemptmsg + '... OK')
                    break
            if rv:
                self.logger.log(level, infomsg + '... OK')
                break
            else:
                if n != 'ALL':
                    if attempt > max_attempts:
                        # We will do one last attempt to match in case the
                        # number of lines that were provided did not capture
                        # the start or end time of interest
                        max_attempts += 1
                    n = 'ALL'
                self.logger.log(level, infomsg + attemptmsg)
            attempt += 1
            time.sleep(interval)
        try:
            # Depending on whether the hostname is local or remote and whether
            # sudo privileges were required, lines returned by log_lines can be
            # an open file descriptor, we close here but ignore errors in case
            # any were raised for all irrelevant cases
            lines.close()
        except:
            pass
        if (rv is None and existence):
            _msg = infomsg + attemptmsg
            raise PtlLogMatchError(rc=1, rv=False, msg=_msg)
        return rv

    def log_match(self, msg=None, id=None, n='ALL', tail=True, allmatch=False,
                  regexp=False, max_attempts=None, interval=None,
                  starttime=None, endtime=None, level=logging.INFO,
                  existence=True):
        """
        Match given ``msg`` in given ``n`` lines of MoM log

        :param msg: log message to match, can be regex also when
                    ``regexp`` is True
        :type msg: str
        :param id: The id of the object to trace. Only used for
                   tracejob
        :type id: str
        :param n: 'ALL' or the number of lines to search through,
                  defaults to 50
        :type n: str or int
        :param tail: If true (default), starts from the end of
                     the file
        :type tail: bool
        :param allmatch: If True all matching lines out of then
                         parsed are returned as a list. Defaults
                         to False
        :type allmatch: bool
        :param regexp: If true msg is a Python regular expression.
                       Defaults to False
        :type regexp: bool
        :param max_attempts: the number of attempts to make to find
                             a matching entry
        :type max_attempts: int
        :param interval: the interval between attempts
        :type interval: int
        :param starttime: If set ignore matches that occur before
                          specified time
        :type starttime: float
        :param endtime: If set ignore matches that occur after
                        specified time
        :type endtime: float
        :param level: The logging level, defaults to INFO
        :type level: int
        :param existence: If True (default), check for existence of
                        given msg, else check for non-existence of
                        given msg.
        :type existence: bool

        :return: (x,y) where x is the matching line
                 number and y the line itself. If allmatch is True,
                 a list of tuples is returned.
        :rtype: tuple
        :raises PtlLogMatchError:
                When ``existence`` is True and given
                ``msg`` is not found in ``n`` line
                Or
                When ``existence`` is False and given
                ``msg`` found in ``n`` line.

        .. note:: The matching line number is relative to the record
                  number, not the absolute line number in the file.
        """
        return self._log_match(self, msg, id, n, tail, allmatch, regexp,
                               max_attempts, interval, starttime, endtime,
                               level, existence)

    def pbs_version(self):
        """
        Get the PBS version
        """
        if self.version:
            return self.version

        exe = self.path_separator.join([self.pbs_conf['PBS_EXEC'], 'sbin', 'pbs_mom'])
        exe = '"' + exe + '"'
        version = self.du.run_cmd(self.hostname,
                                  [exe, '--version'])['out']
        if version:
            self.logger.debug(version)
            # in some cases pbs_mom --version may return multiple lines, we
            # only care about the one that carries pbs_version information
            for ver in version:
                if 'pbs_version' in ver:
                    version = ver.split('=')[1].strip()
                    break
        else:
            version = self.log_match('pbs_version', tail=False)
            if version:
                version = version[1].strip().split('=')[1].strip()
            else:
                version = "unknown"

        self.version = LooseVersion(version)

        return self.version

    def delete_vnodes(self):
        rah = ATTR_rescavail + '.host'
        rav = ATTR_rescavail + '.vnode'
        a = {rah: self.hostname, rav: None}
        try:
            _vs = self.server.status(HOST, a, id=self.hostname)
        except PbsStatusError:
            try:
                _vs = self.server.status(HOST, a, id=self.shortname)
            except PbsStatusError as e:
                err_msg = e.msg[0].rstrip()
                if (err_msg.endswith('Server has no node list') or
                        err_msg.endswith('Unknown node')):
                    _vs = []
                else:
                    raise e
        vs = []
        for v in _vs:
            if v[rav].split('.')[0] != v[rah].split('.')[0]:
                vs.append(v['id'])
        if len(vs) > 0:
            self.server.manager(MGR_CMD_DELETE, VNODE, id=vs)

    def _get_dflt_pbsconfval(self, conf, svr_hostname, hosttype, hostobj):
        """
        Helper function to revert_pbsconf, tries to determine and return
        default value for the pbs.conf variable given

        :param conf: the pbs.conf variable
        :type conf: str
        :param svr_hostname: hostname of the server host
        :type svr_hostname: str
        :param hosttype: type of host being reverted
        :type hosttype: str
        :param hostobj: PTL object associated with the host
        :type hostobj: PBSService

        :return default value of the pbs.conf variable if it can be determined
        as a string, otherwise None
        """
        if conf == "PBS_SERVER":
            return svr_hostname
        elif conf == "PBS_START_SCHED":
            return "0"
        elif conf == "PBS_START_COMM":
            return "0"
        elif conf == "PBS_START_SERVER":
            return "0"
        elif conf == "PBS_START_MOM":
            return "1"
        elif conf == "PBS_CORE_LIMIT":
            return "unlimited"
        elif conf == "PBS_SCP":
            scppath = self.du.which(hostobj.hostname, "scp")
            if scppath != "scp":
                return scppath
        elif conf == "PBS_LOG_HIGHRES_TIMESTAMP":
            return "1"
        elif conf == "PBS_PUBLIC_HOST_NAME":
            return None
        elif conf == "PBS_DAEMON_SERVICE_USER":
            # Only set if scheduler user is not default
            if DAEMON_SERVICE_USER.name == 'root':
                return None
            else:
                return DAEMON_SERVICE_USER.name

        return None

    def revert_mom_pbs_conf(self, primary_server, vals_to_set):
        """
        Helper function to revert_pbsconf to revert all mom daemons' pbs.conf
        :param primary_server: object of the primary PBS server
        :type primary_server: PBSService
        :param vals_to_set: dict of pbs.conf values to set
        :type vals_to_set: dict
        """
        '''svr_hostnames = [svr.hostname for svr in self.servers.values()]
        
        #for mom in moms.values():
        if self.hostname in svr_hostnames:
            return'''

        new_pbsconf = dict(vals_to_set)
        restart_mom = False
        if self.pbs_conf:
            pbs_conf_val = self.pbs_conf
        self.logger.info("----Inside revert_mom_pbs_conf; pbs_conf_val:%s" %(pbs_conf_val))
        if not pbs_conf_val:
            raise ValueError("Could not parse pbs.conf on host %s" %
                             (self.hostname))

        # to start with, set all keys in new_pbsconf with values from the
        # existing pbs.conf
        keys_to_delete = []
        for conf in new_pbsconf:
            if conf in pbs_conf_val:
                new_pbsconf[conf] = pbs_conf_val[conf]
            else:
                # existing pbs.conf doesn't have a default variable set
                # Try to determine the default
                val = self._get_dflt_pbsconfval(conf,
                                                primary_server.hostname,
                                                "mom", self)
                if val is None:
                    self.logger.error("Couldn't revert %s in pbs.conf"
                                      " to its default value" %
                                      (conf))
                    keys_to_delete.append(conf)
                else:
                    new_pbsconf[conf] = val

        for key in keys_to_delete:
            del(new_pbsconf[key])

        # Set the mom start bit to 1
        if (new_pbsconf["PBS_START_MOM"] != "1"):
            new_pbsconf["PBS_START_MOM"] = "1"
            restart_mom = True

        # Set PBS_CORE_LIMIT, PBS_SCP and PBS_SERVER
        if new_pbsconf["PBS_CORE_LIMIT"] != "unlimited":
            new_pbsconf["PBS_CORE_LIMIT"] = "unlimited"
            restart_mom = True
        if new_pbsconf["PBS_SERVER"] != primary_server.hostname:
            new_pbsconf["PBS_SERVER"] = primary_server.hostname
            restart_mom = True
        if "PBS_SCP" not in new_pbsconf:
            scppath = "scp.exe"
            if scppath != "scp":
                new_pbsconf["PBS_SCP"] = "scp.exe"
                restart_mom = True
        if new_pbsconf["PBS_LOG_HIGHRES_TIMESTAMP"] != "1":
            new_pbsconf["PBS_LOG_HIGHRES_TIMESTAMP"] = "1"
            restart_mom = True
        if "PBS_AUTH_METHOD" not in new_pbsconf or new_pbsconf["PBS_AUTH_METHOD"] != "pwd":
            new_pbsconf["PBS_AUTH_METHOD"] = "pwd"


        # Check if existing pbs.conf has more/less entries than the
        # default list
        if len(pbs_conf_val) != len(new_pbsconf):
            restart_mom = True
        # Check if existing pbs.conf has correct ownership
        #dest = self.du.get_pbs_conf_file(self.hostname)
        #(cf_uid, cf_gid) = (os.stat(dest).st_uid, os.stat(dest).st_gid)
        #if cf_uid != 0 or cf_gid > 10:
        
        # Setting this to False for now. We might remove this whole function 
        restart_mom = False
        self.logger.info("----Inside revert_mom_pbs_conf; new_pbsconf`:%s" %(new_pbsconf))
        if restart_mom:
            self.du.set_pbs_config(self.hostname, fin=self.pbs_conf_file, confs=new_pbsconf,
                                   append=False)
            self.pbs_conf = new_pbsconf
            self.logger.info("----Inside revert_mom_pbs_conf; self.pbs_conf:%s" %(self.pbs_conf))
            self.pi.initd(self.hostname, "restart", daemon="mom")
            if not self.isUp():
                self.fail("Mom is not up")

    def revert_to_defaults(self, delvnodedefs=True):
        """
        1. ``Revert MoM configuration to defaults.``

        2. ``Remove epilogue and prologue``

        3. ``Delete all vnode definitions
        HUP MoM``

        :param delvnodedefs: if True (the default) delete all vnode
                             definitions and restart the MoM
        :type delvnodedefs: bool

        :returns: True on success and False otherwise
        """
        self.logger.info(self.logprefix +
                         'reverting configuration to defaults')
        restart = False
        if not self.has_snap:
            self.delete_pelog()
            if delvnodedefs and self.has_vnode_defs():
                restart = True
                if not self.delete_vnode_defs():
                    return False
                self.delete_vnodes()
            if not (self.config == self.dflt_config):
                # Clear older mom configuration. Apply default.
                self.config = {}
                self.apply_config(self.dflt_config, hup=False, restart=True)
            if restart:
                self.restart()
            else:
                self.signal('-HUP')
            return self.isUp()
        return True

    def save_configuration(self, outfile=None, mode='w'):
        """
        Save a MoM ``mom_priv/config``

        :param outfile: Optional Path to a file to which configuration
                        is saved, when not provided, data is saved in
                        class variable saved_config
        :type outfile: str
        :param mode: the mode in which to open outfile to save
                     configuration.
        :type mode: str
        :returns: True on success, False on error

        .. note:: first object being saved should open this file
                  with 'w' and subsequent calls from other objects
                  should save with mode 'a' or 'a+'. Defaults to a+
        """
        conf = {}
        mpriv = self.path_separator.join([self.pbs_conf['PBS_HOME'], 'mom_priv'])
        cf = self.path_separator.join([mpriv, 'config'])
        self._save_config_file(conf, cf)

        if os.path.isdir(os.path.join(mpriv, 'config.d')):
            for f in self.du.listdir(path=os.path.join(mpriv, 'config.d'),
                                     sudo=True):
                self._save_config_file(conf,
                                       os.path.join(mpriv, 'config.d', f))
        mconf = {self.hostname: conf}
        if MGR_OBJ_NODE not in self.server.saved_config:
            self.server.saved_config[MGR_OBJ_NODE] = {}
        self.server.saved_config[MGR_OBJ_NODE].update(mconf)
        if outfile is not None:
            try:
                with open(outfile, mode) as f:
                    json.dump(self.server.saved_config, f)
            except:
                self.logger.error('error saving configuration to ' + outfile)
                return False
        return True

    def load_configuration(self, infile):
        """
        load mom configuration from saved file infile
        """
        rv = self._load_configuration(infile, MGR_OBJ_NODE)
        self.signal('-HUP')
        return rv

    def is_cray(self):
        """
        Not there in Windows platform
        """
        return False

    def is_shasta(self):
        """
        Not there in Windows platform
        """
        return False

    def is_cpuset_mom(self):
        """
        Not there in Windows platforms
        """
        return False

    def is_only_linux(self):
        """
        Not there in Windows platform
        """
        return False

    def create_vnode_def(self, name, attrs={}, numnodes=1, sharednode=True,
                         pre='[', post=']', usenatvnode=False, attrfunc=None,
                         vnodes_per_host=1):
        """
        Create a vnode definition string representation

        :param name: The prefix for name of vnode to create,
                     name of vnode will be prefix + pre + <num> +
                     post
        :type name: str
        :param attrs: Dictionary of attributes to set on each vnode
        :type attrs: Dictionary
        :param numnodes: The number of vnodes to create
        :type numnodes: int
        :param sharednode: If true vnodes are shared on a host
        :type sharednode: bool
        :param pre: The symbol preceding the numeric value of that
                    vnode.
        :type pre: str
        :param post: The symbol following the numeric value of that
                     vnode.
        :type post: str
        :param usenatvnode: use the natural vnode as the first vnode
                            to allocate this only makes sense
                            starting with PBS 11.3 when natural
                            vnodes are reported as a allocatable
        :type usenatvnode: bool
        :param attrfunc: function to customize the attributes,
                         signature is (name, numnodes, curnodenum,
                         attrs), must return a dict that contains
                         new or modified attrs that will be added to
                         the vnode def. The function is called once
                         per vnode being created, it does not modify
                         attrs itself across calls.
        :param vnodes_per_host: number of vnodes per host
        :type vnodes_per_host: int
        :returns: A string representation of the vnode definition
                  file
        """
        sethost = False

        attribs = attrs.copy()
        if not sharednode and 'resources_available.host' not in attrs:
            sethost = True

        if attrfunc is None:
            customattrs = attribs

        vdef = ["$configversion 2"]

        # altering the natural vnode information
        if numnodes == 0:
            for k, v in attribs.items():
                vdef += [name + ": " + str(k) + "=" + str(v)]
        else:
            if usenatvnode:
                if attrfunc:
                    customattrs = attrfunc(name, numnodes, "", attribs)
                for k, v in customattrs.items():
                    vdef += [self.shortname + ": " + str(k) + "=" + str(v)]
                # account for the use of the natural vnode
                numnodes -= 1
            else:
                # ensure that natural vnode is not allocatable by the scheduler
                vdef += [self.shortname + ": resources_available.ncpus=0"]
                vdef += [self.shortname + ": resources_available.mem=0"]

        for n in range(numnodes):
            vnid = name + pre + str(n) + post
            if sethost:
                if vnodes_per_host > 1:
                    if n % vnodes_per_host == 0:
                        _nid = vnid
                    else:
                        _nid = name + pre + str(n - n % vnodes_per_host) + post
                    attribs['resources_available.host'] = _nid
                else:
                    attribs['resources_available.host'] = vnid

            if attrfunc:
                customattrs = attrfunc(vnid, numnodes, n, attribs)
            for k, v in customattrs.items():
                vdef += [vnid + ": " + str(k) + "=" + str(v)]

        if numnodes == 0:
            nn = 1
        else:
            nn = numnodes
        if numnodes > 1:
            vnn_msg = ' vnodes '
        else:
            vnn_msg = ' vnode '

        self.logger.info(self.logprefix + 'created ' + str(nn) +
                         vnn_msg + name + ' with attr ' +
                         str(attribs) + ' on host ' + self.hostname)
        vdef += ["\n"]
        del attribs
        return "\n".join(vdef)

    def add_checkpoint_abort_script(self, dirname=None, body=None,
                                    abort_time=30):
        """
        Add checkpoint script in the mom config.
        returns: a temp file for checkpoint script
        """
        chk_file = self.du.create_temp_file(hostname=self.hostname, body=body,
                                            dirname=dirname)
        self.du.chmod(hostname=self.hostname, path=chk_file, mode=0o700)
        self.du.chown(hostname=self.hostname, path=chk_file, runas=ROOT_USER,
                      uid=0, gid=0)
        c = {'$action checkpoint_abort':
             str(abort_time) + ' !' + chk_file + ' %sid'}
        self.add_config(c)
        return chk_file

    def add_restart_script(self, dirname=None, body=None,
                           abort_time=30):
        """
        Only been used in one cgroups test
        Not valid for Windows Platform
        """
        return None

    def parse_config(self):
        """
        Parse mom config file into a dictionary of configuration
        options.

        :returns: A dictionary of configuration options on success,
                  and None otherwise
        """
        try:
            mconf = os.path.join(self.pbs_conf['PBS_HOME'], 'mom_priv',
                                 'config')
            ret = self.du.cat(self.hostname, mconf, sudo=True)
            if ret['rc'] != 0:
                self.logger.error('error parsing configuration file')
                return None

            self.config = {}
            lines = ret['out']
            for line in lines:
                if line.startswith('$action'):
                    (ac, k, v) = line.split(' ', 2)
                    k = ac + ' ' + k
                else:
                    (k, v) = line.split(' ', 1)
                if k in self.config:
                    if isinstance(self.config[k], list):
                        self.config[k].append(v)
                    else:
                        self.config[k] = [self.config[k], v]
                else:
                    self.config[k] = v
        except:
            self.logger.error('error in parse_config')
            return None

        return self.config

    def add_config(self, conf={}, hup=True):
        """
        Add config options to mom_priv_config.

        :param conf: The configurations to add to ``mom_priv/config``
        :type conf: Dictionary
        :param hup: If True (default) ``HUP`` the MoM
        :type hup: bool
        :returns: True on success and False otherwise
        """

        doconfig = False

        if not self.config:
            self.parse_config()

        mc = self.config

        if mc is None:
            mc = {}

        for k, v in conf.items():
            if k in mc and (mc[k] == v or (isinstance(v, list) and
                                           mc[k] in v)):
                self.logger.debug(self.logprefix + 'config ' + k +
                                  ' already set to ' + str(v))
                continue
            else:
                doconfig = True
                break

        if not doconfig:
            return True

        self.logger.info(self.logprefix + "config " + str(conf))

        return self.apply_config(conf, hup)

    def unset_mom_config(self, name, hup=True):
        """
        Delete a mom_config entry

        :param name: The entry to remove from ``mom_priv/config``
        :type name: String
        :param hup: if True (default) ``HUP`` the MoM
        :type hup: bool
        :returns: True on success and False otherwise
        """
        mc = self.parse_config()
        if mc is None or name not in mc:
            return True
        self.logger.info(self.logprefix + "unsetting config " + name)
        del mc[name]

        return self.apply_config(mc, hup)

    def apply_config(self, conf={}, hup=True, restart=False):
        """
        Apply configuration options to MoM.

        :param conf: A dictionary of configuration options to apply
                     to MoM
        :type conf: Dictionary
        :param hup: If True (default) , HUP the MoM to apply the
                    configuration
        :type hup: bool
        :returns: True on success and False otherwise.
        """
        self.config = {**self.config, **conf}
        try:
            fn = self.du.create_temp_file()
            with open(fn, 'w+') as f:
                for k, v in self.config.items():
                    if isinstance(v, list):
                        for eachprop in v:
                            f.write(str(k) + ' ' + str(eachprop) + '\n')
                    else:
                        f.write(str(k) + ' ' + str(v) + '\n')
            dest = self.path_separator.join(
                [self.pbs_conf['PBS_HOME'], 'mom_priv', 'config'])
            dest = '"' + dest + '"'
            self.du.run_copy(self.hostname, src=fn, dest=dest,
                             preserve_permission=False, sudo=False)
            os.remove(fn)
        except:
            raise PbsMomConfigError(rc=1, rv=False,
                                    msg='error processing add_config')
        if restart:
            return self.restart()
        elif hup:
            return self.signal('-HUP')

        return True

    def get_vnode_def(self, vnodefile=None):
        """
        :returns: A vnode def file as a single string
        """
        if vnodefile is None:
            return None
        with open(vnodefile) as f:
            lines = f.readlines()
        return "".join(lines)

    def insert_vnode_def(self, vdef, fname=None, additive=False, restart=True):
        """
        Insert and enable a vnode definition. Root privilege
        is required

        :param vdef: The vnode definition string as created by
                     create_vnode_def
        :type vdef: str
        :param fname: The filename to write the vnode def string to
        :type fname: str or None
        :param additive: If True, keep all other vnode def files
                         under config.d Default is False
        :type additive: bool
        :param delete: If True, delete all nodes known to the server.
                       Default is True
        :type delete: bool
        :param restart: If True, restart the MoM. Default is True
        :type restart: bool
        """
        try:
            fn = self.create_temp_file(self.hostname, body=vdef)
        except:
            raise PbsMomConfigError(rc=1, rv=False,
                                    msg="Failed to insert vnode definition")
        if fname is None:
            fname = 'pbs_vnode_' + str(int(time.time())) + '.def'
        if not additive:
            self.delete_vnode_defs()
        path = self.path_separator.join([self.pbs_conf['PBS_EXEC'], 'sbin', 'pbs_mom'])
        path = '"' + path + '"'
        cmd = [path, '-N', '-s', 'insert', fname, fn]
        ret = self.du.run_cmd(self.hostname, cmd, sudo=False, logerr=False,
                              level=logging.INFOCLI)
        # self.delete_file(hostname=self.hostname, path=fn, force=True)
        if ret['rc'] != 0:
            raise PbsMomConfigError(rc=1, rv=False, msg="\n".join(ret['err']))
        msg = self.logprefix + 'inserted vnode definition file '
        msg += fname + ' on host: ' + self.hostname
        self.logger.info(msg)
        if restart:
            self.restart()

    def has_vnode_defs(self):
        """
        Check for vnode definition(s)
        """
        path = self.path_separator.join([self.pbs_conf['PBS_EXEC'], 'sbin', 'pbs_mom'])
        path = '"' + path + '"'
        cmd = [path, '-N', '-s', 'list']
        # removed sudo=True because it was giving sudo -H and it was not working
        ret = self.du.run_cmd(self.hostname, cmd, logerr=False,
                              level=logging.INFOCLI)
        if ret['rc'] == 0:
            files = [x for x in ret['out'] if not x.startswith('PBS')]
            if len(files) > 0:
                return True
            else:
                return False
        else:
            return False

    def delete_vnode_defs(self, vdefname=None):
        """
        delete vnode definition(s) on this MoM

        :param vdefname: name of a vnode definition file to delete,
                         if None all vnode definitions are deleted
        :type vdefname: str
        :returns: True if delete succeed otherwise False
        """
        path = self.path_separator.join([self.pbs_conf['PBS_EXEC'], 'sbin', 'pbs_mom'])
        path = '"' + path + '"'
        cmd = [path, '-N', '-s', 'list']
        # removed 'sudo=True' because it was giving sudo -H and it was not working
        ret = self.du.run_cmd(self.hostname, cmd, logerr=False,
                              level=logging.INFOCLI)
        if ret['rc'] != 0:
            return False
        rv = True
        if len(ret['out']) > 0:
            for vnodedef in ret['out']:
                vnodedef = vnodedef.strip()
                if (vnodedef == vdefname) or vdefname is None:
                    if vnodedef.startswith('PBS'):
                        continue
                    path = self.path_separator.join([self.pbs_conf['PBS_EXEC'], 'sbin',
                                        'pbs_mom'])
                    path = '"' + path + '"'
                    cmd = [path, '-N', '-s', 'remove', vnodedef]
                    # removed 'sudo=True' because it was giving sudo -H and it was not working
                    ret = self.du.run_cmd(self.hostname, cmd,
                                          logerr=False, level=logging.INFOCLI)
                    if ret['rc'] != 0:
                        return False
                    else:
                        rv = True
        return rv

    def has_pelog(self, filename=None):
        """
        Check for prologue and epilogue
        """
        _has_pro = False
        _has_epi = False
        phome = self.pbs_conf['PBS_HOME']
        prolog = self.path_separator.join([phome, 'mom_priv', 'prologue'])
        epilog = self.path_separator.join([phome, 'mom_priv', 'epilogue'])
        if self.du.isfile(self.hostname, path=prolog, sudo=True):
            _has_pro = True
        if filename == 'prologue':
            return _has_pro
        if self.du.isfile(self.hostname, path=epilog, sudo=True):
            _has_epi = True
        if filename == 'epilogue':
            return _has_pro
        if _has_epi or _has_pro:
            return True
        return False

    def has_prologue(self):
        """
        Check for prologue
        """
        return self.has_pelog('prolouge')

    def has_epilogue(self):
        """
        Check for epilogue
        """
        return self.has_pelog('epilogue')

    def delete_pelog(self):
        """
        Delete any prologue and epilogue files that may have been
        defined on this MoM
        """
        phome = self.pbs_conf['PBS_HOME']
        prolog = self.path_separator.join([phome, 'mom_priv', 'prologue'])
        epilog = self.path_separator.join([phome, 'mom_priv', 'epilogue'])
        ret = self.delete_file(self.hostname, epilog, force=True, logerr=False)
        if ret:
            ret = self.delete_file(self.hostname, prolog, force=True, logerr=False)
        if not ret:
            self.logger.error('problem deleting prologue/epilogue')
            # we don't bail because the problem may be that files did not
            # exist. Let tester fix the issue
        return ret

    def create_pelog(self, body=None, src=None, filename=None):
        """
        create ``prologue`` and ``epilogue`` files, functionality
        accepts either a body of the script or a source file.

        :returns: True on success and False on error
        """

        if self.has_snap:
            _msg = 'MoM is in loaded from snap so bypassing pelog creation'
            self.logger.info(_msg)
            return False

        if (src is None and body is None) or (filename is None):
            self.logger.error('file and body of script are required')
            return False

        pelog = os.path.join(self.pbs_conf['PBS_HOME'], 'mom_priv', filename)

        self.logger.info(self.logprefix +
                         ' creating ' + filename + ' with body\n' + '---')
        if body is not None:
            self.logger.info(body)
            src = self.du.create_temp_file(prefix='pbs-pelog', body=body)
        elif src is not None:
            with open(src) as _b:
                self.logger.info("\n".join(_b.readlines()))
        self.logger.info('---')

        ret = self.du.run_copy(self.hostname, src=src, dest=pelog,
                               preserve_permission=False, sudo=True)
        if body is not None:
            os.remove(src)
        if ret['rc'] != 0:
            self.logger.error('error creating pelog ')
            return False

        ret = self.du.chown(self.hostname, path=pelog, uid=0, gid=0, sudo=True,
                            logerr=False)
        if not ret:
            self.logger.error('error chowning pelog to root')
            return False
        ret = self.du.chmod(self.hostname, path=pelog, mode=0o755, sudo=True)
        return ret

    def prologue(self, body=None, src=None):
        """
        create prologue
        """
        return self.create_pelog(body, src, 'prologue')

    def epilogue(self, body=None, src=None):
        """
        Create epilogue
        """
        return self.create_pelog(body, src, 'epilogue')

    def action(self, act, script):
        """
        Define action script. Not currently implemented
        """
        pass

    def enable_cgroup_cset(self):
        """
        Not valid for Windows Platform
        """
        return None
        

from ptl.lib.pbs_testlib import PBSInitServices

class PBSInitMoM(PBSInitServices):
    """
    PBS MoM initialization services

    :param hostname: Machine hostname
    :type hostname: str or None
    :param conf: PBS configuaration file
    :type conf: str or None
    """

    def __init__(self, hostname=None, conf=None):
        self.logger = logging.getLogger(__name__)
        self.hostname = hostname
        if self.hostname is None:
            self.hostname = socket.gethostname()
        if conf is None:
            self.dflt_conf_file = "C:\Program Files (x86)\PBS\pbs.conf"
        else:
            self.dflt_conf_file = conf
        self.conf_file = conf
        self.du = DshUtils()
        # self.is_linux = sys.platform.startswith('linux')

    def initd(self, hostname=None, op='start', conf_file=None,
              init_script=None, daemon='all'):
        """
        Run the init script for a given operation

        :param hostname: hostname on which to execute the init script
        :type hostname: str or None
        :param op: one of status, start, stop, restart
        :type op: str
        :param conf_file: optional path to a configuration file
        :type conf_file: str or None
        :param init_script: optional path to a PBS init script
        :type init_script: str or None
        :param daemon: name of daemon to operate on. one of server, mom,
                       sched, comm or all
        :type daemon: str
        """
        if hostname is None:
            hostname = self.hostname
        if conf_file is None:
            conf_file = self.conf_file
        
        init_script = "net"
        init_cmd = [init_script, op, "pbs_mom"]
        msg = 'running init script to ' + op + ' pbs'
        if daemon is not None and daemon != 'all':
            msg += ' ' + daemon
        msg += ' on ' + hostname
        if conf_file is not None:
            msg += ' using ' + conf_file
        msg += ' init_cmd=%s' % (str(init_cmd))
        self.logger.info(msg)
        ret = self.du.run_cmd(hostname, init_cmd, logerr=False)
        if ret['rc'] != 0:
            if ret['rc'] == 2:
                msg = '\n'.join(ret['err'])
                if "The requested service has already been started" in msg:
                    return
            else:
                raise PbsInitServicesError(rc=ret['rc'], rv=False,
                                       msg='\n'.join(ret['err']))
        else:
            return ret

    def restart(self, hostname=None, init_script=None):
        """
        Run the init script for a restart operation

        :param hostname: hostname on which to execute the init script
        :type hostname: str or None
        :param init_script: optional path to a PBS init script
        :type init_script: str or None
        """
        return self.initd(hostname, op='restart', init_script=init_script)

    def restart_mom(self, hostname=None, init_script=None):
        """
        Run the init script for a restart mom

        :param hostname: hostname on which to restart mom
        :type hostname: str or None
        :param init_script: optional path to a PBS init script
        :type init_script: str or None
        """
        return self.initd(hostname, op='restart', init_script=init_script,
                          daemon='mom')

    def start(self, hostname=None, init_script=None):
        """
        Run the init script for a start operation

        :param hostname: hostname on which to execute the init script
        :type hostname: str or None
        :param init_script: optional path to a PBS init script
        :type init_script: str or None
        """
        return self.initd(hostname, op='start', init_script=init_script)

    def start_mom(self, hostname=None, init_script=None):
        """
        Run the init script for a start mom

        :param hostname: hostname on which to start mom
        :type hostname: str or None
        :param init_script: optional path to a PBS init script
        :type init_script: str or None
        """
        return self.initd(hostname, op='start', init_script=init_script,
                          daemon='mom')

    def stop(self, hostname=None, init_script=None):
        """
        Run the init script for a stop operation

        :param hostname: hostname on which to execute the init script
        :type hostname: str or None
        :param init_script: optional path to a PBS init script
        :type init_script: str or None
        """
        return self.initd(hostname, op='stop', init_script=init_script)


    def stop_mom(self, hostname=None, init_script=None):
        """
        Run the init script for a stop mom

        :param hostname: hostname on which to stop mom
        :type hostname: str or None
        :param init_script: optional path to a PBS init script
        :type init_script: str or None
        """
        return self.initd(hostname, op='stop', init_script=init_script,
                          daemon='mom')

    def status(self, hostname=None, init_script=None):
        """
        Run the init script for a status operation

        :param hostname: hostname on which to execute the init script
        :type hostname: str or None
        :param init_script: optional path to a PBS init script
        :type init_script: str or None
        """
        return self.initd(hostname, op='status', init_script=init_script)        


class ProcInfo(object):

    """
    Process information reports ``PID``, ``RSS``, ``VSZ``, Command
    and Time at which process information is collected
    """

    def __init__(self, name=None, pid=None):
        self.name = name
        self.pid = pid
        self.rss = None
        self.vsz = None
        self.pcpu = None
        self.pmem = None
        self.size = None
        self.cputime = None
        self.time = time.time()
        self.command = None

    def __str__(self):
        return "%s pid: %s rss: %s vsz: %s pcpu: %s pmem: %s \
               size: %s cputime: %s command: %s" % \
               (self.name, str(self.pid), str(self.rss), str(self.vsz),
                str(self.pcpu), str(self.pmem), str(self.size),
                str(self.cputime), self.command)
