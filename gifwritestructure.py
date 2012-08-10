# coding: utf-8
# http://bolknote.ru 2012 Evgeny Stepanischev
from __future__ import print_function
import ConfigParser
import struct
import argparse

def printbin(hex):
    binary = (struct.pack('B', int(hex[i:i+2], 16)) for i in xrange(0, len(hex), 2))
    print(''.join(binary), end='')

def readStructure(structurename, filesdir):

    config = ConfigParser.ConfigParser()
    config.read(structurename)

    pict = 0
    for section in config.sections():
        try:
            block_id = config.getint(section, 'block_id')
        except ConfigParser.NoOptionError:
            block_id = None

        # 2C — Image Descriptor
        # 21 — Extension

        # Блок пропуска всего ненужного — того, что не попадёт в результирующий файл
        if block_id in (0x2C, 0x21, None):
            if block_id == 0x21:
                ext_id = config.getint(section, 'ext_id')

                # FF — Application Extension
                # F9 — Graphics Control Extension

                if ext_id != 0xF9 and ext_id != 0xFF: continue

                if ext_id == 0xFF:
                    application = config.get(section, 'application_id') + config.get(section, 'application_id_code')
                    if application != 'NETSCAPE2.0':
                        continue
        else:
            continue

       # print(config.items(section))

        printbin(config.get(section, 'raw'))

        if block_id == 0x2C and config.getboolean(section, 'has_lct'):
            printbin(config.get(section, 'colors'))
        elif block_id is None and config.getboolean(section, 'has_gct'):
            printbin(config.get(section, 'colors'))
        
        if block_id == 0x2C:
            name = "%03d.raw" % pict
            pict += 1

            print(open(filesdir + '/' + name, 'rb').read(), end='')


parser = argparse.ArgumentParser(description='Write uncompressed GIF')
parser.add_argument('tempdir', metavar='tempdir', type=str)

args = parser.parse_args()

readStructure(args.tempdir + '/structure.cfg', args.tempdir)