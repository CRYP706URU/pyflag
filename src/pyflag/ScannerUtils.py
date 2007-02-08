""" Utilities related to scanners """
# Michael Cohen <scudette@users.sourceforge.net>
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
import pyflag.Registry as Registry
import pyflag.pyflaglog as pyflaglog
import pyflag.FlagFramework as FlagFramework

def scan_groups_gen():
    """ A Generator yielding all the scan groups (those scanners with
    a Draw subclass)
    """
    for cls in Registry.SCANNERS.classes:
        try:
            drawer_cls = cls.Drawer
        except AttributeError:
            continue

        yield cls

def fill_in_dependancies(scanners):
    """ Will add scanner names to scanners to satisfy all dependancies """
    while 1:
        modified = False

        for s in scanners:
            cls = Registry.SCANNERS.dispatch(s)
            if type(cls.depends)==type(''):
                d = [cls.depends]
            else:
                d = cls.depends
            for dependancy in d:
                if dependancy not in scanners:
                    pyflaglog.log(pyflaglog.DEBUG,"%s depends on %s, which was not enabled - enabling to satisfy dependancy" % (s,dependancy))
                    scanners.append(dependancy)
                    modified = True
        if not modified: break
