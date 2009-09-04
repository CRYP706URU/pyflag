""" This module implements AFF4 support into PyFlag.

The AFF4 design is effectively a virtual filesystem (VFS) in itself
since a single AFF4 volume may contain many streams.

When we load an external AFF4 file into PyFlag we replicate all the
stream objects in the volume inside the VFS.

We have an AFF4 VFSFile object which is able to access these files.
"""
import pyflag.pyflaglog as pyflaglog
import pyflag.Farm as Farm

## We just include the pure python implementation of AFF4 in the
## PyFlag source tree.
import pyflag.aff4.aff4 as aff4
from pyflag.aff4.aff4_attributes import *

import pyflag.Reports as Reports
import pyflag.FileSystem as FileSystem
import pyflag.conf as conf
config = conf.ConfObject()
from pyflag.FileSystem import DBFS, File
import pyflag.FlagFramework as FlagFramework
from pyflag.ColumnTypes import StringType
import pyflag.DB as DB
import pdb, os, os.path
import pyflag.CacheManager as CacheManager
import PIL, cStringIO, PIL.ImageFile
import pyflag.Registry as Registry

## Some private AFF4 namespace objects
PYFLAG_NS = "urn:pyflag:"
PYFLAG_CASE = PYFLAG_NS + "case"

## These are the supported streams
SUPPORTED_STREAMS = [AFF4_IMAGE, AFF4_MAP, AFF4_AFF1_STREAM,
                     AFF4_EWF_STREAM, AFF4_RAW_STREAM]

## Move towards using the tdb resolver for AFF4
import pyflag.aff4.tdb_resolver as tdb_resolver
aff4.oracle = tdb_resolver.TDBResolver()

#aff4.oracle.set(aff4.GLOBAL, aff4.CONFIG_VERBOSE, 20)
aff4.oracle.set(aff4.GLOBAL, CONFIG_PROPERTIES_STYLE, 'combined')

class LoadAFF4Volume(Reports.report):
    """
    Load an AFF4 volume
    -------------------
    
    AFF4 is an advanced open format for forensic evidence storage and
    exchange. This report merges the AFF4 volume directly into the
    current VFS.
    """
    parameters = {"filename": "string", 'path': 'string', "__submit__": "any"}
    name = "Load AFF4 Volume"
    family = "Load Data"
    description = "Load an AFF4 Volume"
    
    def form(self, query, result):
        result.fileselector("Select AFF4 volume:", name='filename', vfs=True)
        try:
            if not query.has_key("path"):
                query['path'] = query['filename']
            result.textfield("Mount point", "path")
        except KeyError: pass

    def display(self, query, result):
        filenames = query.getarray('filename')
        print "Openning AFF4 volume %s" % (filenames,)
        result.heading("Loading AFF4 Volumes")

        loaded_volumes = []
        
        for f in filenames:
            ## Filenames are always specified relative to the upload
            ## directory
            filename = "file://%s/%s" % (config.UPLOADDIR, f)
            volumes = aff4.load_volume(filename)
            result.row("%s" % volumes)
            loaded_volumes.extend(volumes)

        fsfd = DBFS(query['case'])
        base_dir = os.path.basename(filenames[0])

        ## FIXME - record the fact that these volumes are loaded
        ## already into this case...

        ## Load all the objects inside the volumes
        for v in loaded_volumes:
            for urn in aff4.oracle.resolve_list(v, AFF4_CONTAINS):
                type = aff4.oracle.resolve(urn, AFF4_TYPE)
                if type in SUPPORTED_STREAMS:
                    if "/" in urn:
                        path = "%s/%s" % (base_dir, urn[urn.index("/"):])
                    else:
                        path = base_dir

                    fsfd.VFSCreate(urn, path, _fast=True,
                                   mode=-1)

class AFF4File(File):
    """ A VFS driver to read streams from AFF4 stream objects """
    specifier = 'u'

    def __init__(self, case, fd, inode):
        self.urn = inode
        fd = aff4.oracle.open(inode, 'r')
        try:
            if not fd: raise IOError("Unable to open %s" % inode)
        finally:
            aff4.oracle.cache_return(fd)
            
        File.__init__(self, case, fd, inode)

    def cache(self):
        pass

    def close(self):
        pass

    def read(self, length=None):
        fd = aff4.oracle.open(self.urn,'r')
        try:
            fd.seek(self.readptr)
            result = fd.read(length)
        finally:
            aff4.oracle.cache_return(fd)
            
        self.readptr+=len(result)
        
        return result

class AFF4ResolverTable(FlagFramework.EventHandler):
    """ Create tables for the AFF4 universal resolver. """
    
    def init_default_db(self, dbh, case):
        ## Denormalise these tables for speed and efficiency
        dbh.execute("""CREATE TABLE if not exists
        AFF4_urn (
        `urn_id` int unsigned not null auto_increment primary key,
        `case` varchar(50) default NULL,
        `urn` varchar(2000) default NULL
        ) engine=MyISAM""")

        dbh.execute("""CREATE TABLE if not exists
        AFF4_attribute (
        `attribute_id` int unsigned not null auto_increment primary key,
        `attribute` varchar(2000) default NULL
        ) engine=MyISAM;""")

        dbh.execute("""CREATE TABLE if not exists
        AFF4 (
        `urn_id` int unsigned not null ,
        `attribute_id` int unsigned not null ,
        `value` varchar(2000) default NULL
        ) engine=MyISAM;""")
        
        dbh.check_index("AFF4_urn", "urn", 100)
        dbh.check_index("AFF4_attribute", "attribute", 100)
        dbh.check_index("AFF4", "urn_id")
        dbh.check_index("AFF4", "attribute_id")

    def create(self, dbh, case):
        """ Create a new case AFF4 Result file """
        volume = aff4.ZipVolume(None, 'w')
        filename = "file://%s/%s.aff4" % (config.RESULTDIR, case)
        aff4.oracle.set(volume.urn, aff4.AFF4_STORED, filename)
        volume.finish()
        aff4.oracle.cache_return(volume)
            
    def startup(self):
        dbh = DB.DBO()
        try:
            dbh.execute("desc AFF4")
        except: self.init_default_db(dbh, None)
        
## FIXME - move to Core.py
from pyflag.ColumnTypes import StringType, TimestampType, AFF4URN, FilenameType, IntegerType, DeletedType, SetType, BigIntegerType, StateType

class ThumbnailType(AFF4URN):
    """ A Column showing thumbnails of inodes """
    def __init__(self, name='Thumbnail', **args ):
        AFF4URN.__init__(self, name, **args)
        self.fsfd = FileSystem.DBFS(self.case)
        self.name = name
        
    def select(self):
        return "%s.inode_id" % self.table

    ## When exporting to html we need to export the thumbnail too:
    def render_html(self, inode_id, table_renderer):
        ct=''
        try:
            fd = self.fsfd.open(inode_id = inode_id)
            image = Graph.Thumbnailer(fd, 200)
            inode_filename, ct, fd = table_renderer.make_archive_filename(inode_id)

            filename, ct, fd = table_renderer.make_archive_filename(inode_id, directory = "thumbnails/")
        
            table_renderer.add_file_from_string(filename,
                                                image.display())
        except IOError,e:
            print e
            return "<a href=%r ><img src='images/broken.png' /></a>" % inode_filename

        AFF4URN.render_html(self, inode_id, table_renderer)
        table_renderer.add_file_to_archive(inode_id)
        return DB.expand("<a href=%r type=%r ><img src=%r /></a>",
                         (inode_filename, ct, filename))

    def render_thumbnail_hook(self, inode_id, row, result):
        try:
            fd = self.fsfd.open(inode_id=inode_id)
            image = PIL.Image.open(fd)
        except IOError,e:
            tmp = result.__class__(result)
            tmp.icon("broken.png")
            return result.row(tmp, colspan=5)

        width, height = image.size

        ## Calculate the new width and height:
        new_width = 200
        new_height = int(float(new_width) / width * height)

        if new_width > width and new_height > height:
            new_height = height
            new_width = width

        def show_image(query, result):
            ## Try to fetch the cached copy:
            filename = "thumb_%s" % inode_id

            try:
                fd = CacheManager.MANAGER.open(self.case, filename)
                thumbnail = fd.read()
            except IOError:
                fd = self.fsfd.open(inode_id=inode_id)
                fd = cStringIO.StringIO(fd.read(2000000) + "\xff\xd9")
                image = PIL.Image.open(fd)
                image = image.convert('RGB')
                thumbnail = cStringIO.StringIO()

                try:
                    image.thumbnail((new_width, new_height), PIL.Image.NEAREST)
                    image.save(thumbnail, 'jpeg')
                    thumbnail = thumbnail.getvalue()
                except IOError,e:
                    print "PIL Error: %s" % e
                    thumbnail = open("%s/no.png" % (config.IMAGEDIR,),'rb').read()

                CacheManager.MANAGER.create_cache_from_data(self.case, filename, thumbnail)
                fd = CacheManager.MANAGER.open(self.case, filename)
                
            result.result = thumbnail
            result.content_type = 'image/jpeg'
            result.decoration = 'raw'

        
        result.result += "<img width=%s height=%s src='f?callback_stored=%s' />" % (new_width, new_height,
                                                                result.store_callback(show_image))

    display_hooks = AFF4URN.display_hooks[:] + [render_thumbnail_hook,]

class AFF4VFS(FlagFramework.CaseTable):
    """ A VFS implementation using AFF4 volumes """
    name = 'vfs'
    indexes = ['urn_id']
    columns = [ [ AFF4URN, {} ],
                [ DeletedType, {} ],
                [ IntegerType, dict(name = 'UID', column = 'uid')],
                [ IntegerType, dict(name = 'GID', column = 'gid')],
                [ TimestampType, dict(name = 'Modified', column='mtime')],
                [ TimestampType, dict(name = 'Accessed', column='atime')],
                [ TimestampType, dict(name = 'Changed', column='ctime')],
                [ TimestampType, dict(name = 'Deleted', column='dtime')],
                [ IntegerType, dict(name = 'Mode', column='mode')],
                [ BigIntegerType, dict(name = 'Size', column='size')],
                ## The type for this object
                [ StateType, dict(name='Type', column='type',
                                  states = dict(directory='directory',
                                                file = 'file'))],
                
                ## The dictionary version used on this inode:
                [ IntegerType, dict(name = "Index Version", column='version', default=0)],
                [ IntegerType, dict(name = 'Desired Version', column='desired_version')],
                ## The filename in the VFS where this object goes
                [ FilenameType, dict(table='vfs')],
                ]

    extras = [ [FilenameType, dict(table='vfs', name='Name', basename=True)],
               [ThumbnailType, dict(table='vfs', name='Thumb')],
               ]

    def __init__(self):
        scanners = set([ "%s" % s.__name__ for s in Registry.SCANNERS.classes ])
        self.columns = self.columns + [ [ SetType,
                                          dict(name='Scanner Cache', column='scanner_cache',
                                               states = scanners)
                                          ],
                                        ]

    
import unittest
import pyflag.pyflagsh as pyflagsh

class AFF4LoaderTest(unittest.TestCase):
    """ Load handling of AFF4 volumes """
    test_case = "PyFlagTestCase"
    test_file = 'http.pcap'
#    test_file = '/testimages/pyflag_stdimage_0.5.e01'
#    test_file = 'stdcapture_0.4.pcap.e01'

    def test01CaseCreation(self):
        env = pyflagsh.environment(case=self.test_case)
        pyflagsh.shell_execv(command="delete_case", env=env,
                             argv=[self.test_case])
        pyflagsh.shell_execv(command="create_case", env=env,
                             argv=[self.test_case])
        if 1:
            pyflagsh.shell_execv(command='execute', env=env,
                                 argv=['Load Data.Load AFF4 Volume',
                                       'case=%s' % self.test_case, 
                                       'filename=%s' % self.test_file])

            pyflagsh.shell_execv(command='scan', env=env,
                                 argv=['*', 'PartitionScanner',
                                       'FilesystemLoader', 'PCAPScanner',
                                       'HTTPScanner', 'GZScan'])
            
        #fd = CacheManager.AFF4_MANAGER.create_cache_fd(self.test_case, "/foo/bar/test.txt")
        #fd.write("hello world")
        #fd.close()


import atexit

def close_off_volume():
    """ Check for dirty volumes are closes them """
    dbh = DB.DBO()
    dbh.execute("select value from meta where property='flag_db'")
    for row in dbh:
        volume_urn = CacheManager.AFF4_MANAGER.make_volume_urn(row['value'])
        if volume_urn and aff4.oracle.resolve(volume_urn, AFF4_VOLATILE_DIRTY):
            fd = aff4.oracle.open(volume_urn, 'w')
            print "Closing volume %s" % volume_urn
            if fd:
                fd.close()

atexit.register(close_off_volume)