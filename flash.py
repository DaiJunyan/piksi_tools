#!/usr/bin/env python
# Copyright (C) 2013 Swift Navigation Inc <www.swift-nav.com>

import serial_link
import struct
import time
import sys
from itertools import groupby

MSG_STM_FLASH_WRITE = 0xE0 # Callback in C
MSG_STM_FLASH_READ  = 0xE1 # Callback in both C and Python
MSG_STM_FLASH_ERASE = 0xE2 # Callback in C
MSG_STM_FLASH_DONE  = 0xE0 # Callback in Python

MSG_M25_FLASH_WRITE = 0xF0 # Callback in C
MSG_M25_FLASH_READ  = 0xF1 # Callback in both C and Python
MSG_M25_FLASH_ERASE = 0xF2 # Callback in C
MSG_M25_FLASH_DONE  = 0xF0 # Callback in Python

ADDRS_PER_OP = 128

def stm_addr_sector_map(addr):
  if   addr >= 0x08000000 and addr < 0x08004000:
    return 0
  elif addr >= 0x08004000 and addr < 0x08008000:
    return 1
  elif addr >= 0x08008000 and addr < 0x0800C000:
    return 2
  elif addr >= 0x0800C000 and addr < 0x08010000:
    return 3
  elif addr >= 0x08010000 and addr < 0x08020000:
    return 4
  elif addr >= 0x08020000 and addr < 0x08040000:
    return 5
  elif addr >= 0x08040000 and addr < 0x08060000:
    return 6
  elif addr >= 0x08060000 and addr < 0x08080000:
    return 7
  elif addr >= 0x08080000 and addr < 0x080A0000:
    return 8
  elif addr >= 0x080A0000 and addr < 0x080C0000:
    return 9
  elif addr >= 0x080C0000 and addr < 0x080E0000:
    return 10
  elif addr >= 0x080E0000 and addr < 0x08100000:
    return 11
  else:
    return None

def m25_addr_sector_map(addr):
  if addr < 0 or addr > 0xFFFFF:
    raise ValueError
  return addr >> 16

def ihx_ranges(ihx):
  def first_last(x):
    first = x.next()
    last = first
    for last in x:
      pass
    return (first[1], last[1])
  return [first_last(v) for k, v in
          groupby(enumerate(ihx.addresses()), lambda (i, x) : i - x)]

class Flash():
  _waiting_for_callback = False
  _read_callback_data = []

  def __init__(self, link, flash_type):
    self.link = link
    self.flash_type = flash_type
    if self.flash_type == "STM":
      self.link.add_callback(MSG_STM_FLASH_DONE, self._done_callback)
      self.link.add_callback(MSG_STM_FLASH_READ, self._read_callback)
      self.flash_msg_read = MSG_STM_FLASH_READ
      self.flash_msg_erase = MSG_STM_FLASH_ERASE
      self.flash_msg_write = MSG_STM_FLASH_WRITE
      self.addr_sector_map = stm_addr_sector_map
    elif self.flash_type == "M25":
      self.link.add_callback(MSG_M25_FLASH_DONE, self._done_callback)
      self.link.add_callback(MSG_M25_FLASH_READ, self._read_callback)
      self.flash_msg_read = MSG_M25_FLASH_READ
      self.flash_msg_erase = MSG_M25_FLASH_ERASE
      self.flash_msg_write = MSG_M25_FLASH_WRITE
      self.addr_sector_map = m25_addr_sector_map
    else:
      raise ValueError

  def sectors_used(self, addrs):
    sectors = set()
    for s, e in addrs:
      sectors |= set(range(self.addr_sector_map(s), self.addr_sector_map(e)+1))
    return sorted(list(sectors))

  def sector_restricted(self, sector):
    if self.flash_type == "STM":
      if sector < 4: # assuming bootloader occupies sectors 0-3
        return True
      else:
        return False
    elif self.flash_type == "M25":
      if sector == 15: # assuming authentication hash occupies sector 15
        return True
      else:
        return False
    return None

  def erase_sector(self, sector, check_sector=True):
    if check_sector and self.sector_restricted(sector):
      raise Exception("Tried to erase restricted sector")
    msg_buf = struct.pack("B", sector)
    self._waiting_for_callback = True
    self.link.send_message(self.flash_msg_erase, msg_buf)
    while self._waiting_for_callback == True:
      time.sleep(0.0001)

  def write(self, address, data):
    msg_buf = struct.pack("<IB", address, len(data))
    self._waiting_for_callback = True
    self.link.send_message(self.flash_msg_write, msg_buf + data)
    while self._waiting_for_callback == True:
      time.sleep(0.0001)

  def read(self, address, length):
    msg_buf = struct.pack("<IB", address, length)
    self._waiting_for_callback = True
    self.link.send_message(self.flash_msg_read, msg_buf)
    while self._waiting_for_callback == True:
      time.sleep(0.0001)
    return self._read_callback_data

  def _done_callback(self, data):
    self._waiting_for_callback = False

  def _read_callback(self, data):
    # 4 bytes addr, 1 byte length, length bytes data
    addr = struct.unpack('<I', data[0:4])[0];
    length = struct.unpack('B', data[4])[0];
    self._read_callback_data = list(struct.unpack(str(length)+'B', data[5:]))
    self._waiting_for_callback = False

  def write_ihx(self, ihx):
    # Erase sectors
    ihx_addrs = ihx_ranges(ihx)
    for sector in self.sectors_used(ihx_addrs):
      print ("Erasing sector %d\r" % sector),
      sys.stdout.flush()
      self.erase_sector(sector)
    print ""

    # Write data to flash and validate
    start_time = time.time()
    for start, end in ihx_addrs:
      for addr in range(start, end, ADDRS_PER_OP):
        print ("Programming flash at 0x%08X\r" % addr),
        sys.stdout.flush()
        binary = ihx.tobinstr(start=addr, size=ADDRS_PER_OP)
        self.write(addr, binary)
        flash_readback = self.read(addr, ADDRS_PER_OP)
        if flash_readback != map(ord, binary):
          raise Exception('data read from flash != data written to flash')
    print "\nSuccessfully programmed flash, total time = %d seconds" % int(time.time()-start_time)

