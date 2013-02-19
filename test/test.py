#!/usr/bin/python

import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from lvmraid5 import HardDrive, LvmRaidExec
import pexpect
import unittest

class TestSequenceFunctions(unittest.TestCase):

    def setUp(self):
        self.drive_names = ['/dev/sdb',
                            '/dev/sdc',
                            '/dev/sdd',
                            '/dev/sde']
        
        # Remove any partitions from the existing drives.
        for drive in self.drive_names:
            self.delete_partitions(drive)

    def delete_partitions(self, drive):
        """Delete all partitions on a given drive."""
        # Spawn fdisk.
        fdisk = pexpect.spawn('fdisk {}'.format(drive))
        fdisk.expect(HardDrive.fdisk_main_prompt_re)

        # Delete the first partition (which deletes all child logical
        # partitions too.
        fdisk.sendline('d')
        index = fdisk.expect(['Partition number.*:',
                              'No partition is defined yet!'])
        if index == 0:
            # Delete first partition.
            fdisk.sendline('1')
            fdisk.expect(HardDrive.fdisk_main_prompt_re)
            
            # Write and exit
            fdisk.sendline('w')
        elif index == 1:
            # Nothing to delete.
            fdisk.sendline('q')
            
    def check_lv_exists(self, name):
        """Check that a given LV exists."""
        subprocess.check_output(['lvdisplay',
                                 name])

    def test1(self):
        """Create an array."""
        drives_for_create = self.drive_names[:-1]
        
        # Create an LvmRaidExec instance.
        LvmRaidExec(['create',
                     '--vg_name', '/dev/jjl_vg1',
                     '--lv_name', '/dev/jjl_lv1'] +
                     drives_for_create)
        
        # TODO: do some checking.
        self.check_lv_exists('/dev/jjl_lv1')

if __name__ == '__main__':
    unittest.main()