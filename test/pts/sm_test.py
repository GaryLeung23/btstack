#!/usr/bin/env python
#
# Perform Security Manager Test Cases using two BTstack instances
#
# Copyright 2018 BlueKitchen GmbH
#

import os
import subprocess
import sys
import time
import signal
import select
import fcntl
import csv
import shutil
import datetime

io_capabilities = [
'IO_CAPABILITY_DISPLAY_ONLY',
'IO_CAPABILITY_DISPLAY_YES_NO',
'IO_CAPABILITY_KEYBOARD_ONLY',
'IO_CAPABILITY_NO_INPUT_NO_OUTPUT',
'IO_CAPABILITY_KEYBOARD_DISPLAY']

SM_AUTHREQ_NO_BONDING        = 0x00
SM_AUTHREQ_BONDING           = 0x01
SM_AUTHREQ_MITM_PROTECTION   = 0x04
SM_AUTHREQ_SECURE_CONNECTION = 0x08
SM_AUTHREQ_KEYPRESS          = 0x10

# tester config
regenerate = True
# usb_paths = ['4', '6']
usb_paths = ['3', '5']

class Node:

    def __init__(self):
        self.name = 'node'
        self._got_line = False
        self.peer_addr = None

    def get_name(self):
        return self.name

    def set_name(self, name):
        self.name = name

    def set_auth_req(self, auth_req):
        self.auth_req = auth_req

    def set_io_capabilities(self, io_capabilities):
        self.io_capabilities = io_capabilities

    def set_oob_data(self, oob_data):
        self.oob_data = oob_data

    def set_usb_path(self, path):
        self.usb_path = path

    def get_stdout_fd(self):
        return self.stdout.fileno()

    def read_stdout(self):
        c = os.read(self.stdout.fileno(), 1)
        if len(c) == 0: 
            return
        if c in '\n\r':
            if len(self.linebuffer) > 0:
                self._got_line = True
        else: 
            self.linebuffer += c

    def got_line(self):
        return self._got_line

    def fetch_line(self):
        line = self.linebuffer
        self.linebuffer = ''
        self._got_line = False
        return line

    def start_process(self):
        args = ['./sm_test', '-u', self.usb_path]
        if self.peer_addr != None:
            args.append('-a')
            args.append(self.peer_addr)
        args.append('-r')
        args.append(self.auth_req)
        print('%s - "%s"' % (self.name, ' '.join(args)))
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (self.stdin, self.stdout) = (self.p.stdin, self.p.stdout)
        self.linebuffer = ''

    def set_packet_log(self, path):
        self.packet_log = path

    def get_packet_log(self):
        return self.packet_log

    def set_bd_addr(self, addr):
        self.bd_addr = addr

    def get_bd_addr(self):
        return self.bd_addr

    def set_peer_addr(self, addr):
        self.peer_addr = addr

    def write(self, string):
        self.stdin.write(string)

    def terminate(self):
        self.write('x')
        self.p.terminate()

def run(test_descriptor, nodes):
    state = 'W4_SLAVE_BD_ADDR'
    pairing_complete = []
    while True:
        # create map fd -> node
        nodes_by_fd = { node.get_stdout_fd():node for node in nodes}
        read_fds = nodes_by_fd.keys()
        (read_ready, write_ready, exception_ready) = select.select(read_fds,[],[])
        for fd in read_ready:
            node = nodes_by_fd[fd]
            node.read_stdout()
            if node.got_line():
                line = node.fetch_line()
                # print('%s: %s' % (node.get_name(), line))
                if line.startswith('Packet Log: '):
                    path = line.split(': ')[1]
                    node.set_packet_log(path)
                    print('%s log %s' % (node.get_name(), path))
                if line.startswith('BD_ADDR: '):
                    addr = line.split(': ')[1]
                    node.set_bd_addr(addr)
                    print('%s started' % node.get_name())
                    if state == 'W4_SLAVE_BD_ADDR':
                        # peripheral started, start central
                        state = 'W4_CONNECTED'
                        master_role = test_descriptor['master_role']
                        master = Node()
                        # configure master
                        master.set_name(master_role)
                        master.usb_path = usb_paths[1]
                        master.set_peer_addr(addr)
                        master.set_auth_req(test_descriptor[master_role + '_auth_req'])
                        master.set_io_capabilities(test_descriptor[master_role + '_io_capabilities'])
                        master.start_process()
                        nodes.append(master)
                if line.startswith('CONNECTED:'):
                    print('%s connected' % node.get_name())
                    if state == 'W4_CONNECTED':
                        state = 'W4_PAIRING'
                if line.startswith('JUST_WORKS_REQUEST'):
                    print('%s just works requested' % node.get_name())
                    node.write('a')
                if line.startswith('PAIRING_COMPLETE'):
                    result = line.split(': ')[1]
                    (status,reason) = result.split(',')
                    test_descriptor[node.get_name()+'_pairing_complete_status'] = status
                    test_descriptor[node.get_name()+'_pairing_complete_reason'] = reason
                    print('%s pairing complete: status %s, reason %s' % (node.get_name(), status, reason))
                    pairing_complete.append(node.get_name())
                    # check if both are complete
                    if len(pairing_complete) == 2:
                        return

def write_config(fout, test_descriptor):
    attributes = [
        'header',
        '---',
        'bd_addr',
        'role',
        'io_capabilities',
        'mitm',
        'secure_connection',
        'oob_data',
        'pairing_complete_status',
        'pairing_complete_reason']

    # header
    fout.write('Test: %s\n' % test_descriptor['name'])
    fout.write('Date: %s\n' % str(datetime.datetime.now()))
    fout.write('\n')
    attribute_len = 25
    value_len     = 30
    format_string = '%%-%us|%%-%us|%%-%us\n' % (attribute_len, value_len, value_len)
    for attribute in attributes:
        name = attribute
        if attribute == 'header':
            name  = 'Attribute'
            iut   = 'IUT'
            tester = 'Tester'
        elif attribute == '---':
            name   = '-' * attribute_len
            iut    = '-' * value_len
            tester = '-' * value_len
        elif attribute == 'io_capabilities':
            iut    = io_capabilities[int(test_descriptor['iut_io_capabilities'   ])]
            tester = io_capabilities[int(test_descriptor['tester_io_capabilities'])]
        elif attribute == 'mitm':
            iut    = (int(test_descriptor['iut_auth_req'   ]) & SM_AUTHREQ_MITM_PROTECTION)   >> 2
            tester = (int(test_descriptor['tester_auth_req']) & SM_AUTHREQ_MITM_PROTECTION)   >> 2
        elif attribute == 'secure_connection':
            iut    = (int(test_descriptor['iut_auth_req'   ]) & SM_AUTHREQ_SECURE_CONNECTION) >> 3
            tester = (int(test_descriptor['tester_auth_req']) & SM_AUTHREQ_SECURE_CONNECTION) >> 3
        else:
            iut    = test_descriptor['iut_'    + attribute]
            tester = test_descriptor['tester_' + attribute]
        fout.write(format_string % (name, iut, tester))

def run_test(test_descriptor):
    # shutdown previous sm_test instances
    try:
        subprocess.call("killall sm_test", shell = True)
    except:
        pass

    test_name = test_descriptor['name']
    print('Test: %s' % test_name)

    if '/SLA/' in test_descriptor['name']:
        iut_role    = 'responder'
        tester_role = 'master'
        slave_role  = 'iut'
        test_descriptor['master_role'] = 'tester'
    else:
        iut_role    = 'initiator'
        tester_role = "slave"
        slave_role  = 'tester'
        test_descriptor['master_role'] = 'iut'
    test_descriptor['iut_role'   ] = iut_role
    test_descriptor['tester_role'] = tester_role

    slave = Node()

    # configure slave
    slave.set_name(slave_role)
    slave.usb_path = usb_paths[0]
    slave.set_auth_req(test_descriptor[slave_role + '_auth_req'])
    slave.set_io_capabilities(test_descriptor[slave_role + '_io_capabilities'])

    # start up slave
    slave.start_process()

    nodes = [slave]

    # run test
    try:
        run(test_descriptor, nodes)

        # identify iut and tester
        if iut_role == 'slave':
            iut    = nodes[0]
            tester = nodes[1]
        else:
            iut    = nodes[1]
            tester = nodes[0]

        # move hci logs into result folder
        test_folder =  test_descriptor['test_folder']
        os.makedirs(test_folder)
        shutil.move(iut.get_packet_log(), test_folder + '/iut.pklg')
        shutil.move(tester.get_packet_log(), test_folder + '/tester.pklg')

        # write config
        with open (test_folder + '/config.txt', "wt") as fout:
            test_descriptor['iut_bd_addr'] = iut.get_bd_addr()
            test_descriptor['tester_bd_addr'] = tester.get_bd_addr()
            write_config(fout, test_descriptor)

    except KeyboardInterrupt:
        print('Interrupted')

    # shutdown
    for node in nodes:
        node.terminate()
    print("Done\n")


# read tests
with open('sm_test.csv') as csvfile:
    reader = csv.DictReader(csvfile)
    for test_descriptor in reader:
        test_name = test_descriptor['name']
        test_folder = test_name.replace('/', '_')
        test_descriptor['test_folder'] = test_folder

        # skip test if regenerate not requested
        if os.path.exists(test_folder):
            if regenerate:
                shutil.rmtree(test_folder)
            else: 
                print('Test: %s (completed)' % test_name)
                continue

        # run test
        print(test_descriptor)
        run_test(test_descriptor)
