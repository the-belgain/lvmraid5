#!/usr/bin/python

import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from lvmraid5 import HardDrive, LvmRaidExec, LvmRaidException
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
# The drives must be in increasing size order as follows:
# - 0 == 1 < 2 == 3 < 4 == 5 < 6 == 7 < 8 == 9
drive_names = ['/dev/sdf',
               '/dev/sdg',
               '/dev/sdb',
               '/dev/sdc',
               '/dev/sdd',
               '/dev/sdh',
               '/dev/sdj',
               '/dev/sdk',
               '/dev/sde',
               '/dev/sdi']
num_arrays = 3 # The maximum number of arrays created by any one test.
vg_name = '/dev/jjl_vg1'
lv_name = '/dev/jjl_vg1/lvol0'

class LvmRaid5Test(unittest.TestCase):
    """Parent class containing utility functions."""

    def setUp(self):
        """Called by the unittest framework before every script."""
        self._prepare()

    def tearDown(self):
        """Called by the unittest framework after every script."""
        # self._prepare()

    def wipe_drive(self, drive):
        """Wipe a drive completely."""
        # Wipe the partition superblocks.
        for ii in range(5, 5 + num_arrays):
            self.zero_superblock("%s%d" % (drive, ii))

        # Remove any partitions from the existing drives.
        self.delete_partitions(drive)

    def delete_array(self, name):
        try:
            # Delete the PV.
            subprocess.check_output(['pvremove', '--force', name])
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

    def _prepare(self):
        # Delete LV.
        self.delete_lv(lv_name)
        
        # Delete the VG.
        self.delete_vg(vg_name)
        
        # Delete md arrays and their PVs.
        for ii in range(num_arrays):
            self.delete_array('/dev/md%d' % ii)
            
        # Wipe the drives.
        for drive in drive_names:
            self.wipe_drive(drive)


class LvmRaid5Test1(LvmRaid5Test):

    def create(self):
        """Create an array."""
        # Create an LvmRaidExec instance.
        LvmRaidExec(['create',
                     '--vg_name', vg_name] +
                     [drive_names[0], drive_names[2] + drive_names[4]])
        
        # TODO: do some checking.
        self.check_lv_exists(lv_name)

    def test(self):
        # Run the create test.
        self.create()
        
        # Add 4th largest drive to the array.
        LvmRaidExec(['add',
                     lv_name,
                     drive_names[6]])
        # TODO: check LV size
        
        # Add /dev/sdf to the array.
        LvmRaidExec(['add',
                     lv_name,
                     drive_names[7]])
        # TODO: check LV size


class LvmRaid5Test2(LvmRaid5Test):

    def create(self):
        """Create an array."""
        # Create an LvmRaidExec instance.
        LvmRaidExec(['create',
                     '--vg_name', vg_name] +
                     drive_names[0:1] + drive_names[2:4])
        
        # TODO: do some checking.
        self.check_lv_exists(lv_name)

    def test(self):
        # Create an array with 3 elements.
        self.create()

        # Remove the smallest drive.
        LvmRaidExec(['remove',
                      lv_name,
                      drive_names[0]])

        # Replace the removed drive with a larger one.
        LvmRaidExec(['replace',
                      lv_name,
                      drive_names[4]])

        
class LvmRaid5Test3(LvmRaid5Test):
    """Failure cases for remove/replace.

    - Check that a drive can't be removed when the array is degraded
    - Check that a replacemenet drive can't be added if it doesn't match the
    size of one of the existing drives.
    """

    def test(self):
        # Create an LvmRaidExec instance.
        LvmRaidExec(['create',
                     '--vg_name', vg_name] +
                     [drive_names[0], drive_names[4], drive_names[8]])
        self.check_lv_exists(lv_name)

        # Try to replace a drive in the already-clean array.
        with self.assertRaises(LvmRaidException):
            LvmRaidExec(['replace',
                         lv_name,
                         drive_names[3]])

        # Remove a drive that isn't in the array.
        #with self.assertRaises(LvmRaidException):
        #LvmRaidExec(['remove',
        #             lv_name,
        #             drive_names[2]])

        # Remove the middle-sized drive.
        LvmRaidExec(['remove',
                     lv_name,
                     drive_names[4]])

        # Attempt to remove another drive.
        # This fails because the array is already degraded.
        with self.assertRaises(LvmRaidException):
            LvmRaidExec(['remove',
                         lv_name,
                         drive_names[0]])

        # Try to replace the removed drive with one that is:
        # - larger than the removed drive
        # - smaller than the second-largest drive in the array
        # - not the same size as any of the other arrays.
        # This fails.
        #LvmRaidExec(['replace',
        #             lv_name,
        #             drive_names[4]])

        # Try to replace the removed drive with a smaller one.
        # This fails because it can't make the array clean.
        with self.assertRaises(LvmRaidException):
            LvmRaidExec(['replace',
                         lv_name,
                         drive_names[1]])

        # Try to replace the removed drive with one that is:
        # - larger than the removed drive
        # - smaller than the largest drive in the array
        # - but larger than the second-largest drive in the array
        # - not the same size as any of the other arrays.
        # This succeeds, and grows the array.
        LvmRaidExec(['replace',
                     lv_name,
                     drive_names[6]])


if __name__ == '__main__':
    unittest.main()
