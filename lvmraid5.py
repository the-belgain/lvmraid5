#!/usr/bin/python

# Note this has the following dependencies:
# - fdisk
# - mdadm
# - lvm
# - The python imports below (in particular pexpect)

# TODO: eventually, move to using the python-lvm bindings.  They don't seem
# stable enough yet, or packaged in standard distro's.
# TODO: make logging sane.

import argparse
import logging
import math
import os
import re
import subprocess
import sys
import time

# TODO: move the non-standard imports to check_dependencies and give more
# helpful errors.
import pexpect

def check_critical(condition, msg):
    """Check that a condition is true, exiting with error message if not."""
    if not condition:
        print(msg)
        exit(1)

def round_sigfigs(num, sig_figs, round_down=True):
    """Round to specified number of sigfigs."""
    assert(sig_figs != 0)
    assert(num >= 0)
    unit_size = math.pow(10, math.floor(math.log10(num)) - (sig_figs - 1))
    ret_val = long(round(num, -int(math.floor(math.log10(num)) - (sig_figs - 1))))
    if round_down and ret_val > num:
        ret_val -= unit_size
        assert(ret_val <= num)
    return long(ret_val)

class LvmRaidBaseClass(object):
    """Base class which all other classes inherit from.
    
    Used to provide logging function.
    
    """
    instances = {} # Keys are subclasses, values are a dictionary indexed on name.
    
    @classmethod
    def find_or_create(cls, element_name, child_class):
        if not cls.instances.has_key(child_class):
            cls.instances[child_class] = {}
        if not cls.instances[child_class].has_key(element_name):
            cls.instances[child_class][element_name] = child_class(element_name)
            cls.instances[child_class][element_name].get_info()
        return cls.instances[child_class][element_name]
    
    def __init__(self, name):
        """Provides common initialization function.
        
        Called by the subclass before doing their own initialization."""
        # Assert that we didn't accidentally create the object directly rather
        # than through find_or_create().
        assert(not LvmRaidBaseClass.instances[self.__class__].has_key(name))
        
        # Store the name (all subclasses must have this property).
        self.name = name
        
        # Configure a logger.
        self.logger_adapter = logging.LoggerAdapter(
            logging.getLogger(''),
            {'class_name' : self.__class__.__name__,
             'instance_name' : self.name})
        
    def __str__(self):
        """The default string method is to just return the name."""
        return self.name
    
    def get_info(self):
        """Children generally override this method."""
        pass

    def log(self, msg, level=logging.DEBUG):
        self.logger_adapter.log(level, msg)
        
    def run_cmd(self, cmd):
        # Run the command.  TODO: capture output on failures.
        output = ""
        try:
            output = subprocess.check_output(cmd,
                                             stderr=subprocess.STDOUT)
            self.log("""Running command "{}":\n{}""".format(cmd, output))
        except subprocess.CalledProcessError:
            self.log("""Command failed "{}":\n{}""".format(cmd, output))
            raise
        return output
    
    def spawn_pexpect(self, cmd):
        return pexpect.spawn(cmd,
                             timeout=5,
                             logfile=file('/tmp/lvmraid5_pexpect.log', 'a'))

class HardDrive(LvmRaidBaseClass):
    """Represents a physical hard drive."""
    fdisk_main_prompt_re = re.compile('Command.*\:')
    fdisk_size_re = re.compile('Disk.*\,\s(?P<size>[0-9]+)\sbytes')
    fdisk_partition_list_re = re.compile(
        '(?P<name>\S*(?P<num>[0-9]+))\s+(?P<start>[0-9]+)\s+(?P<end>[0-9]+)\s+(?P<blocks>[0-9]+)\s+(?P<id>\S+).*')
 
    @classmethod
    def find_or_create(cls, name, child_class=None):
        return super(HardDrive, cls).find_or_create(name, cls)
 
    def __init__(self, name):
        super(HardDrive, self).__init__(name)
        self.empty = False
        self.partitions = {} # Keys are the partition number.
        
    def init_partitions(self):
        """Initializes the partition table on a new hard drive.
        
        Expects the drive to have no existing partitions.
        
        """
        self.log('Initialising partition table')
        fdisk = self.spawn_fdisk()
        fdisk.expect(HardDrive.fdisk_main_prompt_re)
        fdisk.sendline('p') # Print the partition table.
        # TODO: Check there were no partitions
        fdisk.expect(HardDrive.fdisk_main_prompt_re)
        
        # Create a new extended partition to hold all future partitions.
        fdisk.sendline('n') # New partition
        fdisk.expect('Select.*\:')
        fdisk.sendline('e') # Partition type extended
        fdisk.expect('Partition number.*\:')
        fdisk.sendline('1') # Partition number 1
        fdisk.expect('First sector.*\:')
        fdisk.sendline('')  # Default first sector (start of disk).
        fdisk.expect('Last sector.*\:')
        fdisk.sendline('')  # Default last sector (end of disk).
        fdisk.expect(HardDrive.fdisk_main_prompt_re)
        
        # TODO: Print the partition and check it's as expected.
        
        # And write the partition to disk.
        fdisk.sendline('w')
        
        # Wait for exit.
        fdisk.expect(pexpect.EOF)
        
    def create_partition(self, size):
        """Create a partition.
        
        Expects the drive to have already been initialized with an extended
        partition.
        
        Returns the new Partition object.
        
        """
        self.log('Creating partition of size {}'.format(size))
        # Refresh the hard drive info.  This checks the partition table is
        # in the expected state.
        self.get_info()
        
        # Spawn fdisk.
        fdisk = self.spawn_fdisk()
        fdisk.expect(HardDrive.fdisk_main_prompt_re)
        
        # Create a new logical partition.
        fdisk.sendline('n') # New partition
        fdisk.expect('Select.*\:')
        fdisk.sendline('l') # Partition type logical
        fdisk.expect('Adding logical partition (?P<num>[0-9]+)')
        partition_num = fdisk.match.group('num')
        fdisk.expect('First sector.*\:')
        fdisk.sendline('')  # Default first sector (first available)
        fdisk.expect('Last sector.*\:')
        fdisk.sendline('+{}K'.format(size / 1024))  # <size> gigabytes after first
        index = fdisk.expect([HardDrive.fdisk_main_prompt_re,
                              'Value out of range'])
        if index == 0:
            # Successfully created the partition.  Set it's type to be
            # raid-autodetect.
            fdisk.sendline('t') # Change partition type
            fdisk.expect('Partition number.*\:')
            fdisk.sendline(partition_num)
            fdisk.expect('Hex code.*\:')
            fdisk.sendline('fd') # Hex code for Linux raid auto
            fdisk.expect(HardDrive.fdisk_main_prompt_re)
            fdisk.sendline('w') # Write the partition table to disk.

        elif index == 1:
            # Not enough space to create the partition - send enter to create
            # one with the remaining space, then q to quit without writing the
            # partition table.
            fdisk.sendline('')
            fdisk.expect(HardDrive.fdisk_main_prompt_re)
            fdisk.sendline('q')
            
        # Wait for exit.
        fdisk.expect(pexpect.EOF)

        # Refresh the drive info.
        self.get_info()
        
        return self.partitions.get(int(partition_num))
    
    def unallocated_size(self):
        """Returns the amount of unallocated space on the drive."""
        # Calculate the amount of space used.
        used_size = 0
        for part in self.partitions.values():
            used_size += part.size()
        assert(used_size <= self.size())
        return (self.size() - used_size)
    
    def get_info(self):
        """Extracts info for the hard drive."""
        self.log('Refreshing info')
        # Spawn fdisk.  If this fails, that likely indicates the drive isn't
        # present.
        fdisk = self.spawn_fdisk()
        index = fdisk.expect([HardDrive.fdisk_main_prompt_re,
                           'fdisk: unable to open {}'.format(self.name)])
        check_critical(index == 0,
                       'Could not find hard drive {}'.format(self.name))
        
        # Print the partition table.
        fdisk.sendline('p')
        fdisk.expect(HardDrive.fdisk_main_prompt_re)
        output = fdisk.before
        
        # Get the hard drive size.
        self.size_in_bytes = long(
            HardDrive.fdisk_size_re.search(output).group('size'))
        
        # Spin through the returned partitions, creating objects for them.
        self.empty = True
        self.partitions = {}
        for groups in HardDrive.fdisk_partition_list_re.findall(output):
            self.empty = False
            if groups[5] == "5": # Extended.
                self.partitions_initialized = True
            elif groups[5] == "fd":
                self.log('Found partition match {}'.format(groups))
                part = Partition.find_or_create(groups[0])
                part.num_blocks = long(groups[4])
                self.partitions[int(groups[1])] = part
                
        # Quit
        fdisk.sendline('q')

        # Wait for exit.
        fdisk.expect(pexpect.EOF)
        
    def size(self):
        """Returns the rounded size of the drive (in bytes).
        
        Round down two significant figures.
        
        """
        return round_sigfigs(self.size_in_bytes, 2)
    
    def spawn_fdisk(self):
        return self.spawn_pexpect('fdisk {}'.format(self.name))
        
class Partition(LvmRaidBaseClass):
    """Represents a partition.
    
    This is hung off both the RaidArray class, and the HardDrive class.
    
    """
    drive_name_re = re.compile('^(?P<name>[^0-9]+)[0-9]+')
    raid_array_name_re = re.compile('')
    
    @classmethod
    def find_or_create(cls, name):
        return super(Partition, cls).find_or_create(name, cls)
    
    def __init__(self, name):
        super(Partition, self).__init__(name)
        self.path = name
        self.array = None # The RaidArray this member is part of.
        self.drive = None # The HardDrive this member is part of.
        self.num_blocks = 0
        
    def get_info(self):
        """Check whether the partition is part of a raid array.
        
        It's perfectly valid for it not to be, so try-except.
        
        """
        # Get the drive name from the partition name.
        drive_name = Partition.drive_name_re.search(self.name).group('name')
        self.drive = HardDrive.find_or_create(drive_name)
        
        # Run suitable mdadm command.
        try:
            output = self.run_cmd(['mdadm',
                                   '--examine',
                                   self.name])
            # array_name = Partition.raid_array_name_re.search(output).group[0]
            # self.array = RaidArray.find_or_create(array_name)
        except subprocess.CalledProcessError:
            pass
        
    def size(self):
        return round_sigfigs(self.num_blocks * 1024, 2, round_down=False)
        
class LogicalVolume(LvmRaidBaseClass):
    vg_name_re = re.compile('^\s*VG\sName\s+(?P<name>[^\s]+)', re.MULTILINE)
    lv_size_re = re.compile('^\s*LV\sSize\s+(?P<size>[^\s]+)\sGB', re.MULTILINE)

    @classmethod
    def find_or_create(cls, name):
        return super(LogicalVolume, cls).find_or_create(name, cls)
    
    def __init__(self, name):
        super(LogicalVolume, self).__init__(name)
        self.name = name
        self.vg = None
        self.size = None
        
    def print_details(self):
        return "Logical Volume {} ({} GB)\n{}".format(self.name,
                                                      self.size,
                                                      self.vg)
        
    def create(self, vg):
        """Create a logical volume, consuming the entire given VG."""
        # Create the LV.
        self.run_cmd(['lvcreate',
                      '--name',
                      self.name,
                      '--extents',
                      '100%FREE',
                      vg.name])
        
        # Get info.
        self.get_info()
        
    def extend(self):
        """Extend the LV, filling all available space on its VG."""
        # Extend the LV.
        self.run_cmd(['lvextend',
                      '-l+100%FREE',
                      self.name])
        
        # Get LV info.
        self.get_info()
        
    def get_info(self):
        """Refresh the info for the LV.
        
        The LV may not yet exists, so cope with lvdisplay returning
        unsuccessfully.
        
        """
        try:
            output = self.run_cmd(["lvdisplay", self.name, "--units", "G"])
            self.size = LogicalVolume.lv_size_re.search(output).group('size')
            m = LogicalVolume.vg_name_re.search(output)
            self.vg = VolumeGroup.find_or_create(m.group('name'))
        except subprocess.CalledProcessError:
            pass
        
    def wait_for_resync_complete(self):
        self.vg.wait_for_resync_complete()
    
class VolumeGroup(LvmRaidBaseClass):
    pv_name_re = re.compile('^\s*PV\sName\s+(?P<name>[^\s]+)', re.MULTILINE)
    
    @classmethod
    def find_or_create(cls, name):
        return super(VolumeGroup, cls).find_or_create(name, cls)
    
    def __init__(self, name):
        super(VolumeGroup, self).__init__(name)
        self.name = name
        self.pvs = {} # Physical volumes, keyed on name.
        
    def print_details(self):
        ret_str = "\--Volume Group {}".format(self.name)
        for pv in self.pvs.keys():
            ret_str += "\n{}".format(pv)
        return ret_str
    
    def create(self, pvs):
        """Creates a VG from a list of PVs."""
        self.run_cmd(['vgcreate', self.name] + [pv.name for pv in pvs])
        
        # Now populate internal fields.
        self.get_info()
    
    def drives(self):
        """Returns a dictionary of all hard drives in the VG."""
        drives = {}
        for pv in self.pvs.values():
            for member in pv.raid_array.members.values():
                if not drives.has_key(member.drive.name):
                    drives[member.drive.name] = member.drive
        return drives
    
    def extend(self, pv):
        """Extend the volume group by adding a new PV to it."""
        # Extend the VG.
        self.run_cmd(['vgextend',
                      self.name,
                      pv.name])
        
        # Refresh VG info.
        self.get_info()
        
    def get_info(self):
        """Get the info for this VG.
        
        The VG may not yet exist, so it's perfectly valid for vgdisplay to
        return unsuccessful.
        
        """
        try:
            output = self.run_cmd(["vgdisplay", self.name, "--verbose"])
            m = VolumeGroup.pv_name_re.findall(output)
            for name in m:
                self.pvs[name] = PhysicalVolume.find_or_create(name)
        except subprocess.CalledProcessError:
            pass
        
    def wait_for_resync_complete(self):
        for pv in self.pvs.values():
            pv.wait_for_resync_complete()

class PhysicalVolume(LvmRaidBaseClass):
    
    @classmethod
    def find_or_create(cls, name):
        return super(PhysicalVolume, cls).find_or_create(name, cls)
    
    def __init__(self, name):
        super(PhysicalVolume, self).__init__(name)
        self.name = name
        self.raid_array = None
        
    def print_details(self):
        return "   \--Physical Volume {}".format(self.name)
        
    def create(self):
        """Create a PV on the device with the PV's name."""
        # Nice and easy, just call pvcreate.
        self.run_cmd(['pvcreate', self.name])
        
    def get_info(self):
        if self.raid_array is None:
            self.raid_array = "Creating"
            self.raid_array = RaidArray.find_or_create(self.name)

    def grow(self):
        # Grow the PV.
        self.run_cmd(['pvresize', self.name])
        
        # And refresh the PV information
        self.get_info()
        
    def wait_for_resync_complete(self):
        self.raid_array.wait_for_resync_complete()

class RaidArray(LvmRaidBaseClass):
    members_re = re.compile(
        '^(\s*[0-9]+){4}[^/]*(?P<name>\S*)$', re.MULTILINE)
    state_re = re.compile('State\s*\:\s*(?P<state>.*)$', re.MULTILINE)
    rebuild_percentage_re = re.compile(
        'Rebuild\sStatus[^0-9]*(?P<percentage>[0-9]+)', re.MULTILINE)
    ARRAY_STATE_CLEAN='clean'
    ARRAY_STATE_RECOVERING='clean, degraded, recovering'
    ARRAY_STATE_RESHAPING='clean, reshaping'
    
    @classmethod
    def find_or_create(cls, name=None):
        if name is None:
            name = cls.next_free_name()
        return super(RaidArray, cls).find_or_create(name, cls)
    
    @classmethod
    def next_free_name(cls):
        """Return the next available name for a raid array."""
        ii = 0
        while True:
            name = '/dev/md{}'.format(ii)
            if not os.path.exists(name):
                return name
            ii += 1
    
    def __init__(self, name):
        """Create a new array object.
        
        The name parameter is optional; if it is not specified the next unused
        name in the /dev/mdX sceme is used.
        
        """
        super(RaidArray, self).__init__(name)
        self.name = name
        self.pv = None
        self.members = {} # Partitions, keyed on partition name.
        
    def print_details(self):
        ret_str = 'Raid 5 array {}:\n'.format(self.name)
        for device in self.devices.values():
            ret_str += '{}\n'.format(device)
        
    def create(self, members):
        """Create a new RAID5 array."""
        self.log('Creating RAID5 array with members {}'.format(members))
        
        # TODO: check the array doesn't exist.
        
        # Create the array.  This will resync in the background.
        self.run_cmd(['mdadm',
                      '--create',
                      self.name,
                      '--level=5',
                      '--raid-devices={}'.format(len(members))] +
                      [part.name for part in members])
        
        # Refresh array info.
        self.get_info()
        
    def get_info(self):
        # Initialize fields.
        if self.pv is None:
            self.pv = "Creating"
            self.pv = PhysicalVolume.find_or_create(self.name)
        self.devices = {}
        self.state = None
        self.op_percentage_completion = None

        # Get the info.
        try:
            output = self.run_cmd(["mdadm", "--detail", self.name])
            for groups in RaidArray.members_re.findall(output):
                self.members[groups[1]] = Partition.find_or_create(groups[1])
            # self.device_size = RaidArray.device_size_re.search(output).group('size')
            self.state = RaidArray.state_re.search(output).group('state').strip()
            self.log("Array state {}".format(self.state))
            if self.state == RaidArray.ARRAY_STATE_RECOVERING:
                self.op_percentage_completion = RaidArray.rebuild_percentage_re.search(output).group('percentage').strip()
                self.log("Rebuild percentage {}".format(self.op_percentage_completion))
            else:
                self.op_percentage_completion = "0"
        except subprocess.CalledProcessError:
            # This indicates the array hasn't yet been created, which is
            # perfectly valid.
            pass

    def grow(self, new_partition, backup_file):
        """Grow the array by adding a new partition to it."""
        assert(new_partition.array is None)
        
        # Grow the array.
        self.run_cmd(['mdadm',
                      self.name,
                      '--add',
                      new_partition.name])
        self.run_cmd(['mdadm',
                      self.name,
                      '--grow',
                      '--raid-devices={}'.format(len(self.members) + 1),
                      '--backup-file={}'.format(backup_file)])
        
        # Wait for async completion.
        self.wait_for_resync_complete()
        
    def is_clean(self):
        return (self.state == RaidArray.ARRAY_STATE_CLEAN)
    
    def members_size(self):
        size = None
        for part in self.members.values():
            assert((size is None) or (size == part.size()))
            size = part.size()
        return size

    def remove_member(self, member):
        """"Remove a given member from an array.
        
        This doesn't reshape the array, it just leaves it degraded.
        
        """
        # TODO: check the array is currently clean.
        
        # Remove the drive.
        self.run_cmd(['mdadm',
                      self.name,
                      '--fail',
                      member.name])
        self.run_cmd(['mdadm',
                      self.name,
                      '--remove',
                      member.name])
        
        # Refresh info
        self.get_info()
        
    def wait_for_resync_complete(self):
        """Wait for this array to complete resynchronisation."""
        self.get_info()
        completion_text = None
        while self.state != RaidArray.ARRAY_STATE_CLEAN:
            if self.state == RaidArray.ARRAY_STATE_RECOVERING:
                completion_text = "Resync"
                print("Waiting for {} to finish resync ({}% complete)...\r"
                      .format(self, self.op_percentage_completion))
                time.sleep(15)
                self.get_info()
            elif self.state == RaidArray.ARRAY_STATE_RESHAPING:
                completion_text = "Reshape"
                print("Waiting for {} to finish reshape ({}% complete)...\r"
                      .format(self, self.op_percentage_completion))
                time.sleep(15)
                self.get_info()
            else:
                check_critical(False,
                    "Unexpected RAID array state: {}".format(self.state))
                
        if completion_text is not None:
            self.log("{} complete for {}".format(completion_text, self),
                     logging.INFO)
        else:
            self.log("Array already clean")

class LvmRaidExec:
    """Represents a single invocation of the lvmraid script."""
    def __init__(self, args):
        # Configure logging.
        self.setup_logging()
        
        # Check all our dependencies are met, before we bother trying anything
        # more complex.
        self.check_dependencies()
        
        parser = argparse.ArgumentParser(
            description='Helper utility for lvm and mdadm.')
        parser.add_argument('-d', '--dry-run',
                            action='store_true',
                            help="Read-only mode; only display information.")
        subparsers = parser.add_subparsers()
        
        # Add parse for the add command.
        add_parser = subparsers.add_parser(
            'add',
            help="""Add a new drive to an existing array, increasing the array's
            number of drives by one.  To add a new drive to the array without 
            changing the total number of drives, see the 'replace' command.
            If the new drive is large enough to increase the array size, it
            will do so.""")
        add_parser.add_argument(
            '--mdadm-backup-file',
            default='/tmp/lvmraid5_mdadm_backup_file.txt',
            help="""Backup file for mdadm to use.  This should be on a physical
            drive other than the array.""")
        add_parser.add_argument(
            'lv', help='The LVM Logical Volume to add the drive to')
        add_parser.add_argument('drive_to_add',
                                help='The drive to add (eg. /dev/sda)')
        add_parser.set_defaults(func=self.add)
        
        # Parser for the create command.
        create_parser = subparsers.add_parser(
            'create',
            help="""Create a new array from a set of drives.""")
        create_parser.add_argument(
            '--vg_name',
            help="""The name of the LVM Volume Group to create (default: 
            /dev/lvmraid_vg<N>""")
        create_parser.add_argument(
            'drives_for_create',
            nargs='*',
            help='List of 3 or more drives from which to create the array.')
        create_parser.set_defaults(func=self.create)

        # Handle the examine command.
        examine_parser = subparsers.add_parser(
            'examine',
            help='Examine a given partition.')
        examine_parser.add_argument(
            'filesystem', help='The filesystem to examine.')
        examine_parser.set_defaults(func=self.examine)

        # Handle the remove command.
        remove_parser = subparsers.add_parser(
            'remove',
            help="""Remove a given drive from an existing array.
            This command checks that no data loss will occur as a result of the
            operation, and disallows the operation otherwise.""")
        remove_parser.add_argument('drive_to_remove',
                                   help='The drive to remove (eg. /dev/sda)')
        remove_parser.set_defaults(func=self.remove)
                
        # Parser for the replace command.
        replace_parser = subparsers.add_parser(
            'replace',
            help="""Replace an existing (possibly faulty) drive in an array 
            with a new one.  This is equivalent to 'remove' followed by 'add'.
            """)
        replace_parser.add_argument(
            'lv',
            help='The Logical Volume to add the drive to')
        replace_parser.add_argument('drive_to_add',
                                    help='The drive to add.')
        replace_parser.set_defaults(func=self.replace)
        
        # Now parse the arguments, and call through to the appropriate function.
        self.args = parser.parse_args(args)
        
        # Call the relevant function.
        self.args.func()
    
    def create(self):
        """Create a new array from a set of drives"""
        self.log("Creating new array...", logging.INFO)
        drives = {}
        drive_sizes = set()
        array_sizes = []
        pvs = {}
        
        # Check that we've been passed at least 2 drives.  We don't currently
        # support creating degraded arrays.
        check_critical(len(self.args.drives_for_create) >= 2,
                       "Must have at least 2 drives for array creation")
        
        for drive_name in self.args.drives_for_create:
            drives[drive_name] = HardDrive.find_or_create(drive_name)
            check_critical(
                drives[drive_name].empty,
                """Drive {} is not empty - please remove existing partitions 
                before running create.""".format(drive_name))
            drive_sizes.add(drives[drive_name].size())
        self.log('Found drive sizes: {}'.format(drive_sizes))
        
        prev_size = 0
        for size in sorted(drive_sizes):
            array_sizes += [size - prev_size]
            prev_size = size     
        self.log('Creating arrays with sizes: {}'.format(array_sizes),
                 logging.INFO)
        
        self.log('Partitioning drives...', logging.INFO)
        for drive in drives.values():
            drive.init_partitions()
            remaining = drive.size()
            for size in array_sizes:
                if remaining >= size:
                    drive.create_partition(size)
                    remaining -= size
                else:
                    break
            
        # Create each of the RAID arrays in turn, with an LVM PV atop them.
        self.log('Creating RAID arrays and Physical Volumes...', logging.INFO)
        part_num = 5 # The first logical partition.
        while True: # TODO: pythonize
            members = ([drive.partitions[part_num] for drive in drives.values()
                        if drive.partitions.has_key(part_num)])
            if len(members) < 2:
                # We're done.
                break
            
            # Create the RAID array.
            array = RaidArray.find_or_create()
            array.create(members)
            
            # And now the LVM PV.
            pvs[array.name] = PhysicalVolume.find_or_create(array.name)
            pvs[array.name].create()
            
            # Move onto the next partition
            part_num += 1
        
        # Now that we've got some PVs, create an VG from them.
        self.log('Creating Volume Group...', logging.INFO)
        vg = VolumeGroup.find_or_create(self.args.vg_name)
        vg.create(pvs.values())
        
        # And finally create a Logical Volume on it.
        self.log('Creating Logical Volume...', logging.INFO)
        lv = LogicalVolume.find_or_create(vg.name + '/lvol0')
        lv.create(vg)
        
        # Log the successful completion.
        self.log(
            """Volume group {} has been successfully created.
            The following RAID arrays are resyncing in the background: {}.
            You can monitor their status by running "mdadm --detail <array_name>".
            """.format(vg, pvs.keys()),
            level=logging.INFO)
    
    def examine(self):
        """Examine a single logical volume.
        
        Builds up data structures for the partition and its constituent parts,
        and prints them to screen.
        
        """
        self.log('Examining volume {}'.format(self.args.filesystem),
                 logging.INFO)
        lv = LogicalVolume.find_or_create(self.args.filesystem)
        print(lv)
        
    def remove(self):
        """Remove a physical drive from the system.
        
        This checks that all partitions on the physical drive are either
        part of a redundant array, or are unused.
        
        """
        # Get the info for the hard drive.  This bails if there are any
        # partitions that aren't LVM raid ones.
        drive = HardDrive.find_or_create(self.args.drive_to_remove)
        
        for partition in drive.partitions.values():
            # It's OK to remove this drive if either it's already marked faulty,
            # or the array is clean.
            if partition.array is not None:
                check_critical((partition.array.is_clean() or 
                                partition.faulty()),
                               'Cannot remove member {} from array {}.'
                               .format(partition, partition.array))
            
        # We're good to remove the drive.  Remove it from each array in turn.
        for raid_member in drive.raid_members.vaules():
            raid_member.remove_from_array()

    def add(self):
        """Add a new drive to an array, increasing the total number of drives
        in it.
        
        This assumes the array is currently clean.  To replace a failed drive,
        the replace() method is used.
        
        """
        lv = LogicalVolume.find_or_create(self.args.lv)
        new_drive = HardDrive.find_or_create(self.args.drive_to_add)
        other_drive_for_new_array = None
        existing_drive_sizes = set()
        
        # Check that the new drive doesn't have anything on it.
        check_critical(
            new_drive.empty,
            'New drive {} is not empty.'.format(self.args.drive_to_add))
        
        # Build up the list of drive sizes.
        for drive in lv.vg.drives().values():
            existing_drive_sizes.add(drive.size())
        largest_drive_size = max(existing_drive_sizes)
        self.log("New drive size: {}".format(new_drive.size()))
        self.log("LV drives sizes: {}".format(existing_drive_sizes))
        check_critical((new_drive.size() > largest_drive_size) or
                       (new_drive.size() in existing_drive_sizes),
                       """New drive capacity must either match one of the 
                       existing drives, or be larger than all existing 
                       drives.""")

        # Spin through the existing arrays on the LV, creating partitions of the 
        # corresponding size on the drive.  We want to spin through the arrays
        # in order of the number of drives in them (largest to smallest).
        arrays = sorted([pv.raid_array for pv in lv.vg.pvs.values()],
                        key=lambda element: len(element.members),
                        reverse=True)
        self.log("Existing array sizes: {}".format(
                                    [array.members_size() for array in arrays]))
        
        # The LV must be clean.  It may be resyncing at the moment, so wait.
        lv.wait_for_resync_complete()
                
        new_drive.init_partitions()
        for array in arrays:
            member = new_drive.create_partition(array.members_size())
            if not member:
                # Couldn't create the partition, which means we're out of space
                # on the new drive.  Drop out.
                break
            self.log("""Adding {} to array {}""".format(member, array),
                     level=logging.INFO)
            
            # For some reason the created partition sometimes doesn't appear.
            # Running partprobe solves it (though shouldn't be necessary).
            self.run_cmd(["partprobe"])
            array.grow(member, self.args.mdadm_backup_file)
            array.pv.grow()
        
        # Now check whether we can create a new array.
        if new_drive.unallocated_size() > 0:
            for drive in lv.vg.drives().values():
                if ((drive is not new_drive) and 
                    (drive.unallocated_size() > 0)):
                    assert(other_drive_for_new_array is None)
                    other_drive_for_new_array = drive
        
        if other_drive_for_new_array is not None:
            new_array_size = min(new_drive.unallocated_size(),
                                 other_drive_for_new_array.unallocated_size())
            self.log("""Creating new array with element size {} on drives 
                     {}, {}""".format(new_array_size,
                                      other_drive_for_new_array,
                                      new_drive),
                     level=logging.INFO)
            
            member1 = new_drive.create_partition(new_array_size)
            member2 = other_drive_for_new_array.create_partition(new_array_size)
            new_array = RaidArray.find_or_create()
            new_array.create([member1, member2])
            
            # Create a PV on the new array.
            pv = PhysicalVolume.find_or_create(new_array.name)
            pv.create()
            lv.vg.extend(pv.name)
        
        # Now ask the LV to grow to consume the space.
        lv.extend()
        
    def replace(self):
        """Replace is a drive in the array.
        
        Assumes the array is degraded.  Depending on the new drive size, this
        might increase the array size or not.
        
        """
        # Check assumptions.
        lv = LogicalVolume.find_or_create(self.args.lv)
        drive_to_add = HardDrive.find_or_create(self.args.drive_to_add)
        
        # Get the largest drive in this LV.
        existing_drives = {}
        for pv in lv.vg.pvs.values():
            for part in pv.raid_array.members.values():
                if ((largest_drive is None) or 
                    (largest_drive.size() < part.drive.size())):
                    largest_drive = part.drive
        
        drives_by_size = sorted()
        
        # Spin through the arrays on the LV.  Ordering?
        for pv in lv.vg.pvs.values():
            array = pv.raid_array
            
        # We're now added the drive to all degraded arrays.  Can we make a
        # new one?
        
        
        
        
        pass
    
    def log(self, msg, level=logging.DEBUG):
        self.logger_adapter.log(level, msg)
    
    def run_cmd(self, cmd):
        # Run the command.
        output = subprocess.check_output(cmd,
                                         stderr=subprocess.STDOUT)
        self.log(output)
        return output    
    
    def setup_logging(self):
        """Configure loggers for the program at start of day."""
        # The main handler writes DEBUG or higher messages to file.
        logging.basicConfig(
            filename='/tmp/lvmraid5.log',
            filemode='w',
            format='[%(asctime)s] %(class_name)s(%(instance_name)s) %(message)s',
            # format='[%(asctime)s] %(message)s',
            level=logging.DEBUG)

        # Define a Handler which writes INFO messages or higher to stderr.
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        formatter = logging.Formatter('%(message)s')
        console.setFormatter(formatter)
        logging.getLogger('').addHandler(console)
        
        # Set up a global logger adapter.
        self.logger_adapter = logging.LoggerAdapter(
            logging.getLogger(''),
            { 'class_name' : self.__class__.__name__, 'instance_name' : '' })
        
    def check_dependencies(self):
        
        def check_dependency(args):
            try:
                # Let stderr go to console - it often includes a handy
                # one-liner telling the user how to install the dependency.
                self.run_cmd(args)
            except subprocess.CalledProcessError:
                check_critical(False, 'Missing dependency: {}'.format(args[0]))
            
        self.log("Checking dependencies", logging.INFO)
        check_dependency(["mdadm", "-V"])
        check_dependency(["pvcreate", "--version"])
        check_dependency(["partprobe"])
    
if __name__ == "__main__":
    LvmRaidExec(sys.argv[1:])
    