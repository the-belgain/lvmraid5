#!/usr/bin/python

import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from lvmraid5 import HardDrive, LvmRaidExec
import pexpect
import subprocess
import unittest

class TestSequenceFunctions(unittest.TestCase):

    def setUp(self):
        self.drive_names = ['/dev/sdb',
                            '/dev/sdc',
                            '/dev/sdd',
                            '/dev/sde']
        self.partitions = ['/dev/sdb5',
                           '/dev/sdc5',
                           '/dev/sdc6',
                           '/dev/sdd5',
                           '/dev/sdd6']
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
            
    def delete_array(self, name):
        try:
            # Delete the PV.
            subprocess.check_output(['pvremove', name])
            
            # Stop the array.
            subprocess.check_output(['mdadm',
                                     '--stop',
                                     name])
            
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

    def test1(self):
        """Create an array."""
        drives_for_create = self.drive_names[:-1]
        
        # Create an LvmRaidExec instance.
        LvmRaidExec(['create',
                     '--vg_name', self.vg_name] +
                     drives_for_create)
        
        # TODO: do some checking.
        self.check_lv_exists(self.lv_name)

if __name__ == '__main__':
    unittest.main()