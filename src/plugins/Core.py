# ******************************************************
# Copyright 2004: Commonwealth of Australia.
#
# Developed by the Computer Network Vulnerability Team,
# Information Security Group.
# Department of Defence.
#
# Michael Cohen <scudette@users.sourceforge.net>
# David Collett <daveco@users.sourceforge.net>
#
# ******************************************************
#  Version: FLAG $Version: 0.87-pre1 Date: Thu Jun 12 00:48:38 EST 2008$
# ******************************************************
#
# * This program is free software; you can redistribute it and/or
# * modify it under the terms of the GNU General Public License
# * as published by the Free Software Foundation; either version 2
# * of the License, or (at your option) any later version.
# *
# * This program is distributed in the hope that it will be useful,
# * but WITHOUT ANY WARRANTY; without even the implied warranty of
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# * GNU General Public License for more details.
# *
# * You should have received a copy of the GNU General Public License
# * along with this program; if not, write to the Free Software
# * Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
# ******************************************************

""" This module contains classes considered to be part of the core functionality of PyFlag.

These are needed by both the DiskForensics and NetworkForensics
"""
import pyflag.FileSystem as FileSystem
import pyflag.IO as IO
import pyflag.Reports as Reports
import pyflag.conf
config=pyflag.conf.ConfObject()
import os.path,os
import pyflag.DB as DB
import pyflag.Farm as Farm
import pyflag.Scanner as Scanner
import pyflag.pyflaglog as pyflaglog
import os, pdb
import pyflag.FlagFramework as FlagFramework
import pyflag.Registry as Registry
from pyflag.ColumnTypes import AFF4URN, StringType, FilenameType, DeletedType, IntegerType, TimestampType, BigIntegerType
import pyflag.Magic as Magic

config.add_option("SCHEMA_VERSION", default=3, absolute=True,
                  help="Current schema version")

class Help(Reports.report):
    """ This facility displays helpful messages """
    hidden = True
    family = "Misc"
    name = "Help"
    parameters = {'topic':'any'}

    def form(self,query,result):
        result.textfield("Topic",'topic')
    
    def display(self,query,result):
        fd=open("%s/%s.html" % (config.DATADIR, os.path.normpath(query['topic'])),'rb')
        result.result+=fd.read()
        result.decoration='naked'

## IO subsystem unit tests:
import unittest
import random,time
from hashlib import md5
import pyflag.tests as tests
from pyflag.FileSystem import DBFS

config.add_option("PERIOD", default=60, type='int',
                  help="Run house keeping every this many seconds")

class Periodic(Farm.Task):
    """ A task to run events periodically.

    Note that periodic events will be fired in a very relaxed manner
    within the timeframe, config.PERIOD to config.PERIOD +
    config.JOB_QUEUE_POLL
    """
    def schedule(self):
        """ We send a request to start the periodic scheduler - there
        can only be one pending request no matter how many workers are
        present.
        """
        dbh=DB.DBO()
        dbh.execute("lock table high_priority_jobs write")
        try:
            dbh.execute("delete from high_priority_jobs where command='Periodic' and state='pending'")
            dbh.insert("high_priority_jobs", _fast=True,
                       command="Periodic", priority=20,
                       _when_valid="from_unixtime(%r)" % (int(time.time()) + config.PERIOD),
                       state = 'pending', cookie=0)
        finally:
            dbh.execute("unlock tables")

    def run(self, *args, **kwargs):
        pyflaglog.log(pyflaglog.VERBOSE_DEBUG, "Running Housekeeping tasks on %s" % time.ctime())
        try:
            FlagFramework.post_event('periodic', None)
        finally:
            self.schedule()

class Exit(Farm.Task):
    """ A task to force the worker to exit """
    def run(self, *args, **kwargs):
        pyflaglog.log(pyflaglog.INFO, "Exiting Worker due to broadcast")
        os._exit(0)
        
class Scan(Farm.Task):
    """ A task to distribute scanning among all workers """
    def run(self,case=None, inode_id=0, scanners=None, cookie=1, *args, **kwargs):
        if scanners:
            Scanner.scan_inode(case, inode_id, scanners, cookie)
            
class DropCase(Farm.Task):
    """ This class is responsible for cleaning up cached data
    structures related to the case
    """
    def run(self, case=None, **kwargs):
        ## Expire any caches we have relating to this case:
        pyflaglog.log(pyflaglog.INFO, "Resetting case %s in worker" % case)
        FlagFramework.post_event('reset', case)

class CaseDBInit(FlagFramework.EventHandler):
    """ A handler for creating common case tables """
    
    ## This should come before any other handlers if possible.
    order = 5
    
    def create(self,case_dbh,case):
        ## Create all CaseTables:
        for t in Registry.CASE_TABLES.classes:
            t().create(case_dbh)

        ## add a (dummy) inode to link orphaned directory entries to
        #case_dbh.execute("insert into inode set inode=0, status='deleted', size=0")
        #case_dbh.execute("insert into file set inode_id=1, inode='00', status='deleted', path='', name=''")

        case_dbh.execute("""Create table if not exists meta(
        `time` timestamp NOT NULL,
        property varchar(50),
        value text,
        KEY property(property),
        KEY joint(property,value(20)))""")

        ## This is a transactional table for managing the cache
        case_dbh.execute("""CREATE TABLE if not exists `sql_cache` (
        `id` INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY ,
        `timestamp` TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL ,
        `query` MEDIUMTEXT NOT NULL,
        `limit` INT default 0,
        `length` INT default 100,
        `status` enum('progress','dirty','cached')
        ) ENGINE=InnoDB""")

        case_dbh.execute("""CREATE TABLE sql_cache_tables (
        `sql_id` INT UNSIGNED,
        `table_name` VARCHAR(250))""")

        case_dbh.check_index("sql_cache_tables","sql_id")
        case_dbh.check_index("sql_cache_tables","table_name")
        
        case_dbh.execute("""CREATE TABLE if not exists `iosources` (
        `id` INT(11) not null auto_increment,
        `name` VARCHAR(250) NOT NULL,
        `type` VARCHAR(250) NOT NULL,
        `timezone` VARCHAR(250) NOT NULL,
        `parameters` TEXT,
        PRIMARY KEY(`id`)
        )""")        

        ## Create a directory inside RESULTDIR for this case to store its temporary files:
        try:
            path = os.path.join(config.RESULTDIR, "case_%s" % case)
            os.mkdir(path)
        except OSError,e:
            print "Error Creating dir %s" % e
            pass

        case_dbh.execute("""CREATE TABLE `GUI_filter_history` (
                            `id` int auto_increment,
                            `filter` VARCHAR(250),
                            `elements` VARCHAR(500),
                            PRIMARY KEY (`id`)) character set latin1""")

        case_dbh.execute("""ALTER TABLE `GUI_filter_history` ADD UNIQUE INDEX stopDupes (filter, elements)""")
    
    def init_default_db(self, dbh, case):
        ## Connect to the mysql database
        tdbh = DB.DBO('mysql')

        ## Make sure we start with a clean slate
        tdbh.execute("drop database if exists %s" % config.FLAGDB)
        tdbh.execute("create database `%s` default character set utf8" % config.FLAGDB)

        ## Source the initial database script. (We no longer use
        ## db.setup to initialise the database - everything is done by
        ## event handlers)

        dbh.execute("""CREATE TABLE meta (
        property varchar(50) default NULL,
        value text default NULL
        ) engine=MyISAM;""")

        ## This is required for the new distributed architecture
        dbh.execute("""create table jobs (
	`id` int unsigned auto_increment, 
	command varchar(250), 
	arg1 text,
	arg2 text,
	arg3 text,
	state enum('broadcast','pending','processing') default 'pending',
        priority int default 10,
        when_valid TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL,
	`cookie` INT(11) not null,
	key `id`(id)
	)""")

        dbh.execute("""CREATE TABLE sql_cache_tables (
        `sql_id` INT UNSIGNED,
        `table_name` VARCHAR(250))""")

        dbh.check_index("sql_cache_tables","sql_id")
        dbh.check_index("sql_cache_tables","table_name")
        
        dbh.execute("""CREATE TABLE `sql_cache` (
        `id` INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY ,
        `timestamp` TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL ,
        `query` MEDIUMTEXT NOT NULL,
        `limit` INT default 0,
        `length` INT default 100,
        `locked` INT default 1
        ) ENGINE=InnoDB""")

        dbh.execute("""CREATE TABLE `logs` (
        `timestamp` TIMESTAMP NOT NULL ,
        `level` TINYINT NOT NULL ,
        `message` VARCHAR( 250 ) NOT NULL
        )""")

        ## Update the schema version.
        dbh.set_meta('schema_version',config.SCHEMA_VERSION)

    def startup(self, dbh, case):
        print "Checking schema for compliance"
        ## Make sure that the schema conforms
        dbh.execute("select value from meta where property='flag_db'")
        DB.check_column_in_table(None, 'sql_cache', 'status',
                                 'enum("progress","dirty","cached")')
        for row in dbh:
            try:
                DB.check_column_in_table(row['value'], 'sql_cache', 'status',
                                         'enum("progress","dirty","cached")')
            except: continue

        ## Check the schema:
        dbh.check_index("jobs", "state")
        DB.check_column_in_table(None, 'jobs', 'priority', 'int default 10')
        DB.check_column_in_table(None, 'jobs', 'pid', 'int default 0')
        DB.check_column_in_table(None, 'jobs', 'when_valid',
                                 'TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL')

        ## Check for the high_priority_jobs table (its basically
        ## another jobs table for high priority jobs - so workers
        ## first check this table before the main jobs table).
        try:
            dbh.execute("select * from high_priority_jobs limit 1")
        except:
            dbh.execute("create table if not exists high_priority_jobs like jobs")
        
        ## Schedule the first periodic task:
        task = Periodic()
        task.run()
        
    def exit(self, dbh, case):
        IO.IO_Cache.flush()
        DB.DBO.DBH.flush()
        DB.DBIndex_Cache.flush()
        Scanner.factories.flush()
        
    def reset(self, dbh, case):
        key_re = "%s.*" % case
        IO.IO_Cache.expire(key_re)
        DB.DBO.DBH.expire(key_re)
        DB.DBIndex_Cache.expire(key_re)
        Scanner.factories.expire(key_re)


class TypeCaseTable(FlagFramework.CaseTable):
    """ Type Table """
    name = 'type'
    columns = [ [ AFF4URN, {}],
                [ StringType, dict(name = 'Mime', column = 'mime')],
                [ StringType, dict(name = 'Type', column = 'type')],
                ]
    index = [ 'type', ]

class CaseConfiguration(Reports.report):
    """
    Case Configuration
    ==================

    This report allows case specific configuations.

    Timezone
    --------
    
    This is the timezone which will be used to view the data.  When
    data is loaded into PyFlag, the iosource itself has a distinct
    timezone associated with it. When users view this data, times are
    automatically presented in the case timezone.

    The case timezone may be specified as SYSTEM which simply
    specifies no special timezone adjustment. If the evidence is
    imported with a SYSTEM timezone, the dates the user views are in
    the same zone that is displayed.

    Note that if you do not see any timezones here, you should run the
    following command to load them into mysql:

    sh$ mysql_tzinfo_to_sql /usr/share/zoneinfo | mysql -u root -p mysql
    
    """
    parameters = { 'TZ': "string"}
    family = "Case Management"
    name = "Configure Case"
    
    def form(self, query, result):
        dbh = DB.DBO(query['case'])
        tz = dbh.get_meta("TZ")
        result.defaults.default("TZ", tz)
        result.heading("Case configuration")
        result.tz_selector("Timezone", "TZ")

    def display(self, query, result):
        dbh = DB.DBO(query['case'])
        result.heading("Setting case parameters")
        for prop in ['TZ',]:
            dbh.execute("update meta set value = %r where property = %r",
                        query[prop],
                        prop)
            result.row(prop, query[prop])

        ## Expire the parameters:
        dbh.DBH.get(query['case']).parameter_flush()

class PyFlagStatistics(Reports.report):
    """ Display statistics on the currently running pyflag
    installation."""
    parameters = {}
    family = "Case Management"
    name = "PyFlag Stats"

    def display(self, query, result):
        result.heading("PyFlag Statistics")
        dbh = DB.DBO()
        dbh.execute("select count(*) as count from jobs where state='pending'")
        row = dbh.fetch()
        result.row("Version", config.VERSION)
        result.row("Outstanding jobs", row['count'])

        cdbh = DB.DBO(query['case'])
        cdbh.execute("select count(*) as count from inode")
        row = cdbh.fetch()
        result.row("Total Inodes in VFS", row['count'])
        result.link("Changelog", url="images/changelog.html")
        result.end_table()

        
        pyflaglog.render_system_messages(result)

        def info_cb(query,result):
            result.heading("PyFlag Plugins")
            result.text(FlagFramework.print_info(), font='typewriter')
            
        result.toolbar(cb=info_cb, icon="question.png")


class ViewFile(Reports.report):
    """ Report to browse the filesystem """
    parameters = {'inode_id':'numeric'}
    family = "Disk Forensics"
    name = "View File Contents"
    description = "Display the contents of a file"
    
    def display(self,query,result):
        new_q = result.make_link(query, '')
        if not query.has_key('limit'): query['limit']= 0
        dbh = self.DBO(query['case'])

        fsfd = FileSystem.DBFS(query["case"])
        ## If this is a directory, only show the stats
        fd = fsfd.open(inode_id=query['inode_id'])
        if not fd: return

        tmp = result.__class__(result)
        tmp.text(fd.urn)
        result.heading(tmp)
        
        try:
            m = Magic.MagicResolver()
            type, mime = m.find_inode_magic(query['case'],
                                            fd.inode_id)
            result.text("Classified as %s by magic" % type)
        except IOError,e:
            result.text("Unable to classify file, no blocks: %s" % e)
        except Exception,e:
            print e
            pass

        names, callbacks = fd.make_tabs()
        
        result.notebook(
            names=names,
            callbacks=callbacks,
            context="mode"
            )
        
        result.toolbar(text="Scan this File",icon="examine.png",
                       link=FlagFramework.query_type(family="Load Data", report="ScanInode",
                                                     inode_id = query['inode_id'],
                                                     case=query['case']
                                                     ),
                       pane = 'pane'
                       )

    def form(self,query,result):
        result.case_selector()
        result.textfield('URN','urn')
        return result

