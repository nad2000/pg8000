# vim: sw=4:expandtab:foldmethod=marker
#
# Copyright (c) 2007-2009, Mathieu Fenniak
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
# * Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
# * The name of the author may not be used to endorse or promote products
# derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

__author__ = "Mathieu Fenniak"

import datetime
from decimal import Decimal
from struct import unpack, Struct
from .errors import NotSupportedError, InternalError, \
    ArrayContentEmptyError, ArrayContentNotHomogenousError, \
    ArrayContentNotSupportedError, ArrayDimensionsNotConsistentError
from itertools import islice
from pg8000 import h_pack, h_unpack, i_pack, i_unpack, q_pack, q_unpack, \
    f_unpack, d_pack, d_unpack, hhhh_pack, hhhh_unpack, qii_pack, qii_unpack, \
    dii_pack, dii_unpack, ii_pack, ii_unpack, iii_pack, iii_unpack

try:
    from pytz import utc
except ImportError:
    ZERO = datetime.timedelta(0)

    class UTC(datetime.tzinfo):

        def utcoffset(self, dt):
            return ZERO

        def tzname(self, dt):
            return "UTC"

        def dst(self, dt):
            return ZERO
    utc = UTC()

bool_struct = Struct("?")
bool_unpack = bool_struct.unpack
bool_pack = bool_struct.pack


class Bytea(bytes):
    pass


class Interval(object):
    def __init__(self, microseconds=0, days=0, months=0):
        self.microseconds = microseconds
        self.days = days
        self.months = months

    def _setMicroseconds(self, value):
        if not isinstance(value, int):
            raise TypeError("microseconds must be an int")
        elif not (min_int8 < value < max_int8):
            raise OverflowError(
                "microseconds must be representable as a 64-bit integer")
        else:
            self._microseconds = value

    def _setDays(self, value):
        if not isinstance(value, int):
            raise TypeError("days must be an int")
        elif not (min_int4 < value < max_int4):
            raise OverflowError(
                "days must be representable as a 32-bit integer")
        else:
            self._days = value

    def _setMonths(self, value):
        if not isinstance(value, int):
            raise TypeError("months must be an int")
        elif not (min_int4 < value < max_int4):
            raise OverflowError(
                "months must be representable as a 32-bit integer")
        else:
            self._months = value

    microseconds = property(lambda self: self._microseconds, _setMicroseconds)
    days = property(lambda self: self._days, _setDays)
    months = property(lambda self: self._months, _setMonths)

    def __repr__(self):
        return "<Interval %s months %s days %s microseconds>" % (
            self.months, self.days, self.microseconds)

    def __eq__(self, other):
        return other is not None and isinstance(other, Interval) and \
            self.months == other.months and self.days == other.days and \
            self.microseconds == other.microseconds

    def __neq__(self, other):
        return not self.__eq__(other)


def pg_type_info(typ):
    value = None
    if isinstance(typ, dict):
        value = typ["value"]
        typ = typ["type"]

    data = py_types.get(typ)
    if data is None:
        raise NotSupportedError("type %r not mapped to pg type" % typ)

    # permit the type data to be determined by the value, if provided
    inspect_func = data.get("inspect")
    if value is not None and inspect_func is not None:
        data = inspect_func(value)

    type_oid = data.get("typeoid")
    if type_oid is None:
        raise InternalError("type %r has no type_oid" % typ)
    elif type_oid == -1:
        # special case: NULL values
        return type_oid, 0

    # prefer bin, but go with whatever exists
    if data.get("bin_out"):
        format = 1
    elif data.get("txt_out"):
        format = 0
    else:
        raise InternalError("no conversion fuction for type %r" % typ)

    return type_oid, format


def pg_value(value, fc, **kwargs):
    typ = type(value)
    data = py_types.get(typ)
    if data is None:
        raise NotSupportedError("type %r not mapped to pg type" % typ)

    # permit the type conversion to be determined by the value, if provided
    inspect_func = data.get("inspect")
    if value is not None and inspect_func is not None:
        data = inspect_func(value)

    # special case: NULL values
    if data.get("typeoid") == -1:
        return None

    if fc == 0:
        func = data.get("txt_out")
    elif fc == 1:
        func = data.get("bin_out")
    else:
        raise InternalError("unrecognized format code %r" % fc)
    if func is None:
        raise NotSupportedError(
            "type %r, format code %r not supported" % (typ, fc))
    return func(value, **kwargs)


def py_value(v, description, **kwargs):
    if v is None:
        # special case - NULL value
        return None

    try:
        fc, func = pg_types[description['type_oid']]
    except KeyError as e:
        raise NotSupportedError("type oid %r not supported" % str(e))

    fmt = description['format']
    if fc != fmt:
        raise NotSupportedError("format code %r not supported" % fmt)
    return func(v, **kwargs)


def boolrecv(data, **kwargs):
    return bool_unpack(data)[0]


def boolsend(v, **kwargs):
    return bool_pack(v)

min_int2, max_int2 = -2 ** 15, 2 ** 15
min_int4, max_int4 = -2 ** 31, 2 ** 31
min_int8, max_int8 = -2 ** 63, 2 ** 63


def int_inspect(value):
    if min_int2 < value < max_int2:
        return {"typeoid": 21, "bin_out": int2send}
    elif min_int4 < value < max_int4:
        return {"typeoid": 23, "bin_out": int4send}
    elif min_int8 < value < max_int8:
        return {"typeoid": 20, "bin_out": int8send}
    else:
        return {"typeoid": 1700, "bin_out": numeric_send}


def int2recv(data, **kwargs):
    return h_unpack(data)[0]


def int2send(v, **kwargs):
    return h_pack(v)


def int4recv(data, **kwargs):
    return i_unpack(data)[0]


def int4send(v, **kwargs):
    return i_pack(v)


def int8recv(data, **kwargs):
    return q_unpack(data)[0]


def int8send(v, **kwargs):
    return q_pack(v)


def float4recv(data, **kwargs):
    return f_unpack(data)[0]


def float8recv(data, **kwargs):
    return d_unpack(data)[0]


def float8send(v, **kwargs):
    return d_pack(v)


def datetime_inspect(value):
    if value.tzinfo is not None:
        # send as timestamptz if timezone is provided
        return {"typeoid": 1184, "bin_out": timestamptz_send}
    else:
        # otherwise send as timestamp
        return {"typeoid": 1114, "bin_out": timestamp_send}


def timestamp_recv(data, integer_datetimes, **kwargs):
    if integer_datetimes:
        # data is 64-bit integer representing milliseconds since 2000-01-01
        val = q_unpack(data)[0]
        return datetime.datetime(2000, 1, 1) + \
            datetime.timedelta(microseconds=val)
    else:
        # data is double-precision float representing seconds since 2000-01-01
        val = d_unpack(data)[0]
        return datetime.datetime(2000, 1, 1) + datetime.timedelta(seconds=val)


# return a timezone-aware datetime instance if we're reading from a
# "timestamp with timezone" type.  The timezone returned will always be UTC,
# but providing that additional information can permit conversion to local.
def timestamptz_recv(data, **kwargs):
    return timestamp_recv(data, **kwargs).replace(tzinfo=utc)


def timestamp_send(v, integer_datetimes, **kwargs):
    delta = v - datetime.datetime(2000, 1, 1)
    val = delta.microseconds + delta.seconds * 1000000 + \
        delta.days * 86400000000
    if integer_datetimes:
        # data is 64-bit integer representing milliseconds since 2000-01-01
        return q_pack(val)
    else:
        # data is double-precision float representing seconds since 2000-01-01
        return d_pack(val / 1000.0 / 1000.0)


def timestamptz_send(v, **kwargs):
    # timestamps should be sent as UTC.  If they have zone info,
    # convert them.
    return timestamp_send(v.astimezone(utc).replace(tzinfo=None), **kwargs)


def date_in(data, **kwargs):
    year = int(data[0:4])
    month = int(data[5:7])
    day = int(data[8:10])
    return datetime.date(year, month, day)


def date_out(v, **kwargs):
    return textout(v.isoformat(), **kwargs)


def time_in(data, **kwargs):
    hour = int(data[0:2])
    minute = int(data[3:5])
    sec = Decimal(data[6:].decode("ascii"))
    return datetime.time(
        hour, minute, int(sec), int((sec - int(sec)) * 1000000))


def time_out(v, **kwargs):
    return textout(v.isoformat(), **kwargs)


def numeric_in(data, **kwargs):
    if data.find(b".") == -1:
        return int(data)
    else:
        return Decimal(data)


def numeric_recv(data, **kwargs):
    num_digits, weight, sign, scale = hhhh_unpack(data[:8])
    pos_weight = max(0, weight) + 1
    digits = ['0000'] * abs(min(weight, 0)) + \
        [str(d).zfill(4) for d in unpack("!" + ("h" * num_digits), data[8:])] \
        + ['0000'] * (pos_weight - num_digits)
    return Decimal(
        ''.join(['-' if sign else '', ''.join(digits[:pos_weight]), '.',
        ''.join(digits[pos_weight:])[:scale]]))

DEC_DIGITS = 4


def numeric_send(d, **kwargs):
    # This is a very straight port of src/backend/utils/adt/numeric.c
    # set_var_from_str()
    s = str(d)
    pos = 0
    sign = 0
    if s[0] == '-':
        sign = 0x4000  # NEG
        pos = 1
    elif s[0] == '+':
        sign = 0  # POS
        pos = 1
    have_dp = False
    decdigits = [0, 0, 0, 0]
    dweight = -1
    dscale = 0
    for char in s[pos:]:
        if char.isdigit():
            decdigits.append(int(char))
            if not have_dp:
                dweight += 1
            else:
                dscale += 1
            pos += 1
        elif char == '.':
            have_dp = True
            pos += 1
        else:
            break

    if len(s) > pos:
        char = s[pos]
        if char == 'e' or char == 'E':
            pos += 1
            exponent = int(s[pos:])
            dweight += exponent
            dscale -= exponent
            if dscale < 0:
                dscale = 0

    if dweight >= 0:
        weight = int((dweight + 1 + DEC_DIGITS - 1) / DEC_DIGITS - 1)
    else:
        weight = int(-((-dweight - 1) / DEC_DIGITS + 1))
    offset = (weight + 1) * DEC_DIGITS - (dweight + 1)
    ndigits = int(
        (len(decdigits) - DEC_DIGITS + offset + DEC_DIGITS - 1) / DEC_DIGITS)

    i = DEC_DIGITS - offset
    decdigits.extend([0, 0, 0])
    ndigits_ = ndigits
    digits = b''
    while ndigits_ > 0:
        # ifdef DEC_DIGITS == 4
        digits += h_pack(
            ((decdigits[i] * 10 + decdigits[i + 1]) * 10 + decdigits[i + 2])
            * 10 + decdigits[i + 3])
        ndigits_ -= 1
        i += DEC_DIGITS

    # strip_var()
    for char in digits:
        if ndigits == 0:
            break
        if char == '0':
            weight -= 1
            ndigits -= 1
        else:
            break

    for char in reversed(digits):
        if ndigits == 0:
            break
        if char == '0':
            ndigits -= 1
        else:
            break

    if ndigits == 0:
        sign = 0x4000  # pos
        weight = 0
    # ----------

    retval = hhhh_pack(ndigits, weight, sign, dscale) + digits
    return retval


def numeric_out(v, **kwargs):
    return str(v).encode("ascii")


# PostgreSQL encodings:
#   http://www.postgresql.org/docs/8.3/interactive/multibyte.html
# Python encodings:
#   http://www.python.org/doc/2.4/lib/standard-encodings.html
#
# Commented out encodings don't require a name change between PostgreSQL and
# Python.  If the py side is None, then the encoding isn't supported.
pg_to_py_encodings = {
    # Not supported:
    "mule_internal": None,
    "euc_tw": None,

    # Name fine as-is:
    #"euc_jp",
    #"euc_jis_2004",
    #"euc_kr",
    #"gb18030",
    #"gbk",
    #"johab",
    #"sjis",
    #"shift_jis_2004",
    #"uhc",
    #"utf8",

    # Different name:
    "euc_cn": "gb2312",
    "iso_8859_5": "is8859_5",
    "iso_8859_6": "is8859_6",
    "iso_8859_7": "is8859_7",
    "iso_8859_8": "is8859_8",
    "koi8": "koi8_r",
    "latin1": "iso8859-1",
    "latin2": "iso8859_2",
    "latin3": "iso8859_3",
    "latin4": "iso8859_4",
    "latin5": "iso8859_9",
    "latin6": "iso8859_10",
    "latin7": "iso8859_13",
    "latin8": "iso8859_14",
    "latin9": "iso8859_15",
    "sql_ascii": "ascii",
    "win866": "cp886",
    "win874": "cp874",
    "win1250": "cp1250",
    "win1251": "cp1251",
    "win1252": "cp1252",
    "win1253": "cp1253",
    "win1254": "cp1254",
    "win1255": "cp1255",
    "win1256": "cp1256",
    "win1257": "cp1257",
    "win1258": "cp1258",
}


def encoding_convert(encoding):
    encoding = encoding.lower()
    if isinstance(encoding, bytes):
        encoding = encoding.decode("ascii")
    return pg_to_py_encodings.get(encoding, encoding)


def varcharin(data, client_encoding, **kwargs):
    return str(data, encoding_convert(client_encoding))


def textout(v, client_encoding, **kwargs):
    return v.encode(encoding_convert(client_encoding))


def byteasend(v, **kwargs):
    return v


def bytearecv(data, **kwargs):
    return Bytea(data)


def interval_recv(data, integer_datetimes, **kwargs):
    if integer_datetimes:
        microseconds, days, months = qii_unpack(data)
    else:
        seconds, days, months = dii_unpack(data)
        microseconds = int(seconds * 1000 * 1000)
    return Interval(microseconds, days, months)


def interval_send(data, integer_datetimes, **kwargs):
    if integer_datetimes:
        return qii_pack(data.microseconds, data.days, data.months)
    else:
        return dii_pack(
            data.microseconds / 1000.0 / 1000.0, data.days, data.months)


def array_recv(data, **kwargs):
    data = iter(data)

    def take(n):
        return bytes(islice(data, n))

    dim, hasnull, typeoid = iii_unpack(take(12))

    # get type conversion method for typeoid
    conversion = pg_types[typeoid][1]

    # Read dimension info
    dim_lengths = [ii_unpack(take(8))[0] for i in range(dim)]

    # Read all array values
    array_values = []
    for dta in zip(*[data] * 4):
        element_len, = i_unpack(bytes(dta))
        if element_len == -1:
            array_values.append(None)
        else:
            array_values.append(conversion(take(element_len), **kwargs))

    # at this point, {{1,2,3},{4,5,6}}::int[][] looks like [1,2,3,4,5,6].
    # go through the dimensions and fix up the array contents to match
    # expected dimensions
    for length in reversed(dim_lengths[1:]):
        array_values = list(map(list, zip(*[iter(array_values)] * length)))
    return array_values


def array_inspect(value):
    # Check if array has any values.  If not, we can't determine the proper
    # array typeoid.
    first_element = array_find_first_element(value)
    if first_element is None:
        raise ArrayContentEmptyError("array has no values")

    # supported array output
    typ = type(first_element)
    if issubclass(typ, int):
        # special int array support -- send as smallest possible array type
        special_int_support = True
        int2_ok, int4_ok, int8_ok = True, True, True
        for v in array_flatten(value):
            if v is None:
                continue
            if min_int2 < v < max_int2:
                continue
            int2_ok = False
            if min_int4 < v < max_int4:
                continue
            int4_ok = False
            if min_int8 < v < max_int8:
                continue
            int8_ok = False
        if int2_ok:
            array_typeoid = 1005  # INT2[]
        elif int4_ok:
            array_typeoid = 1007  # INT4[]
        elif int8_ok:
            array_typeoid = 1016  # INT8[]
        else:
            raise ArrayContentNotSupportedError(
                "numeric not supported as array contents")
    else:
        special_int_support = False
        array_typeoid = py_array_types.get(typ)
        if array_typeoid is None:
            raise ArrayContentNotSupportedError(
                "type %r not supported as array contents" % typ)

    # check for homogenous array
    for v in array_flatten(value):
        if v is not None and not isinstance(v, typ):
            raise ArrayContentNotHomogenousError(
                "not all array elements are of type %r" % typ)

    # check that all array dimensions are consistent
    array_check_dimensions(value)

    if special_int_support:
        if array_typeoid == 1005:
            type_data = {"typeoid": 21, "bin_out": int2send}
        elif array_typeoid == 1007:
            type_data = {"typeoid": 23, "bin_out": int4send}
        elif array_typeoid == 1016:
            type_data = {"typeoid": 20, "bin_out": int8send}
    else:
        type_data = py_types[typ]
    return {
        "typeoid": array_typeoid,
        "bin_out": array_send(type_data["typeoid"], type_data["bin_out"])
    }


def array_find_first_element(arr):
    for v in array_flatten(arr):
        if v is not None:
            return v
    return None


def array_flatten(arr):
    for v in arr:
        if isinstance(v, list):
            for v2 in array_flatten(v):
                yield v2
        else:
            yield v


def array_check_dimensions(arr):
    v0 = arr[0]
    if isinstance(v0, list):
        req_len = len(v0)
        req_inner_lengths = array_check_dimensions(v0)
        for v in arr:
            inner_lengths = array_check_dimensions(v)
            if len(v) != req_len or inner_lengths != req_inner_lengths:
                raise ArrayDimensionsNotConsistentError(
                    "array dimensions not consistent")
        retval = [req_len]
        retval.extend(req_inner_lengths)
        return retval
    else:
        # make sure nothing else at this level is a list
        for v in arr:
            if isinstance(v, list):
                raise ArrayDimensionsNotConsistentError(
                    "array dimensions not consistent")
        return []


def array_has_null(arr):
    for v in array_flatten(arr):
        if v is None:
            return True
    return False


def array_dim_lengths(arr):
    v0 = arr[0]
    if isinstance(v0, list):
        retval = [len(v0)]
        retval.extend(array_dim_lengths(v0))
    else:
        return [len(arr)]
    return retval


class array_send(object):
    def __init__(self, typeoid, bin_out_func):
        self.typeoid = typeoid
        self.bin_out_func = bin_out_func

    def __call__(self, arr, **kwargs):
        has_null = array_has_null(arr)
        dim_lengths = array_dim_lengths(arr)
        data = iii_pack(len(dim_lengths), has_null, self.typeoid)
        for i in dim_lengths:
            data += ii_pack(i, 1)
        for v in array_flatten(arr):
            if v is None:
                data += i_pack(-1)
            else:
                inner_data = self.bin_out_func(v, **kwargs)
                data += i_pack(len(inner_data))
                data += inner_data
        return data

py_types = {
    bool: {"typeoid": 16, "bin_out": boolsend},
    int: {"inspect": int_inspect},
    str: {"typeoid": 25, "bin_out": textout},
    float: {"typeoid": 701, "bin_out": float8send},
    Decimal: {"typeoid": 1700, "bin_out": numeric_send},
    Bytea: {"typeoid": 17, "bin_out": byteasend},
    datetime.datetime: {
        "typeoid": 1114, "bin_out": timestamp_send,
        "inspect": datetime_inspect},
    datetime.date: {"typeoid": 1082, "txt_out": date_out},
    datetime.time: {"typeoid": 1083, "txt_out": time_out},
    Interval: {"typeoid": 1186, "bin_out": interval_send},
    type(None): {"typeoid": -1},
    list: {"inspect": array_inspect},
}

# py type -> pg array typeoid
py_array_types = {
    float: 1022,
    bool: 1000,
    str: 1009,      # TEXT[]
    str: 1009,  # TEXT[]
    Decimal: 1231,  # NUMERIC[]
}

FC_TEXT = 0
FC_BINARY = 1

pg_types = {
    16: (FC_BINARY, boolrecv),
    17: (FC_BINARY, bytearecv),
    19: (FC_BINARY, varcharin),  # name type
    20: (FC_BINARY, int8recv),
    21: (FC_BINARY, int2recv),
    23: (FC_BINARY, int4recv),
    25: (FC_BINARY, varcharin),  # TEXT type
    26: (FC_TEXT, numeric_in),  # oid type
    700: (FC_BINARY, float4recv),
    701: (FC_BINARY, float8recv),
    829: (FC_TEXT, varcharin),  # MACADDR type
    1000: (FC_BINARY, array_recv),  # BOOL[]
    1003: (FC_BINARY, array_recv),  # NAME[]
    1005: (FC_BINARY, array_recv),  # INT2[]
    1007: (FC_BINARY, array_recv),  # INT4[]
    1009: (FC_BINARY, array_recv),  # TEXT[]
    1014: (FC_BINARY, array_recv),  # CHAR[]
    1015: (FC_BINARY, array_recv),  # VARCHAR[]
    1016: (FC_BINARY, array_recv),  # INT8[]
    1021: (FC_BINARY, array_recv),  # FLOAT4[]
    1022: (FC_BINARY, array_recv),  # FLOAT8[]
    1042: (FC_BINARY, varcharin),  # CHAR type
    1043: (FC_BINARY, varcharin),  # VARCHAR type
    1082: (FC_TEXT, date_in),
    1083: (FC_TEXT, time_in),
    1114: (FC_BINARY, timestamp_recv),
    1184: (FC_BINARY, timestamptz_recv),  # timestamp w/ tz
    1186: (FC_BINARY, interval_recv),
    1231: (FC_BINARY, array_recv),  # NUMERIC[]
    1263: (FC_BINARY, array_recv),  # cstring[]
    1700: (FC_BINARY, numeric_recv),
    2275: (FC_BINARY, varcharin),  # cstring
}
