# ******************************************************
# Copyright 2004: Commonwealth of Australia.
#
# Developed by the Computer Network Vulnerability Team,
# Information Security Group.
# Department of Defence.
#
# David Collett <daveco@users.sourceforge.net>
#
# ******************************************************
#  Version: FLAG $Version: 0.84RC1 Date: Fri Feb  9 08:22:13 EST 2007$
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

""" Module for handling Log Files """
import pyflag.Reports as Reports
import pyflag.FlagFramework as FlagFramework
from pyflag.FlagFramework import query_type
import pyflag.DB as DB
import pyflag.conf
config=pyflag.conf.ConfObject()
import pyflag.pyflaglog as pyflaglog
import pickle,gzip
import plugins.LogAnalysis.Whois as Whois
from pyflag.TableObj import IPType
import re
import pyflag.Registry as Registry

def get_file(query,result):
    result.row("Select a sample log file for the previewer",stretch=False)
    tmp = result.__class__(result)
    tmp.filebox(target='datafile')
    result.row("Enter name of log file:",tmp)
    if query.has_key('datafile'):
        return True
    else:
        result.text("Please input a log file name\n",color='red')
        return False

def save_preset(query,result, log=None):
    result.textfield("name for preset:",'log_preset')
    if query.has_key('log_preset'):
        log.parse(query)
        log.store(query['log_preset'])
        query['finished']='yes'
        return True
    else:
        result.text("Please type a name for the preset.\n",color='red')
        return False

class Log:
    """ This base class abstracts Loading of log files.

    Log files are loaded through the use of log file drivers. These
    drivers extend this class, possibly providing new methods for form
    and field, and potentially even read_record.
    """
    name = "BaseClass"

    def parse(self, query, datafile="datafile"):
        """ Parse all options from query and update ourselves.

        This may be done several times during the life of an
        object. We need to ensure that we completely refresh all data
        which is unique to our instance.
        """
        self.query = query
        
    def __init__(self, case=None):
        self.case = case

    def drop(self, name):
        """ Drops the table named in name.
        
        By default we name the table in the db with name+'_log', but
        that is theoretically transparent to users.
        """
        tablename = name + "_log"
        dbh = DB.DBO(self.case)
        dbh.drop(tablename)
        
    def form(self,query,result):
        """ This method will be called when the user wants to configure a new instance of us. IE a new preset """

    def reset(self, query):
        """ This is called to reset the log tables this log driver has created """

    def display_test_log(self,result):
        # try to load and display as a final test
        dbh = DB.DBO(self.case)
        temp_table = dbh.get_temp()

        ## Temporarily store a preset:
        self.store(temp_table)

        try:
            pyflaglog.log(pyflaglog.VERBOSE_DEBUG, "About to attempt to load three rows into a temp table for the preview")

            ## Since this should be a temporary table, we explicitly tell the load
            ## method to drop it if it exists
            for a in self.load(temp_table,rows= 3):
                pass

            pyflaglog.log(pyflaglog.VERBOSE_DEBUG, "Created a test table containing three rows. About to try and display it...")

            ## Display the new table
            self.display(temp_table, result)
            
        finally:
            ## Drop the temporary preset and the table
            drop_preset(temp_table)
    
    def read_record(self, ignore_comment = True):
        """ Generates records.

        This can handle multiple files as provided in the constructor.
        """
        
        blank = re.compile("^\s*$")

        if not self.datafile:
            raise IOError("Datafile is not set!!!")
        
        for file in self.datafile:
            try:
                ## Allow log files to be compressed.
                fd=gzip.open(file,'r')

                ## gzip doesnt really verify the file until you read something:
                fd.read(10)
                fd.seek(0)
            except:
                fd=open(file,'r')
                
            for line in fd:
                if blank.match(line): continue
                if line.startswith('#') and ignore_comment:
                    continue
                else:
                    yield line

    def get_fields(self):
        """ A generator that returns all the columns in a log file.

        @returns: A generator that generates arrays of cells
        """
        return self.read_record()
    
    def load(self,name, rows = None, deleteExisting=None):
        """ Loads the specified number of rows into the database.

        __NOTE__ We assume this generator will run to
        completion... This is a generator just in order to provide a
        running progress indication - maybe this should change?

        @arg table_name: A table name to use
        @arg rows: number of rows to upload - if None , we upload them all
        @arg deleteExisting: If this is anything but none, tablename will first be dropped
        @return: A generator that represents the current progress indication.
        """
        ## We append _log to tablename to prevent name clashes in the
        ## db:
        tablename = name+"_log"
        
        ## First we create the table. We do this by asking all the
        ## column types for their create clause:
        dbh = DB.DBO(self.case)

        dbh.cursor.ignore_warnings = True
        dbh.mass_insert_start(tablename, _fast=True)
        dbh.invalidate(tablename)

        fields = [ x for x in self.fields if x]
        if len(fields)==0:
            raise RuntimeError("No Columns were selected.")
        
        ## Add our table to the table list. This is done first to trap
        ## attempts to reuse the same table name early. FIXME - create
        ## a combined index on driver + table_name
        dbh.insert("log_tables",
                   preset = self.name,
                   table_name = name)

        ## Create the table:
        dbh.execute("create table if not exists %s (%s)", (
            tablename,
            ',\n'.join([ x.create() for x in fields])
            ))

        ## Now insert into the table:
        count = 0
        for fields in self.get_fields():
            count += 1

            if isinstance(fields, list):
                args = dict()
                ## Iterate on the shortest of fields (The fields array
                ## returned from parsing this line) and self.fields
                ## (The total number of fields we expect)
                for i in range(min(len(self.fields),len(fields))):
                    try:
                        key, value = self.fields[i].insert(fields[i])
                        args[key] = value
                    except (IndexError,AttributeError),e:
                        pyflaglog.log(pyflaglog.WARNING, "Attribute or Index Error when inserting value into field: %r" % e)
            elif isinstance(fields, dict):
                args = fields
                
            if args:
                dbh.mass_insert( **args)
            
            if rows and count > rows:
                break

            if not count % 1000:
                yield "Loaded %s rows" % count

        dbh.mass_insert_commit()
        ## Now create indexes on the required fields
        for i in self.fields:
            try:
                ## Allow the column type to create an index on the
                ## column
                if i.index:
                    i.make_index(dbh, tablename)
            except AttributeError:
                pass

        return

    def restore(self, name):
        """ Restores the table from the log tables (This is the
        opposite of self.store(name))
        """
        dbh = DB.DBO()
        dbh.execute("select * from log_presets where name=%r limit 1" , name)
        row = dbh.fetch()
        self.query = query_type(string=row['query'])
        self.name = name

    def store(self, name):
        """ Stores the configured driver in the db.

        Realistically since drivers can only be configured by the GUI
        the query string that caused them to be configured is the best
        method to reconfigure them in future. This is what is
        implemented here.
        """
        dbh = DB.DBO()
        ## Clean up the query a little:
        self.query.clear('datafile')
        self.query.clear('callback_stored')

        dbh.insert("log_presets",
                   name = name,
                   driver = self.name,
                   query = self.query)

    def display(self,table_name, result):
        """ This method is called to display the contents of the log
        file after it has been loaded
        """
        ## Display the table if possible:
        result.table(
            ## We can calculate the elements directly from our field
            ## list:
            elements = [ f for f in self.fields if f ],
            table = table_name + "_log",
            case = self.case
            )

        return result

## The following methods unify manipulation and access of log presets.
## The presets are stored in FLAGDB.log_presets and the table names
## are stored in casedb.log_tables. The names specified in the
## log_tables table sepecify the naked names of the log tables. By
## convension all log tables need to exist on the disk using naked
## name postfixed by _log.

def load_preset(case, name, datafiles=[]):
    """ Loads the preset named with the given datafiles and return an
    initialised object
    """
    dbh = DB.DBO()
    dbh.execute("select * from log_presets where name=%r limit 1" , name)
    row = dbh.fetch()

    log = Registry.LOG_DRIVERS.dispatch(row['driver'])(case)
    log.restore(name)

    del log.query['datafile']
    
    for f in datafiles:
        log.query['datafile'] = f

    log.parse(log.query)

    return log

def drop_table(case, name):
    """ Drops the log table tablename """
    dbh = DB.DBO(case)
    pyflaglog.log(pyflaglog.DEBUG, "Dropping log table %s in case %s" % (name, case))

    dbh.execute("select * from log_tables where table_name = %r limit 1" , name)
    row = dbh.fetch()

    ## Table not found
    if not row:
        return
    
    preset = row['preset']

    ## Get the driver for this table:
    log = load_preset(case, preset)
    log.drop(name)
    
    ## Ask the driver to remove its table:
    dbh.delete("log_tables",
               where="table_name = %r " % name);

    ## Make sure that the reports get all reset
    FlagFramework.reset_all(family='Load Data', report="Load Preset Log File",
                                       table = name, case=case)

def find_tables(preset):
    """ Yields the tables which were created by a given preset.

    @return: (database,table)
    """
    dbh=DB.DBO()
    
    ## Find all the cases we know about:
    dbh.execute("select value as `case` from meta where property = 'flag_db'")
    for row in dbh:
        case = row['case']
        ## Find all log tables with the current preset
        try:
            dbh2=DB.DBO(case)
            dbh2.execute("select table_name from log_tables where preset=%r", preset)
            for row2 in dbh2:
                yield (case, row2['table_name'])
                
        except DB.DBError,e:
            pass

def drop_preset(preset):
    """ Drops the specified preset name """
    pyflaglog.log(pyflaglog.DEBUG, "Droppping preset %s" % preset)
    for case, table in find_tables(preset):
        drop_table(case, table)

    dbh = DB.DBO()
    dbh.delete("log_presets", where="name = %r" % preset)
    
## Some common callbacks which log drivers might need:
def end(query,result):
    """ This is typically the last wizard callback - we just refresh
    into the load preset log file report"""
    query['log_preset'] = 'test'
    result.refresh(0, query_type(log_preset=query['log_preset'], report="Load Preset Log File", family="Load Data"), pane='parent')

import unittest
import pyflag.pyflagsh as pyflagsh

class LogDriverTester(unittest.TestCase):
    test_case = None
    test_table =None
    log_preset = None
    datafile = None
    
    def test00Cleanup(self):
        """ Remove test log tables """
        ## Create the case if it does not already exist:
        pyflagsh.shell_execv(command = "create_case",
                             argv=[self.test_case])
        
        ## clear any existing presets of the same name:
        drop_preset(self.log_preset)

        ## Clear any existing tables of the same name
        drop_table(self.test_case, self.test_table)

    ## This is disabled so as to leave the test table behind - this is
    ## required for development so we can examine the table
    def XXXtest99Cleanup(self):
        """ Remove test log tables """
        ## clear the preset we created
        drop_preset(self.log_preset)
