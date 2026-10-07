"""
DJI Flight Record (.txt) parser.

Implements the reverse-engineered DJI log format (versions 1-14), based on:
  - https://djilogs.live555.com/  (format documentation)
  - lvauvillier/dji-log-parser (Rust) reference implementation

Header (prefix, 12 or 100 bytes):
    u64 detail_offset, u16 detail_length, u8 version, u8 unknown,
    u64 encrypt_magic_version, u8[80] reserved

For version >= 13 the record payloads are XOR-scrambled AND AES-256-CBC
encrypted.  The AES keys ("keychains") are stored inside the log itself as
KeyStorage records (type 56) and must be exchanged with DJI's keychain API
(https://dev.dji.com/openapi/v1/flight-records/keychains) using a developer
App Key.
"""

from __future__ import annotations

import base64
import json
import math
import struct
import urllib.request
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# CRC-64 "Jones" (as used by Redis / the crc64 crate), reflected.
# --------------------------------------------------------------------------

_CRC64_POLY = 0x95AC9329AC4BC9B5  # CRC-64/Jones, reflected form

_CRC64_TAB = []
for _i in range(256):
    _crc = _i
    for _ in range(8):
        _crc = (_crc >> 1) ^ (_CRC64_POLY if _crc & 1 else 0)
    _CRC64_TAB.append(_crc)


def crc64(crc: int, data: bytes) -> int:
    crc &= 0xFFFFFFFFFFFFFFFF
    for b in data:
        crc = (crc >> 8) ^ _CRC64_TAB[(crc ^ b) & 0xFF]
    return crc


# --------------------------------------------------------------------------
# Constants / tables
# --------------------------------------------------------------------------

XOR_MAGIC = 0x123456789ABCDEF0

FEATURE_POINT_NAMES = {
    1: "FR_Standardization_Feature_Base_1",
    2: "FR_Standardization_Feature_Vision_2",
    3: "FR_Standardization_Feature_Waypoint_3",
    4: "FR_Standardization_Feature_Agriculture_4",
    5: "FR_Standardization_Feature_AirLink_5",
    6: "FR_Standardization_Feature_AfterSales_6",
    7: "FR_Standardization_Feature_DJIFlyCustom_7",
    8: "FR_Standardization_Feature_Plaintext_8",
    9: "FR_Standardization_Feature_FlightHub_9",
    10: "FR_Standardization_Feature_Gimbal_10",
    11: "FR_Standardization_Feature_RC_11",
    12: "FR_Standardization_Feature_Camera_12",
    13: "FR_Standardization_Feature_Battery_13",
    14: "FR_Standardization_Feature_FlySafe_14",
    15: "FR_Standardization_Feature_Security_15",
}
FEATURE_POINT_IDS = {v: k for k, v in FEATURE_POINT_NAMES.items()}

FP_PLAINTEXT = 8

def feature_point_for(record_type: int, version: int) -> int:
    """Mapping of record type -> keychain feature point (from the Rust lib)."""
    if record_type == 1:            return 1   # OSD            -> Base
    if record_type == 2:            return 1   # Home           -> Base
    if record_type == 3:            return 10 if version != 13 else 1   # Gimbal
    if record_type == 4:            return 11 if version != 13 else 1   # RC
    if record_type == 5:            return 7   # Custom         -> DJIFlyCustom
    if record_type == 6:            return 1   # Deform
    if record_type == 7:            return 13 if version != 13 else 1   # CenterBattery
    if record_type == 8:            return 13 if version != 13 else 1   # SmartBattery
    if record_type == 9:            return 7   # AppTip
    if record_type == 10:           return 7   # AppWarn
    if record_type == 11:           return 11 if version != 13 else 1   # RCGPS
    if record_type == 12:           return 6   # RCDebug        -> AfterSales
    if record_type == 13:           return 1   # Recover
    if record_type == 14:           return 1   # AppGPS
    if record_type == 15:           return 1   # Firmware
    if record_type == 16:           return 6   # OFDMDebug
    if record_type == 17:           return 2   # VisionGroup
    if record_type == 18:           return 2   # VisionWarn
    if record_type == 19:           return 6   # MCParam
    if record_type == 20:           return 7   # APPOperation
    if record_type == 21:           return 4   # AGOSD
    if record_type == 22:           return 13 if version != 13 else 6   # SmartBatteryGroup
    if record_type == 24:           return 7   # AppSeriousWarn
    if record_type == 25:           return 12 if version != 13 else 1   # Camera
    if record_type == 26:           return 6   # ADSB
    if record_type == 27:           return 6   # ADSBOriginal
    if record_type == 28:           return 6 if version != 13 else 14   # FlyForbidDBuuid
    if record_type == 29:           return 11 if version != 13 else 1   # JoyStick
    if record_type == 30:           return 7   # AppLowFreqCustom
    if record_type in (31, 32, 34, 35, 36, 38, 39): return 3            # Waypoint
    if record_type == 33:           return 11 if version != 13 else 1   # VirtualStick
    if record_type == 40:           return 1   # ComponentSerial
    if record_type in (41, 43, 44, 45, 46, 47, 48): return 4            # Agriculture
    if record_type == 49:           return 5   # OFDM           -> AirLink
    if record_type == 50:           return 8   # Recover marker -> Plaintext
    if record_type == 51:           return 6 if version != 13 else 14   # FlySafeLimit
    if record_type == 52:           return 6 if version != 13 else 14   # FlySafeUnlock
    if record_type == 53:           return 6 if version != 13 else 9    # FlightHub
    if record_type == 54:           return 7   # GOBusiness
    if record_type == 55:           return 15  # Security
    if record_type == 56:           return 8   # KeyStorage     -> Plaintext
    if record_type in (58, 59, 63): return 1   # Health / IMU / FCOSD
    if record_type == 62:           return 11  # RCDisplayField
    return 8                                   # default -> Plaintext


FLIGHT_MODES = {
    0: "Manual", 1: "Atti", 2: "Atti CourseLock", 3: "Atti Hover", 4: "P-GPS",
    5: "GPS Blake", 6: "P-GPS Atti", 7: "GPS CourseLock", 8: "GPS HomeLock",
    9: "GPS HotPoint", 10: "Assisted Takeoff", 11: "Auto Takeoff",
    12: "Auto Landing", 13: "Atti Landing", 14: "Waypoint", 15: "Go Home",
    16: "ClickGo", 17: "Joystick", 18: "GPS Atti Wristband", 19: "Cinematic",
    23: "Atti Limited", 24: "Draw", 25: "Follow Me", 26: "Active Track",
    27: "TapFly", 28: "Pano", 29: "Farming", 30: "FPV", 31: "Sport",
    32: "Novice", 33: "Confirm Landing", 35: "Terrain Tracking",
    36: "Go Home (Adv)", 37: "Landing (Adv)", 38: "Tripod",
    39: "Active Track (Headlock)", 41: "Engine Start", 43: "Gentle",
}

DRONE_TYPES = {
    0: "None", 1: "Inspire 1", 2: "Phantom 3 Advanced", 3: "Phantom 3 Pro",
    4: "Phantom 3 Standard", 11: "Phantom 4", 14: "Matrice 600",
    15: "Phantom 3 4K", 16: "Mavic Pro", 17: "Inspire 2", 18: "Phantom 4 Pro",
    20: "N3", 21: "Spark", 23: "Matrice 600 Pro", 24: "Mavic Air",
    25: "Matrice 200", 27: "Phantom 4 Advanced", 28: "Matrice 210",
    29: "Phantom 3 SE", 30: "Matrice 210 RTK", 36: "Phantom 4 Pro V2",
    41: "Mavic 2", 51: "Mavic 2 Enterprise", 58: "Mavic Air 2",
    60: "Matrice 300 RTK", 63: "Mini 2", 77: "Mavic 3 Enterprise",
    84: "Mavic 3 Pro", 89: "Matrice 350 RTK", 93: "Mini 4 Pro", 94: "Avata 2",
}

PRODUCT_TYPES = {
    0: "None", 1: "Inspire 1", 2: "Phantom 3 Standard", 3: "Phantom 3 Advanced",
    4: "Phantom 3 Pro", 7: "Phantom 4", 12: "Mavic Pro", 16: "Inspire 2",
    23: "Phantom 4 Pro", 26: "Spark", 32: "Phantom 4 Advanced",
    33: "Mavic Air", 34: "Mavic 2", 35: "Phantom 4 Pro V2", 38: "Mavic Mini",
    39: "Matrice 200 V2", 40: "Mavic Air 2", 41: "Matrice 300 RTK",
    43: "Mavic Air 2S", 44: "Mini 2", 45: "Mavic 3", 46: "Mini SE",
    47: "Mavic 3 Enterprise", 48: "Mavic 3", 52: "Mini 3 Pro",
    53: "Mavic 3 Classic", 54: "Mini 3", 58: "Mini 4 Pro", 59: "Air 3",
    60: "Avata 2", 61: "Neo", 62: "Air 3S", 63: "Mavic 4 Pro",
}
# battery cell count defaults for common series (fallback 2)
def battery_cells(product_type: int) -> int:
    if product_type in (7, 23, 32, 35):      # Phantom 4 family
        return 4
    if product_type in (1, 16):              # Inspire 1 / Mavic Pro
        return 3 if product_type == 1 else 3
    return 2


def sub_field(byte: int, mask: int) -> int:
    byte &= mask
    mask >>= 0
    m = mask
    shift = 0
    while m != 0 and (m & 1) == 0:
        shift += 1
        m >>= 1
    return (byte >> shift) if m else 0


def rad2deg(x: float) -> float:
    return x * 180.0 / math.pi


# --------------------------------------------------------------------------
# Keychain API
# --------------------------------------------------------------------------

KEYCHAIN_ENDPOINT = "https://dev.dji.com/openapi/v1/flight-records/keychains"


def fetch_keychains(api_key: str, request_body: dict, endpoint: str = KEYCHAIN_ENDPOINT) -> list:
    body = json.dumps(request_body).encode()
    req = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json", "Api-Key": api_key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    result = payload.get("result", {})
    if result.get("code", -1) != 0:
        raise RuntimeError(f"DJI keychain API error: {result.get('msg')}")
    data = payload.get("data")
    if data is None:
        raise RuntimeError("DJI keychain API returned no data")
    chains = []
    for chain in data:
        d = {}
        for entry in chain:
            fp = FEATURE_POINT_IDS.get(entry.get("featurePoint"))
            if fp is None:
                continue
            d[fp] = (
                base64.b64decode(entry["aesIv"]),
                base64.b64decode(entry["aesKey"]),
            )
        chains.append(d)
    return chains


# --------------------------------------------------------------------------
# Decoding helpers
# --------------------------------------------------------------------------

def xor_key(first_byte: int, record_type: int) -> bytes:
    crc = crc64(
        (first_byte + record_type) & 0xFF,  # u8 overflowing_add, wraps mod 256
        ((XOR_MAGIC * first_byte) & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little"),
    )
    return crc.to_bytes(8, "little")


def xor_decode(data: bytes, first_byte: int, record_type: int) -> bytes:
    key = xor_key(first_byte, record_type)
    return bytes(b ^ key[i % 8] for i, b in enumerate(data))


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes):
    """Returns (plaintext, next_iv). data length must be a multiple of 16."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7
    from cryptography.hazmat.backends import default_backend

    dec = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    d = dec.decryptor()
    padded = d.update(data) + d.finalize()
    next_iv = data[-16:]
    try:
        unpadder = PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
    except Exception:
        plain = padded
    return plain, next_iv


# --------------------------------------------------------------------------
# Binary reader
# --------------------------------------------------------------------------

class R:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def take(self, n: int) -> bytes:
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self):  return self.take(1)[0]
    def i8(self):  return struct.unpack("<b", self.take(1))[0]
    def u16(self): return struct.unpack("<H", self.take(2))[0]
    def i16(self): return struct.unpack("<h", self.take(2))[0]
    def u32(self): return struct.unpack("<I", self.take(4))[0]
    def i32(self): return struct.unpack("<i", self.take(4))[0]
    def i64(self): return struct.unpack("<q", self.take(8))[0]
    def f32(self): return struct.unpack("<f", self.take(4))[0]
    def f64(self): return struct.unpack("<d", self.take(8))[0]
    def rem(self) -> int: return len(self.d) - self.p


# --------------------------------------------------------------------------
# Record payload parsers (return dicts)
# --------------------------------------------------------------------------

def parse_osd(b: bytes, log_version: int) -> dict:
    r = R(b)
    try:
        lon = rad2deg(r.f64()); lat = rad2deg(r.f64())
        alt = r.i16() / 10.0
        vx = r.i16() / 10.0; vy = r.i16() / 10.0; vz = r.i16() / 10.0
        pitch = r.i16() / 10.0; roll = r.i16() / 10.0; yaw = r.i16() / 10.0
        bp1 = r.u8(); flight_mode = sub_field(bp1, 0x7F)
        app_command = r.u8()
        bp2 = r.u8()
        ground_or_sky = sub_field(bp2, 0x06)
        is_motor_up = bool(sub_field(bp2, 0x08))
        go_home_status = sub_field(bp2, 0xE0)
        bp3 = r.u8()
        is_gps_valid = bool(sub_field(bp3, 0x80))
        bp4 = r.u8(); gps_level = sub_field(bp4, 0x3C)
        bp5 = r.u8()
        gps_num = r.u8()
        flight_action = r.u8()
        motor_start_failed = r.u8()
        bp6 = r.u8()
        battery = r.u8()
        s_wave_height = r.u8() / 10.0
        if log_version >= 14:
            battery = None          # v14: byte 40 is not battery percent
            s_wave_height = 0.0
        fly_time = r.u16() / 10.0
        motor_revolution = r.u8()
        r.take(2)
        version_c = r.u8()
        drone_type = r.u8() if r.rem() >= 1 else 0
        imu_fail = r.u8() if r.rem() >= 1 else 0
    except struct.error:
        return None
    return {
        "lon": lon, "lat": lat, "alt": alt, "vx": vx, "vy": vy, "vz": vz,
        "pitch": pitch, "roll": roll, "yaw": yaw,
        "mode": FLIGHT_MODES.get(flight_mode, f"Unknown({flight_mode})"),
        "mode_raw": flight_mode,
        "on_ground": ground_or_sky in (0, 1),
        "motor_on": is_motor_up,
        "gps_valid": is_gps_valid, "gps_num": gps_num, "gps_level": gps_level,
        "battery": battery, "vps_height": s_wave_height, "fly_time": fly_time,
        "motor_rev": motor_revolution, "version_c": version_c,
        "drone_type": DRONE_TYPES.get(drone_type, str(drone_type)),
    }


def parse_home(b: bytes, log_version: int) -> dict:
    r = R(b)
    try:
        lon = rad2deg(r.f64()); lat = rad2deg(r.f64())
        alt = r.f32() / 10.0
        bp1 = r.u8(); is_home_record = bool(sub_field(bp1, 0x01))
        bp2 = r.u8()
        is_compass_calib = bool(sub_field(bp2, 0x04))
        is_beginner = bool(sub_field(bp2, 0x08))
        is_ioc_open = bool(sub_field(bp2, 0x10))
        go_home_height = r.u16()
        ioc_angle = r.i16()
        r.take(1)  # sd state
        r.take(1)  # sd capacity
        r.take(2)  # sd left time
        current_index = r.u16()
        if log_version >= 8:
            r.take(5)
            max_height = r.f32()
        else:
            max_height = 0.0
    except struct.error:
        return None
    return {"lon": lon, "lat": lat, "alt": alt, "is_home_record": is_home_record,
            "go_home_height": go_home_height, "max_height": max_height,
            "compass_calibrating": is_compass_calib, "beginner_mode": is_beginner,
            "ioc": is_ioc_open, "record_index": current_index}


def parse_gimbal(b: bytes, log_version: int) -> dict:
    r = R(b)
    try:
        pitch = r.i16() / 10.0; roll = r.i16() / 10.0; yaw = r.i16() / 10.0
        bp1 = r.u8(); mode = sub_field(bp1, 0xC0) >> 6
        r.take(1)   # roll_adjust
        r.take(2)   # yaw angle
        r.take(1)   # bitpack2
        if log_version >= 2:
            r.take(1)
    except struct.error:
        return None
    return {"pitch": pitch, "roll": roll, "yaw": yaw,
            "mode": ["Free", "FPV", "Yaw Follow"][mode] if mode < 3 else str(mode)}


def parse_rc(b: bytes, log_version: int) -> dict:
    r = R(b)
    try:
        aileron = r.u16(); elevator = r.u16(); throttle = r.u16(); rudder = r.u16()
        gimbal = r.u16()
    except struct.error:
        return None
    return {"aileron": aileron, "elevator": elevator,
            "throttle": throttle, "rudder": rudder, "gimbal": gimbal}


def parse_rc_display(b: bytes) -> dict:
    r = R(b)
    try:
        r.take(7)
        aileron = r.u16(); elevator = r.u16(); throttle = r.u16(); rudder = r.u16()
        gimbal = r.u16()
    except struct.error:
        return None
    return {"aileron": aileron, "elevator": elevator,
            "throttle": throttle, "rudder": rudder, "gimbal": gimbal}


def parse_smart_battery(b: bytes) -> dict:
    r = R(b)
    try:
        r.take(10)  # useful/go_home/land time + batteries
        r.take(8)   # safe_fly_radius, volume_consume, status
        r.take(2)   # go_home_status + countdown
        voltage = r.u16() / 1000.0
        percent = r.u8()
    except struct.error:
        return None
    return {"voltage": voltage, "percent": percent}


def parse_smart_battery_group(b: bytes) -> dict | None:
    """Type 22 SmartBatteryGroup (v14): sub-type 1 static, 2 dynamic, 3 cells."""
    if not b:
        return None
    sub = b[0]
    try:
        if sub == 1 and len(b) >= 32:  # static
            idx = b[1]
            designed_capacity = struct.unpack_from("<I", b, 2)[0]
            loop_times = struct.unpack_from("<H", b, 6)[0]
            full_voltage = struct.unpack_from("<I", b, 8)[0] / 1000.0
            return {"group": "static", "index": idx,
                    "designed_capacity": designed_capacity,
                    "loop_times": loop_times, "full_voltage": full_voltage}
        if sub == 2 and len(b) >= 29:  # dynamic
            idx = b[1]
            voltage, current = struct.unpack_from("<ii", b, 2)
            full_cap, rem_cap = struct.unpack_from("<II", b, 10)
            temp, = struct.unpack_from("<h", b, 18)
            return {"group": "dynamic", "index": idx,
                    "voltage": voltage / 1000.0,
                    "current": abs(current) / 1000.0,
                    "full_capacity": full_cap, "remained_capacity": rem_cap,
                    "temperature": temp / 10.0,
                    "cell_count": b[20], "percent": b[21]}
        if sub == 3 and len(b) >= 2:  # single voltages
            cc = b[1]
            cells = [v / 1000.0 for v in
                     struct.unpack_from(f"<{cc}H", b, 2)] if cc else []
            return {"group": "cells", "index": 0, "cell_count": cc,
                    "cells": cells}
    except struct.error:
        return None
    return None


def parse_center_battery(b: bytes, log_version: int) -> dict:
    r = R(b)
    try:
        rel = r.u8()
        voltage = r.u16() / 1000.0
        cur_cap = r.u16(); full_cap = r.u16()
        r.take(1)   # life
        r.take(2)   # discharges
        r.take(4)   # error type
        current = r.i16() / 1000.0
        cells = [r.u16() / 1000.0 for _ in range(6)]
        r.take(4)   # serial, product date
        temp = r.u16() / 10.0 - 273.15 if log_version >= 8 else 0.0
    except struct.error:
        return None
    return {"voltage": voltage, "percent": rel, "current": current,
            "cells": cells, "temperature": temp,
            "current_capacity": cur_cap, "full_capacity": full_cap}


def parse_recover(b: bytes, log_version: int) -> dict:
    r = R(b)
    try:
        product_type = r.u8()
        platform = r.u8()
        app_ver = ".".join(str(x) for x in r.take(3))
        n = 10 if log_version <= 7 else 16
        sn = r.take(n).split(b"\x00")[0].decode("utf-8", "replace")
        name = r.take(32).split(b"\x00")[0].decode("utf-8", "replace")
        ts = r.i64()
        cam_sn = r.take(n).split(b"\x00")[0].decode("utf-8", "replace")
        rc_sn = r.take(n).split(b"\x00")[0].decode("utf-8", "replace")
        bat_buf = r.take(n)
    except struct.error:
        return None
    return {"product_type": product_type, "product": PRODUCT_TYPES.get(product_type, str(product_type)),
            "platform": platform, "app_version": app_ver, "aircraft_sn": sn,
            "aircraft_name": name, "timestamp": ts, "camera_sn": cam_sn, "rc_sn": rc_sn}


def parse_custom(b: bytes) -> dict:
    r = R(b)
    try:
        r.take(2)
        h_speed = r.f32()
        distance = r.f32()
        ts = r.i64()
    except struct.error:
        return None
    if not (1262304000 < ts / 1000 < 4102444800):
        return None
    return {"h_speed": h_speed, "distance": distance, "timestamp": ts / 1000.0}


def parse_key_storage(b: bytes) -> dict:
    r = R(b)
    try:
        fp = r.u16()
        n = r.u16()
        data = r.take(n)
    except struct.error:
        return None
    return {"feature_point": fp, "data": data}


# --------------------------------------------------------------------------
# Details (aux info) parsing
# --------------------------------------------------------------------------

def parse_details(b: bytes, log_version: int) -> dict:
    r = R(b)
    try:
        def s(n: int) -> str:
            return r.take(n).split(b"\x00")[0].decode("utf-8", "replace")
        sub_street = s(20); street = s(20); city = s(20); area = s(20)
        r.take(3)                       # favorite / new / needs upload
        record_lines = r.i32()
        r.take(4)                       # checksum
        start_time = r.i64()            # ms
        lon = r.f64(); lat = r.f64()
        total_distance = r.f32()
        total_time = r.i32() / 1000.0
        max_height = r.f32()
        max_h_speed = r.f32()
        max_v_speed = r.f32()
        capture_num = r.i32()
        video_time = r.i64()
        r.take(32)                      # moment buffer lens
        r.take(32)                      # shrink lens
        r.take(32)                      # moment lons
        r.take(32)                      # moment lats
        r.take(8)                       # analysis offset
        r.take(16)                      # md5
        takeoff_alt = r.f32()
        product_type = r.u8()
        r.take(8)                       # activation ts
        aircraft_name = s(24 if log_version <= 5 else 32)
        aircraft_sn = s(10 if log_version <= 5 else 16)
        camera_sn = s(10 if log_version <= 5 else 16)
        rc_sn = s(10 if log_version <= 5 else 16)
        battery_buf = r.take(10 if log_version <= 5 else 16)
        app_platform = r.u8()
        app_version = ".".join(str(x) for x in r.take(3))
    except (struct.error, IndexError):
        return None
    return {
        "sub_street": sub_street, "street": street, "city": city, "area": area,
        "record_lines": record_lines, "start_time": start_time / 1000.0,
        "lon": lon, "lat": lat, "total_distance": total_distance,
        "total_time": total_time, "max_height": max_height,
        "max_h_speed": max_h_speed, "max_v_speed": max_v_speed,
        "capture_num": capture_num, "video_time": video_time,
        "takeoff_altitude": takeoff_alt,
        "product_type": product_type,
        "product": PRODUCT_TYPES.get(product_type, str(product_type)),
        "aircraft_name": aircraft_name, "aircraft_sn": aircraft_sn,
        "camera_sn": camera_sn, "rc_sn": rc_sn,
        "app_platform": app_platform, "app_version": app_version,
    }


# --------------------------------------------------------------------------
# Main parser
# --------------------------------------------------------------------------

@dataclass
class ParsedLog:
    version: int
    details: dict
    aux_version: int
    department: int
    records: list = field(default_factory=list)
    keychains: list = field(default_factory=list)   # cached for offline reuse


def parse_prefix(data: bytes) -> dict:
    detail_offset = struct.unpack_from("<Q", data, 0)[0]
    detail_length = struct.unpack_from("<H", data, 8)[0]
    version = data[10]
    return {"detail_offset": detail_offset, "detail_length": detail_length,
            "version": version}


def read_aux_info(data: bytes, offset: int) -> dict:
    """Reads the first auxiliary block (encrypted details) at `offset`."""
    if data[offset] != 0:
        raise ValueError(f"expected aux info magic 0 at {offset}, got {data[offset]}")
    size = struct.unpack_from("<H", data, offset + 1)[0]
    payload = data[offset + 3: offset + 3 + size]
    seed = payload[0]
    decoded = xor_decode(payload[1:], seed, 0)
    r = R(decoded)
    version_data = r.u8()
    info_len = r.u16()
    info_data = r.take(info_len)
    sig_len = r.u16()
    sig_data = r.take(sig_len)
    return {"size": size, "version_data": version_data,
            "info_data": info_data, "signature": sig_data}


def read_aux_version(data: bytes, offset: int) -> dict:
    """Reads the second auxiliary block (auxiliary version) at `offset`."""
    if data[offset] != 1:
        raise ValueError(f"expected aux version magic 1 at {offset}, got {data[offset]}")
    size = struct.unpack_from("<H", data, offset + 1)[0]
    payload = data[offset + 3: offset + 3 + size]
    r = R(payload)
    version = r.u16()
    department = r.u8()
    return {"version": version, "department": department, "size": size}


def build_keychain_request(log: ParsedLog, data: bytes) -> dict:
    """Collects KeyStorage records and builds the API request body.

    KeyStorage records (type 56, plaintext) hold the AES ciphertext blobs.
    Each KeyStorageRecover record (type 50) marks the boundary between
    consecutive keychains.
    """
    department = log.department if log.department in (1, 2, 3, 4, 5, 6, 7, 8) else 3
    request = {"version": log.aux_version, "department": department,
               "keychainsArray": []}

    current: list = []
    for rtype, length, payload, _nxt in iter_raw_records(
            data, log.records_offset, u16_length=log.version >= 13):
        if rtype == 50:  # chain boundary
            request["keychainsArray"].append(current)
            current = []
        elif rtype == 56:  # KeyStorage
            content = xor_decode(payload[1:], payload[0], 56)
            ks = parse_key_storage(content)
            if ks:
                fp_name = FEATURE_POINT_NAMES.get(ks["feature_point"])
                if fp_name:
                    current.append({
                        "featurePoint": fp_name,
                        "aesCiphertext": base64.b64encode(ks["data"]).decode(),
                    })
        elif rtype == "jpeg":
            continue
        # other records are irrelevant for the request
    request["keychainsArray"].append(current)
    return request


def iter_raw_records(data: bytes, start: int, u16_length: bool = True):
    """Yields (type, length, payload, next_pos) for each record.

    Record layout (v7+): type(1) + length(u8 for v<=12, u16 LE for v13+)
    + `length` bytes (= seed byte + content(length-2) + lastChar)
    + 0xFF end marker.  The end marker sits *after* the counted field.
    """
    pos = start
    end = len(data)
    while pos < end - 3:
        # JPEG record?
        if data[pos:pos + 2] == b"\xff\xd8":
            idx = data.find(b"\xff\xd9", pos + 2)
            if idx == -1:
                return
            yield ("jpeg", idx + 2 - pos, data[pos:idx + 2], idx + 2)
            pos = idx + 2
            continue
        rtype = data[pos]
        if pos + 3 > end:
            return
        if u16_length:
            length = struct.unpack_from("<H", data, pos + 1)[0]
        else:
            length = data[pos + 1]
        field_end = pos + 3 + length  # end of counted field
        if length < 3 or field_end + 1 > end or data[field_end] != 0xFF:
            # invalid -> scan forward for JPEG start or stray 0xFF
            scan = pos
            found = False
            while scan < end - 1:
                if data[scan:scan + 2] == b"\xff\xd8":
                    pos = scan
                    found = True
                    break
                if data[scan] == 0xFF:
                    pos = scan + 1
                    found = True
                    break
                scan += 1
            if not found:
                return
            continue
        payload = data[pos + 3: field_end]  # seed + content + lastChar
        yield (rtype, length, payload, field_end + 1)
        pos = field_end + 1


def decode_record(data: bytes, rtype: int, payload: bytes, log_version: int,
                  keychain: dict) -> bytes | None:
    """Returns decoded record content.

    The counted field is: seed byte + content(length-2) + lastChar.
    XOR-only records expose seed + content (the lastChar is trailing filler
    the struct parsers ignore); AES records decrypt exactly (length-2) bytes.
    """
    seed = payload[0]
    if rtype == 50:  # KeyStorageRecover: raw, no decoding
        return payload
    fp = feature_point_for(rtype, log_version)
    decoded = xor_decode(payload[1:], seed, rtype)
    if log_version >= 13 and fp != FP_PLAINTEXT and fp in keychain:
        iv, key = keychain[fp]
        ciphertext = decoded[:-1]  # drop lastChar
        if len(ciphertext) >= 16 and len(ciphertext) % 16 == 0:
            try:
                plain, next_iv = aes_cbc_decrypt(key, iv, ciphertext)
                keychain[fp] = (next_iv, key)
                return plain
            except Exception:
                return decoded
    return decoded


def parse_log(data: bytes, api_key: str | None = None,
              cached_keychains: list | None = None) -> ParsedLog:
    prefix = parse_prefix(data)
    version = prefix["version"]

    # ---- auxiliary blocks / details ----------------------------------------
    if version < 12:
        aux_offset = prefix["detail_offset"]
        details_raw = data[aux_offset:aux_offset + max(prefix["detail_length"], 400)]
        details = parse_details(details_raw.ljust(400, b"\x00"), version)
        aux_version, department = version, 2
        records_offset = 12 if version < 6 else 100
        records_end = prefix["detail_offset"]
        aux = {}
    else:
        aux = read_aux_info(data, 100)
        details = parse_details(aux["info_data"], version)
        if version >= 13:
            # second aux block right after the first one
            second_offset = 100 + 3 + aux["size"]
            try:
                av = read_aux_version(data, second_offset)
                aux_version, department = av["version"], av["department"]
                records_offset = second_offset + 3 + av["size"]
            except ValueError:
                aux_version, department = version, 3
                records_offset = prefix["detail_offset"]
            if prefix["detail_offset"] not in (0, records_offset):
                records_offset = prefix["detail_offset"]
            records_end = len(data)
        else:
            aux_version, department = version, 2
            records_offset = 536 if version == 12 else 100
            records_end = prefix["detail_offset"]

    log = ParsedLog(version=version, details=details or {},
                    aux_version=aux_version, department=department)
    log.records_offset = records_offset
    log.records_end = records_end

    # ---- keychains ----------------------------------------------------------
    if version >= 13:
        if cached_keychains:
            chains = []
            for ch in cached_keychains:
                d = {}
                for entry in ch:
                    if isinstance(entry, dict):
                        fp = FEATURE_POINT_IDS.get(entry.get("featurePoint"))
                        if fp is None:
                            continue
                        d[fp] = (base64.b64decode(entry["aesIv"]),
                                 base64.b64decode(entry["aesKey"]))
                    else:  # [fp, iv_b64, key_b64]
                        fp, iv_b64, key_b64 = entry
                        d[fp] = (base64.b64decode(iv_b64),
                                 base64.b64decode(key_b64))
                if d:
                    chains.append(d)
        else:
            if not api_key:
                raise RuntimeError(
                    "Log version >= 13 requires a DJI developer API key "
                    "(or a cached keychain).")
            request = build_keychain_request(log, data)
            if not any(request["keychainsArray"]):
                raise RuntimeError("No KeyStorage records found for keychain request")
            chains = fetch_keychains(api_key, request)
        log.keychains = chains

    # ---- iterate records ----------------------------------------------------
    from collections import deque
    chain_queue = deque(chains)
    keychain = chain_queue.popleft() if chain_queue else {}

    records = []
    for rtype, length, payload, _nxt in iter_raw_records(
            data, records_offset, u16_length=version >= 13):
        if rtype == 50:  # KeyStorageRecover: switch to next keychain
            records.append({"type": 50, "content": None})
            keychain = chain_queue.popleft() if chain_queue else {}
            continue
        if rtype == "jpeg":
            records.append({"type": "jpeg", "content": payload})
            continue
        content = decode_record(data, rtype, payload, version, keychain)
        if content is None:
            continue
        parsed = None
        if rtype == 1:   parsed = parse_osd(content, version)
        elif rtype == 2: parsed = parse_home(content, version)
        elif rtype == 3: parsed = parse_gimbal(content, version)
        elif rtype == 4: parsed = parse_rc(content, version)
        elif rtype == 5: parsed = parse_custom(content)
        elif rtype == 7: parsed = parse_center_battery(content, version)
        elif rtype == 8: parsed = parse_smart_battery(content)
        elif rtype == 22: parsed = parse_smart_battery_group(content)
        elif rtype == 13: parsed = parse_recover(content, version)
        elif rtype == 62: parsed = parse_rc_display(content)
        elif rtype == 56:
            ks = parse_key_storage(content)
            parsed = ks
        records.append({"type": rtype, "content": parsed})
    log.records = records
    return log


# --------------------------------------------------------------------------
# Frame normalization (one frame per OSD record, like the Rust lib)
# --------------------------------------------------------------------------

def build_frames(log: ParsedLog) -> dict:
    details = log.details or {}
    cells = battery_cells(details.get("product_type", 0))
    last_home = {"lon": 0.0, "lat": 0.0, "alt": 0.0}
    last_gimbal = {"pitch": 0.0, "roll": 0.0, "yaw": 0.0}
    last_rc = {"aileron": 0, "elevator": 0, "throttle": 0, "rudder": 0}
    last_bat = {"voltage": 0.0, "percent": 0, "current": 0.0,
                "cells": [0.0] * cells, "temperature": 0.0,
                "current_capacity": 0, "full_capacity": 0}
    last_custom_ts = None
    recover_info = None

    frames = []
    for rec in log.records:
        t, c = rec["type"], rec["content"]
        if t == 13 and c:
            recover_info = c
        elif t == 5 and c:
            last_custom_ts = c["timestamp"]
        elif t == 2 and c:
            last_home = c
        elif t == 3 and c:
            last_gimbal = c
        elif t == 4 or t == 62:
            if c:
                last_rc = c
        elif t in (7, 8) and c:
            last_bat.update({k: c[k] for k in c})
        elif t == 22 and c:
            if (c["group"] == "dynamic" and 0 < c["voltage"] < 100
                    and 0 <= (c["percent"] or 0) <= 100):
                last_bat.update({"voltage": c["voltage"], "current": c["current"],
                                 "percent": c["percent"],
                                 "temperature": c["temperature"],
                                 "current_capacity": c["remained_capacity"],
                                 "full_capacity": c["full_capacity"]})
            elif c["group"] == "cells" and c["cells"]:
                last_bat["cells"] = c["cells"]
        elif t == 1 and c:
            hs = (c["vx"] ** 2 + c["vy"] ** 2) ** 0.5
            frames.append({
                "ts": last_custom_ts,
                "lon": c["lon"], "lat": c["lat"],
                "height": c["alt"],                      # AGL
                "altitude": c["alt"] + last_home["alt"],  # ASL (approx)
                "vps": c["vps_height"],
                "vx": c["vx"], "vy": c["vy"], "vz": c["vz"],
                "hspeed": round(hs, 3),
                "pitch": c["pitch"], "roll": c["roll"], "yaw": c["yaw"],
                "gpitch": last_gimbal["pitch"], "gyaw": last_gimbal["yaw"],
                "mode": c["mode"],
                "gps_num": c["gps_num"], "gps_level": c["gps_level"],
                "gps_valid": c["gps_valid"],
                "battery": (last_bat["percent"]
                            if 0 < (last_bat["percent"] or 0) <= 100
                            else c["battery"]),
                "voltage": last_bat["voltage"],
                "current": last_bat["current"],
                "temperature": last_bat["temperature"],
                "fly_time": c["fly_time"],
                "aileron": last_rc["aileron"], "elevator": last_rc["elevator"],
                "throttle": last_rc["throttle"], "rudder": last_rc["rudder"],
                "on_ground": c["on_ground"], "motor_on": c["motor_on"],
            })
    return {"details": details, "frames": frames, "recover": recover_info,
            "version": log.version, "keychains": log.keychains}