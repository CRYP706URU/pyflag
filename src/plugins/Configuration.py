import pyflag.Reports as Reports
import pyflag.conf
config=pyflag.conf.ConfObject()
import os
import pyflag.DB as DB
import stat
import pyflag.pyflaglog as pyflaglog
import pyflag.FlagFramework as FlagFramework

class Configure(Reports.report):
    """ Configures pyflag.

    This report allows the modification of pyflag's configuration through the GUI.
    """
    name = "Pyflag Configuration"
    family = "Configuration"
    parameters = {}

    def generate_config_file(self, query):
        """ Iterate through the parameters in query and generate a new
        configuraion file
        """
        lines = ["""[DEFAULT]"""]
        for c in config.options:
            c=c.lower()

            ## Read only values cant be changed in the config file
            if config.readonly.has_key(c):
                value = getattr(config,c)
            else:
                value = query.get(c, getattr(config,c))

            lines.append('\n# %s' % config.docstrings[c])

            line = ''
            
            ## If this value was specified on the command line, or is
            ## a default value, we comment the line out - but still
            ## show it:
            if value==str(config.cnf_opts.get(c, '')):
                line = ''
            elif value == str(config.opts.get(c, None)) or value == str(config.default_opts.get(c, None)):
                line = "# "

            ## We write the config option:
            lines.append(line + "%s=%s" % (c, value))

        return "\n".join(lines)
    
    def display(self, query, result):
        result.heading("Update PyFlags configuration")
        highlights = query.getarray('highlight')

        result.start_form(query)

        ## Try to save the configuration file:
        if query.has_key("__submit__"):
            try:
                fd = open(config.filename ,'w')
                fd.write(self.generate_config_file(query))
                fd.close()
            except IOError,e:
                result.heading("Error")
                result.para("An error occured while writing the new configuration file.")
                result.para("The error reported was %s" % e)
                return

            ## Force a re-read of the configuration file:
            print "Forcing reread of %r" % config
            config.add_file(config.filename)

            result.refresh(0, query.__class__())

        for c in config.options:
            if not query.has_key(c):
                result.defaults.set(c, getattr(config,c))
            help = config.docstrings[c]

            if config.readonly.get(c):
                continue

            if c in highlights:
                result.textfield(c, c, tooltip=help, size=40, **{'class': 'highlight'})
            else:
                result.textfield(c, c, tooltip=help, size=40)

        result.end_form()

class HigherVersion(Reports.report):
    """ A Higher version was encountered """
    name = "Higher Version"
    family = 'Configuration'
    hidden = True
    version = 0

    parameters = {}

    def display(self, query,result):
        result.heading("Version error")
        result.para("This is PyFlag version %s, which can only handle schema version %s. However, the default database %s has version %s." % (config.VERSION, config.SCHEMA_VERSION, config.FLAGDB, self.version))
        result.para("You can force me to try and use the more advanced schema by using the --schema_version parameter. But all bets are off in that case...")
        result.para("Alternatively, you can set a new default database name (using --flagdb) and I will create the correct schema version on it")
        result.para("A better solution is to upgrade to the current version of pyflag.")

class InitDB(Reports.report):
    """ Initialises the database """
    name = "Initialise Database"
    family = "Configuration"
    hidden = True
    parameters = {'upgrade':'any'}
    version = 0

    def form(self,query, result):
        try:
            dbh = DB.DBO()
            if not self.version or self.version < config.SCHEMA_VERSION:
                result.para("PyFlag detected that the this installation is using an old database schema version (%s) but the current version is (%s). There are a number of options:" % (self.version,config.SCHEMA_VERSION))
                result.row("1", "Upgrade the schema (This will delete all the currently loaded cases - and the whois and nsrl databases)")
                result.row("2", "Set a different default database name using the command line option --flagdb. This will still allow you to run the old version concurrently")
                result.end_table()
        except DB.DBError,e:
            result.para("PyFlag detected no default database %r. Would you like to create it?" % config.FLAGDB)

        result.checkbox("Upgrade the database?",'upgrade','yes')

    def display(self,query,result):
        ## Try to delete the old cases:
        try:
            dbh = DB.DBO()
            dbh.execute("select * from meta where property='flag_db'")
            for row in dbh:
                pyflaglog.log(pyflaglog.INFO, "Deleting case %s due to an upgrade" % row['value'])
                FlagFramework.delete_case(row['value'])
        except DB.DBError,e:
            pass

        ## Initialise the default database: We post an initialise
        ## event to allow plugins to contribute
        try:
            dbh = DB.DBO()
        except:
            dbh = DB.DBO('mysql')
            dbh.execute("drop database if exists `%s`" % config.FLAGDB)
            dbh.execute("create database `%s`" % config.FLAGDB)
            dbh = DB.DBO()
            
        FlagFramework.post_event('init_default_db', dbh.case)
        try:
            version = dbh.get_meta("schema_version")
            assert(int(version) == config.SCHEMA_VERSION)
        except:
            result.heading("Failed")
            result.para("Unable to create database properly. Try to create it manually from %s/db.setup" % config.DATADIR)
            return

        result.heading("Success")
        result.para("Attempt to create initial database succeeded. Pyflag will start in a few seconds.")
        
        result.refresh(5,query.__class__())
