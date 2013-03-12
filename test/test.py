#!/usr/bin/python

import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from lvmraid5 import HardDrive, LvmRaidExec
import pexpect
import subprocess
import unittest

# Take care, the lvmraid5 tests assume a specific arrangement of hard drives
# (see below), and will erase any data on those drives.  The intention is to 
# run this test within a virtual machine with suitable arranged virtual drives.
#
# The test script must be run with root privileges.  Again, it will erase data
# on all drives it uses.
#
# The drives are arranged as follows:
# - /dev/sdb (size 1)
# - /dev/sdc, /dev/sdd (size 2)

class LvmRaid5Test(unittest.TestCase):
    """Parent class containing utility functions."""

    def delete_array(self, name):
        try:
            # Delete the PV.
            subprocess.check_output(['pvremove', name])
        except subprocess.CalledProcessError:
            pass
        
        try:    
            # Stop the array.
            subprocess.check_output(['mdadm',
                                     '--stop',
                                     name])
        except subprocess.CalledProcessError:
            pass
        
        try:            
            # Now delete it.
            subprocess.check_output(['mdadm',
                                     '--remove',
                                     name])
        except subprocess.CalledProcessError:
            pass
        
    def delete_lv(self, name):
        try:
            subprocess.check_output(['lvremove', '--force', name])
        except subprocess.CalledProcessError:
            pass
                
    def delete_partitions(self, drive):
        """Delete all partitions on a given drive."""
        # Spawn fdisk.
        fdisk = pexpect.spawn('fdisk {}'.format(drive))
        fdisk.expect(HardDrive.fdisk_main_prompt_re)

        # Delete the first partition (which deletes all child logical
        # partitions too.
        fdisk.sendline('d')
        index = fdisk.expect(['Partition number.*\:',
                              'Selected partition 1',
                              'No partition is defined yet!'])
        if index == 0:
            # Delete first partition.
            fdisk.sendline('1')
            fdisk.expect(HardDrive.fdisk_main_prompt_re)
            
            # Write and exit
            fdisk.sendline('w')
        elif index == 1:
            # We have only one partition and it's selected it for us.
            fdisk.sendline('w')
        elif index == 2:
            # Nothing to delete.
            fdisk.sendline('q')
            
        # Wait for exit.
        fdisk.expect(pexpect.EOF)
        
    def delete_vg(self, name):
        try:
            subprocess.check_output(['vgreduce', '--removemissing', name])
            subprocess.check_output(['vgremove', name])
        except subprocess.CalledProcessError:
            pass
        
    def zero_superblock(self, partition):
        try:
            subprocess.check_output(['mdadm',
                                     '--zero-superblock',
                                     partition])
        except subprocess.CalledProcessError:
            pass
            
    def check_lv_exists(self, name):
        """Check that a given LV exists."""
        subprocess.check_output(['lvdisplay',
                                 name])

class LvmRaid5Test1(LvmRaid5Test):

    def prepare(self):
        self.drive_names = ['/dev/sdb',
                            '/dev/sdc',
                            '/dev/sdd',
                            '/dev/sde']
        self.partitions = ['/dev/sdb5',
                           '/dev/sdc5',
                           '/dev/sdc6',
                           '/dev/sdd5',
                           '/dev/sdd6',
                           '/dev/sde5',
                           '/dev/sde6']
        self.array_names = ['/dev/md0',
                            '/dev/md1',
                            '/dev/md126',
                            '/dev/md127']
        self.vg_name = '/dev/jjl_vg1'
        self.lv_name = '/dev/jjl_vg1/lvol0'
       
        # Delete LV.
        self.delete_lv(self.lv_name)
        
        # Delete the VG.
        self.delete_vg(self.vg_name)
        
        # Delete md arrays and their PVs.
        for array in self.array_names:
            self.delete_array(array)
            
        # Wipe the partition superblocks.
        for partition in self.partitions:
            self.zero_superblock(partition)
        
        # Remove any partitions from the existing drives.
        for drive in self.drive_names:
            self.delete_partitions(drive)

    def create(self):
        """Create an array."""        
        # Create an LvmRaidExec instance.
        drives_for_create = self.drive_names[:-1]
        LvmRaidExec(['create',
                     '--vg_name', self.vg_name] +
                     drives_for_create)
        
        # TODO: do some checking.
        self.check_lv_exists(self.lv_name)
        
    def add(self):
        """Add a new drive to the array."""
        
        # Add a drive to the array.  The drive is larger than any existing 
        # members.
        LvmRaidExec(['add',
                     self.lv_name,
                     '/dev/sde'])
        
        # Check all good.
        self.check_lv_exists(self.lv_name)
        
    def test(self):
        # Prepare the test.
        self.prepare()
        
        # Run the create test.
        self.create()
        
        # Run the add test.
        self.add()

if __name__ == '__main__':
    unittest.main()