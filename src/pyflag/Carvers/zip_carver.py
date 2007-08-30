#!/usr/bin/python
# ******************************************************
# Michael Cohen <scudette@users.sourceforge.net>
#
# ******************************************************
#  Version: FLAG $Version: 0.84RC4 Date: Wed May 30 20:48:31 EST 2007$
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

"""
Zip File Carving
================

Zip files are described in the application note:
http://www.pkware.com/documents/casestudies/APPNOTE.TXT

Although the application note discusses a Zip64 standard with
different format, it seems to suggest that much of that standard is
covered by patent claims. This means that in practice its uncommon to
see and most zip files use the old structures. We only support the old
structures here. We also do not support multi-disk archives since they
very uncommon these days.

The zip file consists of a sequence of compressed files preceeded by a
file header. These are then followed by a central directory (CD). The
CD is a sequence of CDFileHeader structs each of which describes a
file in the Zip file. This sequence is the followed by an
EndCentralDirectory struct. (For a full description of these structs,
see Zip.py)

In terms of carving, there are a number of good candidates for
identified points:

1) The EndCentralDirectory struct has an offset_of_cd ULONG indicating
the offset of the CD. We can determine if this is correct by using the
CD signature (0x02014b50).

2) The CD is a sequence of CDFileHeader structs, each of which has
relative_offset_local_header ULONG which points to the start of the
FileHeader struct. We also have in the CDFileHeader struct the
filename of the compressed file. Note that the filename also appears
in the FileHeader and depending on the zip program used to generate
the file, one of these locations may be empty. Sometimes, however,
(e.g. the linux zip program), both the locations contain the same
filename. This may be used to assist in confirming the identified
point.

3) FileHeader structs contain the compr_size field. We expect to see
the next FileHeader struct right after the compressed file. This is
not needed usually as the same identified point should be recovered
from the CD (if its a normal - untampered zip file) but if we need to
reconstruct the file without a CD this could be useful.
"""
from format import Buffer
import FileFormats.Zip as Zip
from optparse import OptionParser
import re,sys,binascii
import pickle, zlib
from Carver import Reassembler

SECTOR_SIZE = 512

parser = OptionParser(usage="""%prog """)
parser.add_option('-i', '--index', default=None,
                  help = 'Index file to operate on')

parser.add_option('-c', '--create', default=False, action="store_true",
                  help = 'Create a new index file')

parser.add_option('-m', '--maps', default=False,  action="store_true",
                  help = 'Carve the index file by creating initial map files')

parser.add_option('-p', '--print', default=False, action="store_true",
                  help = 'print the index hits')

parser.add_option('-e', '--extract', default=None,
                  help = 'extract the zip file described in MAP into the file provided')

parser.add_option('-M', '--map', default=None,
                  help = 'map file to read for extraction') 

parser.add_option('-f', '--force', default=False, action="store_true",
                  help = "Force the map file given in --map")

(options, args) = parser.parse_args()

class ZipDiscriminator:
    """ We test the provided carved zip file for errors by reading it
    sequentially
    """
    def __init__(self, reassembler):
        self.r = reassembler

    def decode_file(self, b):
        """ Attempts to decode and verify a ZipFileHeader """
        fh = Zip.ZipFileHeader(b)

        print "Zip File Header @ offset %s (name %s) " % (b.offset, fh['zip_path'])

        ## Deflate:
        if fh['compression_method']==8:
            dc = zlib.decompressobj(-15)
            crc = 0

            compressed_size = fh['compr_size'].get_value()
            print "compressed_size = %s" % compressed_size
            self.offset = b.offset + fh.size()
            self.r.seek(self.offset)

            print "Start of data: %s" % (self.offset)
            total = 0

            while compressed_size>0:
                cdata = self.r.read(min(512,compressed_size))
                compressed_size -= len(cdata)
                data = dc.decompress(cdata)
                total += len(data)
                self.offset += len(cdata)
                crc = binascii.crc32(data, crc)

            ## Finalise the data:
            ex = dc.decompress('Z') + dc.flush()
            total += len(ex)
            crc = binascii.crc32(ex, crc)

            if total != fh['uncompr_size'].get_value():
                print "Total decompressed data: %s (%s)" % (total, fh['uncompr_size'])
                raise IOError("Decompressed file does not have the expected length")

            if crc<0: crc = crc + (1 << 32)
            if crc != fh['crc32'].get_value():
                print "CRC is %d %s" % (crc, fh['crc32'])
                raise IOError("CRC does not match")
            
        else:
            print "Unable to verify compression_method %s - not implemented, skipping file" % fh['compression_method']

        return fh.size() + fh['compr_size'].get_value()

    def decode_cd_file(self, b):
        cd = Zip.CDFileHeader(b)
        print "Found CD Header: %s" % cd['filename']

        return cd.size()

    def decode_ecd_header(self, b):
        ecd = Zip.EndCentralDirectory(b)

        print "Found ECD %s" % ecd
        return ecd.size()
    
    def parse(self, error_count):
        """
        Reads the reassembled zip file from the start and detect errors.

        Returns the offset where the last error occurs
        """
        b = Buffer(fd = self.r)
        self.offset = 0

        ## Try to find the next ZipFileHeader. We allow some padding
        ## between archived files:
        ## Is the structure a ZipFileHeader?
        while 1:
            try:
                length = self.decode_file(b)
                b = b[length:]
            except RuntimeError:
                try:
                    length = self.decode_cd_file(b)
                    b=b[length:]
                except RuntimeError:
                    length = self.decode_ecd_header(b)
                    ## If we found the ecd we can quit:
                    return b.offset+length
                
            except Exception,e:
                print "Error occured after parsing %s bytes" % self.offset
                raise

if options.force:
    if options.map == None:
        print "You must provide a map file to extract"
        sys.exit(1)
        
    c = Reassembler(open(args[0]))
    c.load_map(options.map)
    
    d = ZipDiscriminator(c)
    d.parse(10)

if options.extract:
    if options.map == None:
        print "You must provide a map file to extract"
        sys.exit(1)

    c = Reassembler(open(args[0]))
    c.load_map(options.map)

    outfd = open(options.extract, 'w')

    ## We count on the last identified point to mark the end of the
    ## zip file:
    while 1:
        required_len = min(c.points[-1] - c.readptr, 1024*1024)
        data = c.read(required_len)
        if not data:
            break
        
        outfd.write(data)

    sys.exit(0)
    
if not options.index:
    print "Need an index file to operate on."
    sys.exit(1)

## For now use regex - later convert to pyflag indexs:
regexs = {
    'ZipFileHeader': 'PK\x03\x04',
    'EndCentralDirectory': 'PK\x05\x06',
    'CDFileHeader': 'PK\x01\x02'
    }

cregexs = {}
hits = {}

def build_index():
    ## Compile the res
    for k,v in regexs.items():
        cregexs[k] = re.compile(v)

    BLOCK_SIZE = 4096

    p = pickle.Pickler(open(options.index,'w'))

    offset = 0
    fd = open(args[0],'r')
    while 1:
        data = fd.read(BLOCK_SIZE)
        if len(data)==0: break

        for k,v in cregexs.items():
            for m in v.finditer(data):
                print "Found %s in %s" % (k, offset + m.start())
                try:
                    hits[k].append(offset + m.start())
                except KeyError:
                    hits[k] = [ offset + m.start(), ]

        offset += len(data)

    ## Serialise the hits into a file:
    p.dump(hits)

    print hits

def print_structs():
    p = pickle.Unpickler(open(options.index,'r'))
    hits = p.load()
    
    image_fd = open(args[0],'r')
    zip_files = {}

    for ecd_offset in hits['EndCentralDirectory']:
        ## Each EndCentralDirectory represents a new Zip file
        r = Reassembler(None)
        b = Buffer(image_fd)[ecd_offset:]
        ecd = Zip.EndCentralDirectory(b)
        print "End Central Directory at offset %s:" % (ecd_offset,)

        ## Find the CD:
        offset_of_cd = ecd['offset_of_cd'].get_value()

        ## Check if the cd is where we think it should be:
        possibles = []
        for x in hits['CDFileHeader']:
            if x == ecd_offset - ecd['size_of_cd'].get_value():
                ## No fragmentation in CD:
                print "No fragmentation in Central Directory at offset %s discovered... good!" % x
                possibles = [ x,]
                break
            
            if x % 512 == offset_of_cd % 512:
                print "Possible Central Directory Starts at %s" % x
                possibles.append(x)

        ## FIXME: this needs to be made to estimate the most similar
        ## possibility - we really have very little to go on here -
        ## how can we distinguish between two different CDs that occur
        ## in the same spot? I dont think its very likely in reality
        ## because the CD will be at the end of the zip file which
        ## will be of varying sizes.

        ## For now we go with the first possibility:
        cd_image_offset = possibles[0]

        ## Identify the central directory:
        r.add_point(offset_of_cd, cd_image_offset, "Central_Directory")

        ## We can calculate the offset of ecd here:
        r.add_point(offset_of_cd + ecd['size_of_cd'].get_value(),
                    ecd_offset, "End_Central_Directory")
        
        ## The file end - this is used to stop the carver:
        r.add_point(offset_of_cd + ecd['size_of_cd'].get_value() + ecd.size(),
                                     ecd_offset + ecd.size(), "End")
        
        for i in range(ecd['total_entries_in_cd_on_disk'].get_value()):
            b = Buffer(image_fd)[cd_image_offset:]
            cd = Zip.CDFileHeader(b)

            ## Now try to find the ZipFileHeader for this cd entry:
            fh_offset = cd['relative_offset_local_header'].get_value()

            for fh_image_offset in hits['ZipFileHeader']:
                if fh_image_offset % 512 == fh_offset % 512:
                    print "Possible File header at image offset %s" % fh_image_offset
                    
                    b = Buffer(image_fd)[fh_image_offset:]
                    try:
                        fh = Zip.ZipFileHeader(b)
                    except:
                        print "Oops - no File Header here... continuing"
                        continue

                    ## Is it the file we expect?
                    path = fh['zip_path'].get_value()
                    expected_path = cd['filename'].get_value()

                    ## Check the paths:
                    if path and expected_path and path != expected_path:
                        print "This ZipFileHeader is for %s, while we wanted %s" % (path,expected_path)
                        continue

                    ## Check the expected lengths with the central directory:
                    cd_compr_size = cd['compressed_size'].get_value()
                    cd_uncompr_size = cd['uncompr_size'].get_value()

                    fh_comr_size = fh['compr_size'].get_value()
                    fh_uncomr_size = fh['uncompr_size'].get_value()
                    
                    if cd_compr_size and fh_comr_size and cd_compr_size!=fh_comr_size:
                        print "Compressed size does not match (%s - expected %s)" % (cd_compr_size, fh_comr_size)
                        continue

                    if cd_uncompr_size and fh_uncomr_size and cd_uncompr_size!=fh_uncomr_size:
                        print "Uncompressed size does not match (%s - expected %s)" % (
                            cd_uncompr_size, fh_uncomr_size)
                        continue

                    print "Will use Zip File Header at %s." % (fh_image_offset)
                    
                    ## Identify point:
                    r.add_point(fh_offset, fh_image_offset, "File_%s" % path)
                    
            ## Progress to the next file in the archive:
            cd_image_offset += cd.size()

    r.save_map(open("%s.map" % ecd_offset, 'w'))

if options.create:
    build_index()
    
if getattr(options, "maps"):
    print_structs()
    
else:
    print "nothing to do. Use -h"
