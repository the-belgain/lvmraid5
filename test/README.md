# How to test

The easiest way to run the regression tests is to use the Vagrant box provided.  This is a headless Ubuntu box with the required dependencies installed, and the right configuration of virtual hard disks.

## Creating the VM

* Install VirtualBox
* Install Vagrant
* Clone this git repo
* Change to the test/vagrant directory, and run ```vagrant up --provision```

## Using the VM

Once you've created and started your VM you can ssh to localhost:2222, user/pass: vagrant/vagrant.  The git checkout of this project from the host is mounted at /home/vagrant/lvmraid5.

To run the test script:
```cd /home/vagrant/lvmraid5/test
sudo python -m unittest test``` 
