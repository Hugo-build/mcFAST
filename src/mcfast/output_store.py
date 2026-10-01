"""Shared, bounded OpenFAST output access without whole-file decompression."""
from collections import OrderedDict
from pathlib import Path
import struct
import tempfile
import threading

import numpy as np

_LOCK = threading.RLock()
_CACHED = None
_CHANNEL_BUDGET = 64 * 1024 * 1024


class OutputStore:
    def __init__(self, source):
        self.source = source
        self.lock = threading.RLock()
        self.decoded = OrderedDict()
        self.decoded_bytes = 0
        self.backing = None
        if source.suffix == '.outb':
            self._binary()
        else:
            self._text()
        if len(self.names) != len(self.units) or self.names[0] != 'Time':
            raise ValueError('Inconsistent output columns or missing Time column.')
        if self.units[0] not in {'s', 'sec'}:
            raise ValueError('Unsupported time units.')
        if len(self.times) < 2 or not np.all(np.isfinite(self.times)) or not np.all(np.diff(self.times) > 0):
            raise ValueError('Results need at least two finite, increasing timestamps.')
        self.times.flags.writeable = False

    def _binary(self):
        with self.source.open('rb') as stream:
            def read(n):
                value = stream.read(n)
                if len(value) != n:
                    raise ValueError('Truncated OpenFAST output; regenerate the run output.')
                return value

            def number(fmt):
                return struct.unpack('<' + fmt, read(struct.calcsize('<' + fmt)))[0]

            file_id = number('h')
            if file_id not in {1, 2, 3, 4}:
                raise ValueError('Unsupported OpenFAST binary format.')
            width = number('h') if file_id == 4 else 10
            columns, count = number('i'), number('i')
            if width <= 0 or columns <= 0 or count < 2:
                raise ValueError('Invalid OpenFAST header dimensions.')
            if count * columns * (8 if file_id == 3 else 2) > self.source.stat().st_size:
                raise ValueError('Truncated OpenFAST data or invalid header dimensions.')
            a, b = number('d'), number('d')
            self.scale = self.offset = None
            if file_id != 3:
                self.scale = np.frombuffer(read(columns * 4), dtype='<f4').astype(float)
                self.offset = np.frombuffer(read(columns * 4), dtype='<f4').astype(float)
                if not np.all(np.isfinite(self.scale)) or np.any(self.scale == 0) or not np.all(np.isfinite(self.offset)):
                    raise ValueError('Invalid channel scaling in OpenFAST header.')
            description_size = number('i')
            if description_size < 0 or description_size > self.source.stat().st_size:
                raise ValueError('Invalid OpenFAST description length.')
            read(description_size)
            self.names = [read(width).decode('ascii').strip() for _ in range(columns + 1)]
            self.units = [read(width).decode('ascii').strip()[1:-1] for _ in range(columns + 1)]
            if file_id == 1:
                if not np.isfinite(a) or a == 0:
                    raise ValueError('Invalid timestamp scaling.')
                packed_time = np.frombuffer(read(count * 4), dtype='<i4')
                self.times = (packed_time.astype(float) - b) / a
            else:
                self.times = a + b * np.arange(count, dtype=float)
            offset = stream.tell()
            dtype = '<f8' if file_id == 3 else '<i2'
            expected = offset + count * columns * np.dtype(dtype).itemsize
            if self.source.stat().st_size != expected:
                raise ValueError('Incorrect OpenFAST data length; regenerate the run output.')
            self.packed = np.memmap(self.source, dtype=dtype, mode='r', offset=offset, shape=(count, columns))

    def _text(self):
        # Convert text in bounded chunks to a disk-backed array, avoiding a list
        # of every token and a resident floating-point matrix.
        self.backing = tempfile.TemporaryFile()
        with self.source.open() as stream:
            header = [stream.readline() for _ in range(8)]
            self.names = header[6].split()
            self.units = [unit[1:-1] for unit in header[7].split()]
            if not self.names:
                raise ValueError('Missing OpenFAST text header.')
            chunk = []
            count = 0

            def flush():
                nonlocal count
                rows = np.loadtxt(chunk, ndmin=2)
                if rows.shape[1] != len(self.names):
                    raise ValueError('Inconsistent output columns.')
                self.backing.write(rows.astype('<f8', copy=False).tobytes())
                count += len(rows)

            for line in stream:
                if not line.strip():
                    continue
                chunk.append(line)
                if len(chunk) == 512:
                    flush()
                    chunk.clear()
            if chunk:
                flush()
        if count < 2:
            raise ValueError('Results need at least two samples.')
        self.backing.flush()
        matrix = np.memmap(self.backing, dtype='<f8', mode='r', shape=(count, len(self.names)))
        self.times = np.array(matrix[:, 0])
        self.packed = matrix[:, 1:]
        self.scale = self.offset = None

    def values(self, index):
        with self.lock:
            if index in self.decoded:
                self.decoded.move_to_end(index)
                return self.decoded[index]
            values = np.array(self.packed[:, index], dtype=float)
            if self.scale is not None:
                values -= self.offset[index]
                values /= self.scale[index]
            values.flags.writeable = False
            while self.decoded and self.decoded_bytes + values.nbytes > _CHANNEL_BUDGET:
                _, old = self.decoded.popitem(last=False)
                self.decoded_bytes -= old.nbytes
            if values.nbytes <= _CHANNEL_BUDGET:
                self.decoded[index] = values
                self.decoded_bytes += values.nbytes
            return values


class Channel(dict):
    def __init__(self, store, index, unit):
        super().__init__(unit=unit)
        self.store, self.index = store, index

    def __getitem__(self, key):
        return self.store.values(self.index) if key == 'values' else super().__getitem__(key)


def get_store(source):
    global _CACHED
    stat = source.stat()
    key = (source.resolve(), stat.st_mtime_ns, stat.st_size, stat.st_ino)
    # One loading operation shared by metadata, playback and series requests.
    with _LOCK:
        if _CACHED is None or _CACHED[0] != key:
            _CACHED = None
            store = OutputStore(source)
            _CACHED = key, store
        return _CACHED[1]


def release_store(run_dir):
    global _CACHED
    with _LOCK:
        if _CACHED is not None and _CACHED[0][0].parent == run_dir.resolve():
            # Existing requests keep their own reference until they finish.
            _CACHED = None
