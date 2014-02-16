# Install required packages.
package { 'lvm2': ensure => installed }
package { 'mdadm': ensure => installed }
package { 'python-pexpect': ensure => installed }
