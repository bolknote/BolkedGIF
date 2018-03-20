# coding: utf-8
# http://bolknote.ru 2012 Evgeny Stepanischev
from __future__ import print_function
from struct import unpack_from
import struct
import itertools
import ConfigParser
import sys
import argparse

def readBlockDec(key, value):
    """Дорасшифровка флагов"""

    if isinstance(key, basestring): return ((key, value),)

    shift, out = 0, []

    # расшифровка битовых значений. На входе: (имя, сколько-занимает-в-битах)
    for name, size in key:
        if name[:8] != 'reserved':
            mask = (1 << size) - 1

            val = (value >> shift) & mask
            out.append((name, val))

        shift += size

    return out


def readBlock(file, map):
    """Чтение и расшифровка блока"""

    # собираем формат и читаем столько, сколько в формат умещается
    format   = '< ' + ' '.join(x[1] for x in map)
    calcsize = struct.calcsize(format)

    binary  = file.read(calcsize)
    content = unpack_from(format, binary)

    # сборка декодированных значений, за вычетом пустых (это формат «x») значений
    out = dict(itertools.chain(
        *(readBlockDec(key, value)
            for key, value in itertools.izip(
                (x[0] for x in map if not ~x[1].find('x')),
            content)
        )
    ))

    out.update({'raw': list(unpack_from(str(len(binary)) + 'B', binary))})

    return out


def readImageDescriptor(f):
    """Чтение дискриптора изображения"""

    info = readBlock(f, (
        ('x',       'H'),
        ('y',       'H'),
        ('width',   'H'),
        ('height',  'H'),
        (
            (
                ('LCT size',    3),
                ('reserved',    2),
                ('sorted',      1),
                ('interleaced', 1),
                ('has LCT',     1),
            ),'B'
        )
    ))

    info['LCT len'] = 3 * pow(2, info['LCT size'] + 1)

    if info['has LCT']:
       info['colors'] = unpack_from(str(info['LCT len']) + 'B', f.read(info['LCT len']))

    info['image'] = readImage(f)
    return info

def readGraphicControlExtension(f, size):
    """Разбор дополнительного блока управления изображением"""

    return readBlock(f, (
        (
            (
                ('transparent flag',1),
                ('user input',      1),
                ('disposal method', 3),
                ('reserved',        3),
            ), 'B'
        ),
        ('delay',               'H'), # 1/100 sec.
        ('transparent index',   'B'),
        ('terminator',          'x'),
    ))

def ignoreBlock(f, size):
    """Блок игнорируется"""
    return {'raw': list(unpack_from(str(size) + 'B', f.read(size + 1)))}

def readApplicationExtension(f, size):
    info = readBlock(f, (
        ('application id',      '8s'),
        ('application id code', '3s'),
    ))

    data = readDataChunks(f)

    raw = ''.join(itertools.chain(*data))
    info['raw'] += unpack_from(str(len(raw)) + 'B', raw)

    # если это расширение Нетскейпа для зацикливания
    if info['application id'] + info['application id code'] == 'NETSCAPE2.0':
        info['loop'] = unpack_from('< x H', data[1])[0]
    else:
        info['content'] = data

    return info

def readDataChunks(f):
    """Чтение кусков данных в формате блоков GIF"""
    rawsize = f.read(1)
    size    = unpack_from('B', rawsize)[0]

    chunks = []

    while size:
        chunks.append(rawsize)
        chunk, rawsize = f.read(size), f.read(1)
        size = unpack_from('B', rawsize)[0]
        chunks.append(chunk)

    chunks.append(rawsize)

    return tuple(chunks)

def readImage(f):
    """Чтение тела картинки"""
    lzwsize = f.read(1) # минимальный размер кода LZW

    return itertools.chain( (lzwsize, ), readDataChunks(f))

def readExtensionBlock(f):
    """Чтение блока расширения"""
    (marker, size) = unpack_from('BB', f.read(2))

    info = {
        0xF9: readGraphicControlExtension,
        0xFE: ignoreBlock, # комментарий
        0x01: ignoreBlock, # дополнительная текстовая информация
        0x21: ignoreBlock, # дополнительный блок с простым текстом
        0xFF: readApplicationExtension,
    }.get(marker, ignoreBlock)(f, size)

    info['ext id'] = marker
    info['raw'] = [marker, size] + info['raw']

    return info

def createIni(section, struct, config):
    config.add_section(section)

    for key, value in struct.iteritems():
        key = key.replace(' ', '_')

        if isinstance(value, (int, long, basestring)):
            config.set(section, key, str(value))
        else:
            try:
                config.set(section, key, ''.join(('0' + hex(val)[2:])[-2:] for val in value))
            except TypeError:
                pass

    return config

def readGif(name, single):
    """чтение GIF на вход — имя"""

    pictnum = 1

    with sys.stdin if name == '-' else open(name) as f:
        info = readBlock(f, (
            ('header',  '3x'),
            ('version', '3s'),
            ('width',    'H'),
            ('height',   'H'),
            (
                (
                    ('GCT size',         3),
                    ('sorted',           1),
                    ('color resolution', 3),
                    ('has GCT',          1),
                ), 'B'
            ),
            ('bgcolor index',  'B'),
            ('ratio',          'B'),
        ))

        info['GCT len'] = 3 * pow(2, info['GCT size'] + 1)

        if info['has GCT']:
            #  глобальная таблица цветов
            info['colors'] = unpack_from(str(info['GCT len']) + 'B', f.read(info['GCT len']))

        config = ConfigParser.RawConfigParser()
        createIni('global', info, config)

        while 1:
            # Какой следующий блок у нас?
            try:
                blockid = unpack_from('B', f.read(1))[0]
            except struct.error:
                # всё, данные кончились
                break

            # 0x00-0x7F (0-127) - блоки с графической информацией; исключение составляет блок-терминатор (0x3B);
            # 0x80-0xF9 (128-249) - блоки управления;
            # 0xFA-0xFF (250-255) - специальные блоки

            try:
                block = {
                    0x2C: readImageDescriptor,
                    0x21: readExtensionBlock,
                    0x3B: lambda f: {'raw': [f.read(1)]}, # конец изображения
                }.get(blockid)(f)
            except TypeError as e:
                raise TypeError(blockid)

            # выдача тела
            if single and blockid == 0x2C:
                print(''.join(block['image']), end='')
                return # выход! остальные данные не нужны

            block['raw'].insert(0, blockid)
            block['block id'] = blockid

            createIni(str(pictnum), block, config)
            pictnum+=1

    config.write(sys.stdout)

parser = argparse.ArgumentParser(description='Parse GIF to structure')
parser.add_argument('image', metavar='gif', type=str, help='GIF to parse')
parser.add_argument('--body', type=bool, required=False, help='only GIF image body', default=False)

args = parser.parse_args()

readGif(args.image, args.body)